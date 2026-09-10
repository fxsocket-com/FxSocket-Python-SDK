"""Multi-account trading — the public v1 API (``/v1/orders``).

Accessible as ``client.orders`` on both :class:`~fxsocket.Client` and
:class:`~fxsocket.AsyncClient`. One request fans out to several of your
accounts' terminals concurrently; each leg (order, or account to close on)
may carry its own parameters and inherits the batch ``defaults`` for the
rest.

**Validation is all-or-nothing, execution is not.** The SDK mirrors the
API's own leg validation client-side so a malformed batch fails before
anything is sent, and the API does the same again server-side (``400``).
Once a batch is dispatched the legs are independent: the reply is a
``200`` with a per-leg result even if every one of them failed. There is
no atomicity across brokers and there cannot be.

Send an ``idempotency_key`` (an ``Idempotency-Key`` header) whenever a
retry is possible: a batch that times out is exactly when a client
retries, and a blind retry double-fills every leg that already landed.
Replaying the identical body with the same key returns the original reply
and sends nothing. Keys are remembered for 15 minutes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import Enum
from typing import Any, TypeVar

import pydantic

from ._http import AsyncHTTP, SyncHTTP
from .enums import CloseKind, CloseSide, SymbolMatch
from .errors import ValidationError
from .models import (
    Account,
    BatchCloseResult,
    BatchOrderResult,
    CloseDefaults,
    CloseLeg,
    OrderDefaults,
    OrderLeg,
    PrivateServerAccount,
)
from .terminal.client import _validate_order_send, coerce_operation

#: Anything ``client.orders.send()`` accepts as one leg: a full
#: :class:`OrderLeg`, a plain mapping with the same keys, or just the account
#: (object or id) when the batch ``defaults`` say everything else.
OrderLegLike = OrderLeg | Mapping[str, Any] | Account | PrivateServerAccount | str

#: Anything ``client.orders.close()`` accepts as one account entry.
CloseLegLike = CloseLeg | Mapping[str, Any] | Account | PrivateServerAccount | str

IDEMPOTENCY_KEY_MAX_LENGTH = 128

_M = TypeVar("_M", bound=pydantic.BaseModel)
_E = TypeVar("_E", bound=Enum)

_CLOSE_SELECTOR_FIELDS = ("symbol", "symbol_match", "side", "kind", "magic")


# --------------------------------------------------------------------------- #
# Payload building + client-side validation (shared by sync + async)
# --------------------------------------------------------------------------- #


def _coerce_model(model: type[_M], value: Any, where: str) -> _M:
    """Accept an instance of ``model``, a mapping, or an account (→ id)."""
    if isinstance(value, model):
        return value
    if isinstance(value, (Account, PrivateServerAccount, str)):
        value = {"account_id": value}
    if not isinstance(value, Mapping):
        raise ValidationError(
            f"{where}: expected {model.__name__}, a mapping or an account, "
            f"got {type(value).__name__}"
        )
    try:
        return model.model_validate(dict(value))
    except pydantic.ValidationError as exc:
        raise ValidationError(f"{where}: {exc}") from None


def _coerce_enum(enum: type[_E], value: Any, where: str, field: str) -> str:
    if isinstance(value, enum):
        return str(value.value)
    try:
        return str(enum(str(value).lower()).value)
    except ValueError:
        choices = ", ".join(m.value for m in enum)
        raise ValidationError(
            f"{where}: {field} must be one of {choices}, got {value!r}"
        ) from None


def _dump(model: pydantic.BaseModel) -> dict[str, Any]:
    """Wire form of an input model: snake_case, aliases, no ``None`` fields."""
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def _order_fields(model: OrderDefaults | OrderLeg, where: str) -> dict[str, Any]:
    body = _dump(model)
    if model.operation is not None:
        try:
            body["operation"] = coerce_operation(model.operation).value
        except ValidationError as exc:
            raise ValidationError(f"{where}: {exc.message}") from None
    return body


def _validate_effective_order(effective: dict[str, Any], where: str) -> None:
    """Check a leg merged with its defaults the way the terminal would."""
    for field in ("symbol", "operation", "volume"):
        if effective.get(field) in (None, ""):
            raise ValidationError(
                f"{where}: {field} is required (set it on the leg or in defaults)"
            )
    try:
        _validate_order_send(
            coerce_operation(effective["operation"]),
            volume=effective["volume"],
            price=effective.get("price"),
            stop_limit_price=effective.get("stop_limit_price"),
            stop_loss=effective.get("stop_loss"),
            take_profit=effective.get("take_profit"),
        )
    except ValidationError as exc:
        raise ValidationError(f"{where}: {exc.message}") from None
    slippage = effective.get("slippage")
    if slippage is not None and slippage < 0:
        raise ValidationError(f"{where}: slippage must be >= 0, got {slippage}")


def build_order_batch(
    orders: Iterable[OrderLegLike],
    *,
    defaults: OrderDefaults | Mapping[str, Any] | None,
    require_reachable: bool,
) -> dict[str, Any]:
    """Build and validate the ``POST /orders`` body. Raises
    :class:`~fxsocket.ValidationError` before anything is sent."""
    default_model = (
        OrderDefaults()
        if defaults is None
        else _coerce_model(OrderDefaults, defaults, "defaults")
    )
    default_body = _order_fields(default_model, "defaults")
    legs: list[dict[str, Any]] = []
    for i, raw in enumerate(orders):
        where = f"orders[{i}]"
        leg = _coerce_model(OrderLeg, raw, where)
        leg_body = _order_fields(leg, where)
        _validate_effective_order({**default_body, **leg_body}, where)
        legs.append(leg_body)
    if not legs:
        raise ValidationError("orders must contain at least one leg")
    body: dict[str, Any] = {"orders": legs}
    if default_body:
        body["defaults"] = default_body
    if require_reachable:
        body["require_reachable"] = True
    return body


def _close_fields(model: CloseDefaults | CloseLeg, where: str) -> dict[str, Any]:
    body = _dump(model)
    if model.symbol_match is not None:
        body["symbol_match"] = _coerce_enum(
            SymbolMatch, model.symbol_match, where, "symbol_match"
        )
    if model.side is not None:
        body["side"] = _coerce_enum(CloseSide, model.side, where, "side")
    if model.kind is not None:
        body["kind"] = _coerce_enum(CloseKind, model.kind, where, "kind")
    if model.volume is not None and model.volume <= 0:
        raise ValidationError(f"{where}: volume must be > 0, got {model.volume}")
    if model.slippage is not None and model.slippage < 0:
        raise ValidationError(f"{where}: slippage must be >= 0, got {model.slippage}")
    return body


def _validate_close_leg(
    leg: CloseLeg, leg_body: dict[str, Any], default_body: dict[str, Any], where: str
) -> None:
    if leg.tickets is not None:
        if not leg.tickets:
            raise ValidationError(f"{where}: tickets must not be empty")
        if any(t < 1 for t in leg.tickets):
            raise ValidationError(f"{where}: tickets must be >= 1")
        mixed = [f for f in _CLOSE_SELECTOR_FIELDS if f in leg_body]
        if mixed:
            raise ValidationError(
                f"{where}: tickets can't be combined with selector fields "
                f"({', '.join(mixed)}) — use one or the other"
            )
        return
    if not ({**default_body, **leg_body}).get("symbol"):
        raise ValidationError(
            f"{where}: symbol is required (set it on the leg or in defaults; "
            'pass "*" to close every symbol)'
        )


def build_close_batch(
    accounts: Iterable[CloseLegLike],
    *,
    defaults: CloseDefaults | Mapping[str, Any] | None,
    require_reachable: bool,
) -> dict[str, Any]:
    """Build and validate the ``POST /orders/close`` body. Raises
    :class:`~fxsocket.ValidationError` before anything is sent."""
    default_model = (
        CloseDefaults()
        if defaults is None
        else _coerce_model(CloseDefaults, defaults, "defaults")
    )
    default_body = _close_fields(default_model, "defaults")
    legs: list[dict[str, Any]] = []
    for i, raw in enumerate(accounts):
        where = f"accounts[{i}]"
        leg = _coerce_model(CloseLeg, raw, where)
        leg_body = _close_fields(leg, where)
        _validate_close_leg(leg, leg_body, default_body, where)
        legs.append(leg_body)
    if not legs:
        raise ValidationError("accounts must contain at least one entry")
    body: dict[str, Any] = {"accounts": legs}
    if default_body:
        body["defaults"] = default_body
    if require_reachable:
        body["require_reachable"] = True
    return body


def idempotency_headers(key: str | None) -> dict[str, str] | None:
    if key is None:
        return None
    if not key or len(key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise ValidationError(
            "idempotency_key must be 1–"
            f"{IDEMPOTENCY_KEY_MAX_LENGTH} characters, got {len(key)}"
        )
    return {"Idempotency-Key": key}


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #


class Orders:
    """Synchronous multi-account trading (``client.orders``).

    Requires the full ``fxs_live_…`` key — a read-only ``fxs_ro_…`` key
    raises :class:`~fxsocket.ForbiddenError`.
    """

    def __init__(self, http: SyncHTTP) -> None:
        self._http = http

    def send(
        self,
        orders: Iterable[OrderLegLike],
        *,
        defaults: OrderDefaults | Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        require_reachable: bool = False,
    ) -> BatchOrderResult:
        """Send one order to several accounts at once (``POST /v1/orders``).

        ``orders`` is one entry per leg: an :class:`~fxsocket.OrderLeg`, a
        mapping with the same keys, or just an account (object or id) when
        ``defaults`` already say everything else. Each leg inherits
        ``defaults`` and may override any of it — including with a falsy
        value, so ``slippage=0`` really means zero.

        The reply's ``results`` are positional (``zip(orders, ...)``);
        a leg's ``status`` is only trustworthy as ``"filled"`` — a
        ``"timeout"`` leg *may* have executed. Symbols are per broker
        (``EURUSD`` / ``EURUSD.sd`` / ``EURUSDm``), so one ``defaults``
        symbol across mixed brokers will partly fail by design — that is
        what a leg-level ``symbol`` is for.

        ``idempotency_key`` (at most 128 characters) makes a retry safe:
        replaying the identical batch with the same key returns the stored
        reply (``idempotent_replay=True``) and sends nothing. A different
        body under the same key, a key still in flight, or a moment when
        the guarantee can't be honoured raise
        :class:`~fxsocket.IdempotencyError` — nothing is sent either way.

        ``require_reachable=True`` rejects the whole batch up front
        (:class:`~fxsocket.ValidationError`, ``unreachable_account``) if any
        account has no terminal, instead of reporting that leg as
        ``"unreachable"``. A pre-dispatch check only.
        """
        body = build_order_batch(
            orders, defaults=defaults, require_reachable=require_reachable
        )
        data = self._http.request(
            "POST", "/orders", json=body, headers=idempotency_headers(idempotency_key)
        )
        return BatchOrderResult.model_validate(data)

    def close(
        self,
        accounts: Iterable[CloseLegLike],
        *,
        defaults: CloseDefaults | Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        require_reachable: bool = False,
    ) -> BatchCloseResult:
        """Close orders across several accounts at once
        (``POST /v1/orders/close``).

        Closes by **selector**, not by ticket: each account is matched
        against ``symbol`` (plus optional ``side``, ``kind``, ``magic``),
        the backend resolves the tickets from that account's open orders
        and closes them. ``accounts`` takes :class:`~fxsocket.CloseLeg`
        entries, mappings, or bare accounts when ``defaults`` carry the
        selector. Pass ``tickets`` on a leg instead when you already have
        them — never both.

        ``symbol`` is required and ``"*"`` must be typed to mean every
        symbol. ``symbol_match="base"`` lets one selector reach
        ``EURUSD``, ``EURUSD.sd`` and ``EURUSDm`` across brokers. ``kind``
        defaults to ``"position"``, so a routine close does not also delete
        resting pending orders — pass ``"pending"`` or ``"any"``
        deliberately. ``volume`` makes it a partial close.

        The reply carries per-account and per-ticket outcomes;
        ``"nothing_matched"`` is a normal answer, not an error.
        ``idempotency_key`` works as in :meth:`send` and matters most for
        partial closes, where a blind retry genuinely over-closes.
        """
        body = build_close_batch(
            accounts, defaults=defaults, require_reachable=require_reachable
        )
        data = self._http.request(
            "POST",
            "/orders/close",
            json=body,
            headers=idempotency_headers(idempotency_key),
        )
        return BatchCloseResult.model_validate(data)


# --------------------------------------------------------------------------- #
# Async
# --------------------------------------------------------------------------- #


class AsyncOrders:
    """Asynchronous mirror of :class:`Orders`."""

    def __init__(self, http: AsyncHTTP) -> None:
        self._http = http

    async def send(
        self,
        orders: Iterable[OrderLegLike],
        *,
        defaults: OrderDefaults | Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        require_reachable: bool = False,
    ) -> BatchOrderResult:
        """Async mirror of :meth:`Orders.send`."""
        body = build_order_batch(
            orders, defaults=defaults, require_reachable=require_reachable
        )
        data = await self._http.request(
            "POST", "/orders", json=body, headers=idempotency_headers(idempotency_key)
        )
        return BatchOrderResult.model_validate(data)

    async def close(
        self,
        accounts: Iterable[CloseLegLike],
        *,
        defaults: CloseDefaults | Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        require_reachable: bool = False,
    ) -> BatchCloseResult:
        """Async mirror of :meth:`Orders.close`."""
        body = build_close_batch(
            accounts, defaults=defaults, require_reachable=require_reachable
        )
        data = await self._http.request(
            "POST",
            "/orders/close",
            json=body,
            headers=idempotency_headers(idempotency_key),
        )
        return BatchCloseResult.model_validate(data)
