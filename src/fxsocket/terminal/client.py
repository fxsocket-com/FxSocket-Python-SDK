"""Per-account terminal REST client (MT4, MT5 and cTrader).

One :class:`TerminalClient` (sync) / :class:`AsyncTerminalClient` (async) is
bound to a single account's terminal endpoint (``Account.rest_url``). The two
are method-for-method mirrors; only the awaiting differs.

MT4 and MT5 share one interface. Both terminal APIs accept the same nine
timeframes (see :class:`~fxsocket.Timeframe`); a timeframe outside that set
raises :class:`~fxsocket.ValidationError` before the request goes out. The one
platform gate is stop-limit: MT4 has no stop-limit order type, so a
``*StopLimit`` operation on ``order_send`` or a ``stop_limit_price`` on
``order_modify`` raises :class:`~fxsocket.UnsupportedOnPlatformError` on MT4.

Order inputs are validated client-side to fail fast and, above all, safely:
the same constraints the terminal enforces (volume > 0, an entry price for
pending orders, a stop-limit price for ``*StopLimit``) plus a guard against the
silent ``order_modify`` footgun where a literal ``0.0`` stop-loss / take-profit
*removes* the protection — pass ``clear_stop_loss`` / ``clear_take_profit`` to
do that explicitly, ``None`` (the default) keeps the current value.

Two opt-in trade flags need bridge MT5 0.18.0 / MT4 0.16.0
(``status().bridge.version``); older bridges ignore them:

* ``order_close(ticket, pending_only=True)`` only deletes a pending order. If
  it filled in the meantime the position stays open and
  :class:`~fxsocket.NotPendingError` is raised. An older bridge would close
  the position instead, so check the version before relying on it.
* ``order_send(..., execution="Manual")`` places a market order through the
  account's own order dialog, for brokers that refuse expert trading
  (``account_info().trade_expert`` is ``False``). ``Buy`` / ``Sell`` only,
  magic 0; not on cTrader. :class:`~fxsocket.ManualNotSentError` means
  nothing was sent.

cTrader accounts (bridge 0.1.6) add a few things MT4 and MT5 do not have:

* ``trailing_stop`` and ``guaranteed_stop_loss`` on ``order_send`` and
  ``order_modify``, cTrader's own stop loss kinds. Both need a stop loss.
* ``client_order_id`` on ``order_modify`` and ``order_close`` too. On
  cTrader the ids of send, modify and close are one set.
* :meth:`TerminalClient.journal`, the account's connection events and
  trade requests.

The client knows the account's platform, so on MT4 and MT5 these raise
:class:`~fxsocket.UnsupportedOnPlatformError` before anything is sent (the
MT terminals would silently ignore the fields). ``trailing_stop=False`` and
``guaranteed_stop_loss=False`` are not sent there, since every MT stop loss
is already a fixed, normal one.

No request is auto-retried — in particular order send/modify/close must never
replay, to avoid duplicate fills. To retry an ``order_send`` safely yourself
(after a :class:`~fxsocket.TradeOutcomeUnknownError`, say), give it a
``client_order_id``: the terminal trades an id once and answers every repeat
with the first outcome.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone
from typing import Any

import httpx

from .._http import AsyncHTTP, SyncHTTP, auth_headers
from ..enums import (
    PENDING_OPERATIONS,
    STOP_LIMIT_OPERATIONS,
    Execution,
    JournalKind,
    OrderOperation,
    Platform,
    Timeframe,
)
from ..errors import (
    UnsupportedOnPlatformError,
    ValidationError,
    error_from_response,
)
from ..models import (
    AccountInfo,
    AccountSummary,
    Candle,
    CloseAllSummary,
    Health,
    HealthChecks,
    HistoryTrade,
    JournalEntry,
    MarginCalc,
    OpenedOrder,
    OrderResult,
    PendingOrder,
    PositionTrade,
    ProfitCalc,
    Quote,
    ServerTimezone,
    ServerTiming,
    SymbolInfo,
    SymbolInfoBatch,
    SymbolSelectResult,
)

# --------------------------------------------------------------------------- #
# Coercion / validation helpers (shared by sync + async)
# --------------------------------------------------------------------------- #

_OP_BY_NORM = {op.value.lower(): op for op in OrderOperation}

_TF_HUMAN = {
    "1min": Timeframe.M1,
    "5min": Timeframe.M5,
    "15min": Timeframe.M15,
    "30min": Timeframe.M30,
    "1h": Timeframe.H1,
    "4h": Timeframe.H4,
    "1d": Timeframe.D1,
    "1w": Timeframe.W1,
    "1month": Timeframe.MN1,
    "1mn": Timeframe.MN1,
}


def coerce_operation(value: OrderOperation | str) -> OrderOperation:
    """Normalize an order operation (case/separator-insensitive)."""
    if isinstance(value, OrderOperation):
        return value
    key = "".join(ch for ch in str(value).lower() if ch.isalnum())
    op = _OP_BY_NORM.get(key)
    if op is None:
        raise ValidationError(f"Unknown order operation: {value!r}")
    return op


def coerce_timeframe(value: Timeframe | str) -> Timeframe:
    """Normalize a timeframe label (``M5``, ``5min``, ``1h`` … )."""
    if isinstance(value, Timeframe):
        return value
    raw = str(value).strip()
    try:
        return Timeframe(raw.upper())
    except ValueError:
        pass
    tf = _TF_HUMAN.get(raw.lower())
    if tf is None:
        supported = ", ".join(t.value for t in Timeframe)
        raise ValidationError(
            f"Unknown timeframe: {value!r}; the terminal API accepts {supported}"
        )
    return tf


def _require_positive_volume(volume: float) -> None:
    if volume <= 0:
        raise ValidationError(f"volume must be > 0, got {volume}")


def _validate_order_send(
    op: OrderOperation,
    *,
    volume: float,
    price: float | None,
    stop_limit_price: float | None,
    stop_loss: float | None,
    take_profit: float | None,
) -> None:
    """Mirror the terminal's own ``/OrderSend`` validation, fail-fast."""
    _require_positive_volume(volume)
    if op in PENDING_OPERATIONS and (price is None or price <= 0):
        raise ValidationError(f"price is required for pending orders ({op.value})")
    if op in STOP_LIMIT_OPERATIONS and (
        stop_limit_price is None or stop_limit_price <= 0
    ):
        raise ValidationError(f"stop_limit_price is required for {op.value} orders")
    # On send, 0.0 / absent legitimately means "no SL/TP"; only negatives are wrong.
    if stop_loss is not None and stop_loss < 0:
        raise ValidationError(f"stop_loss must be >= 0, got {stop_loss}")
    if take_profit is not None and take_profit < 0:
        raise ValidationError(f"take_profit must be >= 0, got {take_profit}")


def _check_stop_kinds_need_stop_loss(
    trailing_stop: bool | None,
    guaranteed_stop_loss: bool | None,
    stop_loss: float | None,
) -> None:
    """``order_send``: a trailing or guaranteed stop loss needs a stop loss
    (the terminal answers 400 otherwise)."""
    if (trailing_stop or guaranteed_stop_loss) and not (stop_loss and stop_loss > 0):
        raise ValidationError("trailing_stop and guaranteed_stop_loss need a stop_loss")


def _resolve_modify_sl_tp(
    *,
    stop_loss: float | None,
    take_profit: float | None,
    clear_stop_loss: bool,
    clear_take_profit: bool,
    price: float | None,
    stop_limit_price: float | None,
) -> tuple[float | None, float | None]:
    """Validate an ``order_modify`` and resolve SL/TP to wire values.

    Returns ``(sl, tp)`` where ``None`` means "omit → keep current" and ``0.0``
    means "clear". A bare ``stop_loss=0.0`` is rejected because, sent verbatim,
    the terminal would silently *remove* the protection — removal must be
    explicit via ``clear_stop_loss``.
    """
    if clear_stop_loss and stop_loss is not None:
        raise ValidationError(
            "pass either stop_loss=<price> or clear_stop_loss=True, not both"
        )
    if clear_take_profit and take_profit is not None:
        raise ValidationError(
            "pass either take_profit=<price> or clear_take_profit=True, not both"
        )
    if stop_loss is not None and stop_loss <= 0:
        raise ValidationError(
            "stop_loss must be > 0 in order_modify; pass clear_stop_loss=True to "
            "remove it, or leave it None to keep the current value"
        )
    if take_profit is not None and take_profit <= 0:
        raise ValidationError(
            "take_profit must be > 0 in order_modify; pass clear_take_profit=True "
            "to remove it, or leave it None to keep the current value"
        )
    if price is not None and price <= 0:
        raise ValidationError(f"price must be > 0, got {price}")
    if stop_limit_price is not None and stop_limit_price <= 0:
        raise ValidationError(f"stop_limit_price must be > 0, got {stop_limit_price}")
    sl = 0.0 if clear_stop_loss else stop_loss
    tp = 0.0 if clear_take_profit else take_profit
    return sl, tp


def _fmt_time(value: str | datetime | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _fmt_utc(value: str | datetime | date | None) -> str | None:
    """Format ``expiration_utc`` as ``YYYY-MM-DDTHH:MM:SSZ``.

    An aware datetime is converted to UTC, a naive one is taken to be UTC
    already, a date means midnight UTC; a string is sent unchanged.
    """
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        dt = value.astimezone(timezone.utc)
    else:
        dt = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_expiration(expiration: object, expiration_utc: object) -> None:
    if expiration is not None and expiration_utc is not None:
        raise ValidationError(
            "pass either expiration (broker server time) or expiration_utc, not both"
        )


def _check_client_order_id(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValidationError("client_order_id must be a string of 1-64 characters")


_MAX_SYMBOLS = 100


def _check_symbols(symbols: Sequence[str]) -> list[str]:
    """Validate a symbol list for ``symbol_select`` / ``symbol_infos``."""
    if isinstance(symbols, str) or not isinstance(symbols, Sequence):
        raise ValidationError("symbols must be a list of symbol names")
    names = list(symbols)
    if not names:
        raise ValidationError("symbols must not be empty")
    if len(names) > _MAX_SYMBOLS:
        raise ValidationError(
            f"at most {_MAX_SYMBOLS} symbols per call, got {len(names)}"
        )
    for name in names:
        if not isinstance(name, str) or not name.strip() or "," in name:
            raise ValidationError(f"invalid symbol name: {name!r}")
    return names


#: Why a platform has no MT5-style stop-limit order.
_NO_STOP_LIMIT = {
    Platform.MT4: "MT4 has no stop-limit order type",
    Platform.CTRADER: (
        "cTrader's stop-limit fills within a range beyond the stop instead "
        "of resting as a limit"
    ),
}


def _guard_stop_limit_send(platform: Platform, op: OrderOperation) -> None:
    reason = _NO_STOP_LIMIT.get(platform)
    if reason and op in STOP_LIMIT_OPERATIONS:
        raise UnsupportedOnPlatformError(
            f"{op.value} orders are not supported on {_label(platform)} ({reason})"
        )


def _guard_stop_limit_modify(
    platform: Platform, stop_limit_price: float | None
) -> None:
    reason = _NO_STOP_LIMIT.get(platform)
    if reason and stop_limit_price is not None:
        raise UnsupportedOnPlatformError(
            f"stop_limit_price is not supported on {_label(platform)} ({reason})"
        )


def coerce_execution(value: Execution | str | None) -> Execution | None:
    if value is None or isinstance(value, Execution):
        return value
    norm = str(value).strip().lower()
    for ex in Execution:
        if ex.value.lower() == norm:
            return ex
    raise ValidationError(f"Unknown execution: {value!r} (EA or Manual)")


def _validate_manual(
    platform: Platform,
    op: OrderOperation,
    *,
    magic: int | None,
    stop_limit_price: float | None,
    expiration: object,
    expiration_utc: object,
) -> None:
    """Mirror the terminal's checks for ``execution="Manual"``, fail-fast."""
    if platform is Platform.CTRADER:
        raise UnsupportedOnPlatformError("execution=Manual is not supported on cTrader")
    if op not in (OrderOperation.BUY, OrderOperation.SELL):
        raise ValidationError("execution=Manual places market orders only (Buy, Sell)")
    if magic:
        raise ValidationError("execution=Manual orders always have magic 0")
    if any(v is not None for v in (stop_limit_price, expiration, expiration_utc)):
        raise ValidationError("execution=Manual takes no stop_limit_price, expiration")


def _label(platform: Platform) -> str:
    return "cTrader" if platform is Platform.CTRADER else platform.value.upper()


def _stop_kinds(
    platform: Platform,
    trailing_stop: bool | None,
    guaranteed_stop_loss: bool | None,
) -> tuple[bool | None, bool | None]:
    """cTrader's stop loss kinds, as they go on the wire.

    On MT4 / MT5 ``True`` is refused (the platform has no such stop loss)
    and ``False`` is dropped: every MT stop loss is fixed and not guaranteed
    already, and the MT terminals would ignore the field.
    """
    if platform is Platform.CTRADER:
        return trailing_stop, guaranteed_stop_loss
    for name, value in (
        ("trailing_stop", trailing_stop),
        ("guaranteed_stop_loss", guaranteed_stop_loss),
    ):
        if value:
            raise UnsupportedOnPlatformError(
                f"{name} is only supported on cTrader, not on {_label(platform)}"
            )
    return None, None


def _ctrader_client_order_id(
    platform: Platform, client_order_id: str | None, call: str
) -> str | None:
    """``client_order_id`` on ``order_modify`` / ``order_close``: cTrader
    only (the MT terminals would ignore it, so a retry would not be safe)."""
    if client_order_id is None:
        return None
    if platform is not Platform.CTRADER:
        raise UnsupportedOnPlatformError(
            f"client_order_id on {call} is only supported on cTrader, "
            f"not on {_label(platform)}"
        )
    _check_client_order_id(client_order_id)
    return client_order_id


_MAX_JOURNAL = 1000


def _journal_params(
    platform: Platform,
    limit: int | None,
    since: str | datetime | date | None,
    kind: JournalKind | str | None,
) -> dict[str, Any]:
    if platform is not Platform.CTRADER:
        raise UnsupportedOnPlatformError(
            f"journal() is only served by cTrader terminals, not by {_label(platform)}"
        )
    if limit is not None and (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= _MAX_JOURNAL
    ):
        raise ValidationError(f"limit must be 1-{_MAX_JOURNAL}, got {limit!r}")
    kind_value: str | None = None
    if kind is not None:
        try:
            kind_value = JournalKind(str(getattr(kind, "value", kind)).lower()).value
        except ValueError:
            raise ValidationError(
                f"Unknown journal kind: {kind!r} (connection or trade)"
            ) from None
    return _clean({"limit": limit, "since": _fmt_since(since), "kind": kind_value})


def _fmt_since(value: str | datetime | date | None) -> str | None:
    """Format a journal ``since`` as UTC with milliseconds
    (``2026-11-22T21:00:00.000Z``).

    An aware datetime is converted to UTC, a naive one is taken to be UTC
    already, a date means midnight UTC; a string is sent unchanged.
    """
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        dt = value.astimezone(timezone.utc)
    else:
        dt = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _order_result(body: Any, headers: Any) -> OrderResult:
    """Build an :class:`OrderResult` from a trade reply and its headers."""
    result = OrderResult.model_validate(body)
    replayed = headers.get("Idempotent-Replayed", "").strip().lower() == "true"
    timing = ServerTiming.from_header(headers.get("Server-Timing"))
    return result.model_copy(
        update={"idempotent_replayed": replayed, "server_timing": timing}
    )


def _clean(params: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None (so omitted fields aren't sent)."""
    return {k: v for k, v in params.items() if v is not None}


def _order_send_body(
    *,
    symbol: str,
    operation: OrderOperation,
    volume: float,
    price: float | None,
    slippage: int | None,
    stop_loss: float | None,
    take_profit: float | None,
    stop_limit_price: float | None,
    expiration: str | datetime | date | None,
    expiration_utc: str | datetime | date | None,
    comment: str | None,
    magic: int | None,
    client_order_id: str | None,
    execution: Execution | None = None,
    trailing_stop: bool | None = None,
    guaranteed_stop_loss: bool | None = None,
) -> dict[str, Any]:
    return _clean(
        {
            "symbol": symbol,
            "operation": operation.value,
            "volume": volume,
            "price": price,
            "slippage": slippage,
            "stopLoss": stop_loss,
            "takeProfit": take_profit,
            "stopLimitPrice": stop_limit_price,
            "expiration": _fmt_time(expiration),
            "expirationUtc": _fmt_utc(expiration_utc),
            "comment": comment,
            "expertId": magic,
            "clientOrderId": client_order_id,
            "execution": execution.value if execution else None,
            "trailingStop": trailing_stop,
            "guaranteedStopLoss": guaranteed_stop_loss,
        }
    )


def _order_close_body(
    ticket: int,
    volume: float | None,
    slippage: int | None,
    pending_only: bool,
    client_order_id: str | None = None,
) -> dict[str, Any]:
    body = _clean(
        {
            "ticket": ticket,
            "volume": volume,
            "slippage": slippage,
            "clientOrderId": client_order_id,
        }
    )
    if pending_only:
        body["pendingOnly"] = True
    return body


def _close_all_body(
    *, symbol: str | None, magic: int | None, delete_pending: bool
) -> dict[str, Any]:
    # magic=0 is a real filter (manually-opened orders) — only None is omitted.
    # An empty dict is a valid body: it means "close everything".
    return _clean(
        {
            "symbol": symbol,
            "magic": magic,
            "deletePending": True if delete_pending else None,
        }
    )


def _order_modify_body(
    *,
    ticket: int,
    stop_loss: float | None,
    take_profit: float | None,
    price: float | None,
    stop_limit_price: float | None,
    expiration: str | datetime | date | None,
    expiration_utc: str | datetime | date | None,
    trailing_stop: bool | None = None,
    guaranteed_stop_loss: bool | None = None,
    client_order_id: str | None = None,
) -> dict[str, Any]:
    return _clean(
        {
            "ticket": ticket,
            "stopLoss": stop_loss,
            "takeProfit": take_profit,
            "price": price,
            "stopLimitPrice": stop_limit_price,
            "expiration": _fmt_time(expiration),
            "expirationUtc": _fmt_utc(expiration_utc),
            "trailingStop": trailing_stop,
            "guaranteedStopLoss": guaranteed_stop_loss,
            "clientOrderId": client_order_id,
        }
    )


def _prepare_order_send(
    platform: Platform,
    *,
    symbol: str,
    operation: OrderOperation | str,
    volume: float,
    price: float | None,
    slippage: int | None,
    stop_loss: float | None,
    take_profit: float | None,
    stop_limit_price: float | None,
    expiration: str | datetime | date | None,
    comment: str | None,
    magic: int | None,
    expiration_utc: str | datetime | date | None,
    client_order_id: str | None,
    execution: Execution | str | None,
    trailing_stop: bool | None,
    guaranteed_stop_loss: bool | None,
) -> dict[str, Any]:
    """Validate an ``order_send`` and build its body (sync + async)."""
    op = coerce_operation(operation)
    _guard_stop_limit_send(platform, op)
    ex = coerce_execution(execution)
    if ex is Execution.MANUAL:
        _validate_manual(
            platform,
            op,
            magic=magic,
            stop_limit_price=stop_limit_price,
            expiration=expiration,
            expiration_utc=expiration_utc,
        )
    trailing, guaranteed = _stop_kinds(platform, trailing_stop, guaranteed_stop_loss)
    _validate_order_send(
        op,
        volume=volume,
        price=price,
        stop_limit_price=stop_limit_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )
    _check_stop_kinds_need_stop_loss(trailing, guaranteed, stop_loss)
    _check_expiration(expiration, expiration_utc)
    _check_client_order_id(client_order_id)
    return _order_send_body(
        symbol=symbol,
        operation=op,
        volume=volume,
        price=price,
        slippage=slippage,
        stop_loss=stop_loss,
        take_profit=take_profit,
        stop_limit_price=stop_limit_price,
        expiration=expiration,
        expiration_utc=expiration_utc,
        comment=comment,
        magic=magic,
        client_order_id=client_order_id,
        execution=ex,
        trailing_stop=trailing,
        guaranteed_stop_loss=guaranteed,
    )


def _prepare_order_modify(
    platform: Platform,
    *,
    ticket: int,
    stop_loss: float | None,
    take_profit: float | None,
    price: float | None,
    stop_limit_price: float | None,
    expiration: str | datetime | date | None,
    clear_stop_loss: bool,
    clear_take_profit: bool,
    expiration_utc: str | datetime | date | None,
    trailing_stop: bool | None,
    guaranteed_stop_loss: bool | None,
    client_order_id: str | None,
) -> dict[str, Any]:
    """Validate an ``order_modify`` and build its body (sync + async)."""
    _guard_stop_limit_modify(platform, stop_limit_price)
    trailing, guaranteed = _stop_kinds(platform, trailing_stop, guaranteed_stop_loss)
    coid = _ctrader_client_order_id(platform, client_order_id, "order_modify")
    sl, tp = _resolve_modify_sl_tp(
        stop_loss=stop_loss,
        take_profit=take_profit,
        clear_stop_loss=clear_stop_loss,
        clear_take_profit=clear_take_profit,
        price=price,
        stop_limit_price=stop_limit_price,
    )
    if clear_stop_loss and (trailing or guaranteed):
        raise ValidationError(
            "trailing_stop and guaranteed_stop_loss need a stop loss; "
            "clear_stop_loss removes it"
        )
    _check_expiration(expiration, expiration_utc)
    return _order_modify_body(
        ticket=ticket,
        stop_loss=sl,
        take_profit=tp,
        price=price,
        stop_limit_price=stop_limit_price,
        expiration=expiration,
        expiration_utc=expiration_utc,
        trailing_stop=trailing,
        guaranteed_stop_loss=guaranteed,
        client_order_id=coid,
    )


def _prepare_order_close(
    platform: Platform,
    *,
    ticket: int,
    volume: float | None,
    slippage: int | None,
    pending_only: bool,
    client_order_id: str | None,
) -> dict[str, Any]:
    """Validate an ``order_close`` and build its body (sync + async)."""
    if volume is not None and volume < 0:
        raise ValidationError(f"volume must be >= 0, got {volume}")
    coid = _ctrader_client_order_id(platform, client_order_id, "order_close")
    return _order_close_body(ticket, volume, slippage, pending_only, coid)


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #


class TerminalClient:
    """Synchronous terminal client bound to one account's REST endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        platform: Platform | str,
        verify: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self.platform = Platform(platform)
        self.base_url = base_url
        self._client = httpx.Client(
            base_url=base_url,
            headers=auth_headers(api_key),
            timeout=timeout,
            verify=verify,
        )
        self._http = SyncHTTP(self._client)

    # -- account state ----------------------------------------------------- #

    def account_summary(self) -> AccountSummary:
        return AccountSummary.model_validate(
            self._http.request("GET", "/AccountSummary")
        )

    def account_info(self) -> AccountInfo:
        return AccountInfo.model_validate(self._http.request("GET", "/AccountInfo"))

    def opened_orders(self) -> list[OpenedOrder]:
        rows = self._http.request("GET", "/OpenedOrders")
        return [OpenedOrder.model_validate(r) for r in rows]

    def pending_orders(self) -> list[PendingOrder]:
        """The active pending orders (``GET /PendingOrders``), with initial
        volume, stop-limit price and expiry (bridges MT5 0.17.0 / MT4 0.15.0
        on)."""
        rows = self._http.request("GET", "/PendingOrders")
        return [PendingOrder.model_validate(r) for r in rows]

    def order_history(
        self,
        from_: str | datetime | date | None = None,
        to: str | datetime | date | None = None,
    ) -> list[HistoryTrade]:
        params = _clean({"from": _fmt_time(from_), "to": _fmt_time(to)})
        rows = self._http.request("GET", "/OrderHistory", params=params)
        return [HistoryTrade.model_validate(r) for r in rows]

    def position_history(
        self,
        from_: str | datetime | date | None = None,
        to: str | datetime | date | None = None,
    ) -> list[PositionTrade]:
        params = _clean({"from": _fmt_time(from_), "to": _fmt_time(to)})
        rows = self._http.request("GET", "/PositionHistory", params=params)
        return [PositionTrade.model_validate(r) for r in rows]

    def server_timezone(self) -> ServerTimezone:
        return ServerTimezone.model_validate(
            self._http.request("GET", "/ServerTimezone")
        )

    def journal(
        self,
        *,
        limit: int | None = None,
        since: str | datetime | date | None = None,
        kind: JournalKind | str | None = None,
    ) -> list[JournalEntry]:
        """The account's journal, newest first (``GET /Journal``). cTrader
        only (bridge 0.1.6): on MT4 / MT5 this raises
        :class:`~fxsocket.UnsupportedOnPlatformError`.

        Connection entries say when the account signed in at cTrader, became
        ready, lost and regained its connection, and when its token was
        refreshed or refused. Trade entries hold every send, modify, close
        and close-all with its request, outcome, duration,
        ``client_order_id``, ``User-Agent`` and client address.

        ``limit`` is 1-1000 (the terminal's default is 100). ``since`` keeps
        entries at or after a moment in UTC (a naive datetime is taken as
        UTC). ``kind`` is ``connection`` or ``trade`` (see
        :class:`~fxsocket.JournalKind`). The journal lives in the bridge's
        memory: the newest 1000 entries of at most 7 days, emptied when the
        bridge restarts. Use :meth:`order_history` for the full record.
        """
        params = _journal_params(self.platform, limit, since, kind)
        body = self._http.request("GET", "/Journal", params=params)
        return [JournalEntry.model_validate(r) for r in (body or {}).get("entries", [])]

    # -- market data ------------------------------------------------------- #

    def symbols(self) -> list[str]:
        return list(self._http.request("GET", "/symbols"))

    def quote(self, symbol: str) -> Quote:
        return Quote.model_validate(
            self._http.request("GET", "/getQuote", params={"symbol": symbol})
        )

    def symbol_info(self, symbol: str) -> SymbolInfo:
        return SymbolInfo.model_validate(
            self._http.request("GET", "/SymbolInfo", params={"symbol": symbol})
        )

    def symbol_infos(self, symbols: Sequence[str]) -> SymbolInfoBatch:
        """Contract specs for up to 100 symbols in one call
        (``GET /SymbolInfo?symbols=…``). Symbols that fail are listed in
        ``errors`` instead of raising."""
        names = _check_symbols(symbols)
        return SymbolInfoBatch.model_validate(
            self._http.request(
                "GET", "/SymbolInfo", params={"symbols": ",".join(names)}
            )
        )

    def symbol_select(self, symbols: Sequence[str]) -> list[SymbolSelectResult]:
        """Put up to 100 symbols into Market Watch in one pass
        (``POST /SymbolSelect``) and wait up to 1 s, once, until they quote.

        Warm the symbols you will trade before the first order, so it does
        not wait for a first tick. Needs a trading key (read-only keys get
        403).
        """
        names = _check_symbols(symbols)
        rows = self._http.request("POST", "/SymbolSelect", json={"symbols": names})
        return [SymbolSelectResult.model_validate(r) for r in rows]

    def price_history(
        self,
        symbol: str,
        timeframe: Timeframe | str,
        from_: str | datetime | date | None = None,
        to: str | datetime | date | None = None,
    ) -> list[Candle]:
        tf = coerce_timeframe(timeframe)
        params = _clean(
            {
                "symbol": symbol,
                "timeframe": tf.value,
                "from": _fmt_time(from_),
                "to": _fmt_time(to),
            }
        )
        rows = self._http.request("GET", "/PriceHistory", params=params)
        return [Candle.model_validate(r) for r in rows]

    # -- calculators ------------------------------------------------------- #

    def calc_margin(
        self, symbol: str, operation: OrderOperation | str, volume: float, price: float
    ) -> MarginCalc:
        op = coerce_operation(operation)
        _require_positive_volume(volume)
        params = {
            "symbol": symbol,
            "operation": op.value,
            "volume": volume,
            "price": price,
        }
        return MarginCalc.model_validate(
            self._http.request("GET", "/OrderCalcMargin", params=params)
        )

    def calc_profit(
        self,
        symbol: str,
        operation: OrderOperation | str,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> ProfitCalc:
        op = coerce_operation(operation)
        _require_positive_volume(volume)
        params = {
            "symbol": symbol,
            "operation": op.value,
            "volume": volume,
            "priceOpen": price_open,
            "priceClose": price_close,
        }
        return ProfitCalc.model_validate(
            self._http.request("GET", "/OrderCalcProfit", params=params)
        )

    # -- trading (never retried) ------------------------------------------- #

    def order_send(
        self,
        *,
        symbol: str,
        operation: OrderOperation | str,
        volume: float,
        price: float | None = None,
        slippage: int | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        stop_limit_price: float | None = None,
        expiration: str | datetime | date | None = None,
        comment: str | None = None,
        magic: int | None = None,
        expiration_utc: str | datetime | date | None = None,
        client_order_id: str | None = None,
        execution: Execution | str | None = None,
        trailing_stop: bool | None = None,
        guaranteed_stop_loss: bool | None = None,
    ) -> OrderResult:
        """Place a market or pending order (``POST /OrderSend``).

        A reply only means the terminal answered: check ``success`` and
        ``retcode``. ``client_order_id`` makes a retry safe (see the module
        docstring).

        cTrader only: ``trailing_stop=True`` makes the stop loss trail the
        price, ``guaranteed_stop_loss=True`` makes it guaranteed where the
        broker offers it (``symbol_info().guaranteed_stop_loss``). Both need
        a ``stop_loss``. On a limited-risk account
        (``account_info().limited_risk``) every order gets a guaranteed stop
        loss by default and needs a ``stop_loss``.
        """
        body = _prepare_order_send(
            self.platform,
            symbol=symbol,
            operation=operation,
            volume=volume,
            price=price,
            slippage=slippage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            stop_limit_price=stop_limit_price,
            expiration=expiration,
            comment=comment,
            magic=magic,
            expiration_utc=expiration_utc,
            client_order_id=client_order_id,
            execution=execution,
            trailing_stop=trailing_stop,
            guaranteed_stop_loss=guaranteed_stop_loss,
        )
        return _order_result(
            *self._http.request_with_headers("POST", "/OrderSend", json=body)
        )

    def order_modify(
        self,
        ticket: int,
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        price: float | None = None,
        stop_limit_price: float | None = None,
        expiration: str | datetime | date | None = None,
        clear_stop_loss: bool = False,
        clear_take_profit: bool = False,
        expiration_utc: str | datetime | date | None = None,
        trailing_stop: bool | None = None,
        guaranteed_stop_loss: bool | None = None,
        client_order_id: str | None = None,
    ) -> OrderResult:
        """Change the stops, entry price or expiry of an order
        (``POST /OrderModify``). ``None`` keeps a value; a stop is removed
        with ``clear_stop_loss`` / ``clear_take_profit``, never with ``0``.

        cTrader only: ``trailing_stop`` turns trailing on (``True``) or off
        (``False``), ``guaranteed_stop_loss`` makes the stop loss guaranteed
        or normal, ``None`` keeps either as it is. Removing the stop loss
        drops both. ``client_order_id`` makes the change safe to repeat: the
        ids of send, modify and close are one set, a repeat changes nothing
        and answers with the first outcome (``idempotent_replayed``), and
        reusing an id for a different request raises
        :class:`~fxsocket.ClientOrderIdReusedError`.
        """
        body = _prepare_order_modify(
            self.platform,
            ticket=ticket,
            stop_loss=stop_loss,
            take_profit=take_profit,
            price=price,
            stop_limit_price=stop_limit_price,
            expiration=expiration,
            clear_stop_loss=clear_stop_loss,
            clear_take_profit=clear_take_profit,
            expiration_utc=expiration_utc,
            trailing_stop=trailing_stop,
            guaranteed_stop_loss=guaranteed_stop_loss,
            client_order_id=client_order_id,
        )
        return _order_result(
            *self._http.request_with_headers("POST", "/OrderModify", json=body)
        )

    def order_close(
        self,
        ticket: int,
        *,
        volume: float | None = None,
        slippage: int | None = None,
        pending_only: bool = False,
        client_order_id: str | None = None,
    ) -> OrderResult:
        """Close a position, or delete a pending order, by ticket
        (``POST /OrderClose``). ``volume`` closes part of a position.

        cTrader only: ``client_order_id`` makes the close safe to repeat (a
        repeat closes nothing more and answers with the first outcome), so a
        partial close that timed out can be sent again with the same id.
        """
        body = _prepare_order_close(
            self.platform,
            ticket=ticket,
            volume=volume,
            slippage=slippage,
            pending_only=pending_only,
            client_order_id=client_order_id,
        )
        return _order_result(
            *self._http.request_with_headers("POST", "/OrderClose", json=body)
        )

    def close_all(
        self,
        *,
        symbol: str | None = None,
        magic: int | None = None,
        delete_pending: bool = False,
    ) -> CloseAllSummary:
        """Close every open position in one trade-EA pass (``POST /CloseAll``).

        Optionally filtered by ``symbol`` and/or ``magic`` (``magic=0``
        matches manually-opened orders; ``None`` means no filter);
        ``delete_pending=True`` also deletes matching pending orders. The
        reply carries a per-ticket result for every attempted close/delete.

        Positions opened while the pass is running are not covered. A
        :class:`~fxsocket.TradeOutcomeUnknownError` (or, on older bridges, any
        :class:`~fxsocket.TerminalTimeoutError`) means the pass *continues to
        completion inside the terminal*: check :meth:`opened_orders` before
        acting again rather than re-sending. Only a
        :class:`~fxsocket.TradeAbandonedError` means nothing was attempted.
        """
        body = _close_all_body(
            symbol=symbol, magic=magic, delete_pending=delete_pending
        )
        return CloseAllSummary.model_validate(
            self._http.request("POST", "/CloseAll", json=body)
        )

    # -- health ------------------------------------------------------------ #

    def status(self) -> Health:
        return Health.model_validate(self._http.request("GET", "/status"))

    def healthz(self) -> HealthChecks:
        return self._probe("/healthz")

    def livez(self) -> HealthChecks:
        return self._probe("/livez")

    def _probe(self, path: str) -> HealthChecks:
        # /healthz and /livez answer 503 when not-ready, with a useful body —
        # parse it instead of raising; only other codes are real errors.
        resp = self._client.get(path)
        if resp.status_code not in (200, 503):
            raise error_from_response(resp)
        return HealthChecks.model_validate(resp.json())

    # -- lifecycle --------------------------------------------------------- #

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TerminalClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# --------------------------------------------------------------------------- #
# Async
# --------------------------------------------------------------------------- #


class AsyncTerminalClient:
    """Asynchronous mirror of :class:`TerminalClient`."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        platform: Platform | str,
        verify: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self.platform = Platform(platform)
        self.base_url = base_url
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=auth_headers(api_key),
            timeout=timeout,
            verify=verify,
        )
        self._http = AsyncHTTP(self._client)

    async def account_summary(self) -> AccountSummary:
        return AccountSummary.model_validate(
            await self._http.request("GET", "/AccountSummary")
        )

    async def account_info(self) -> AccountInfo:
        return AccountInfo.model_validate(
            await self._http.request("GET", "/AccountInfo")
        )

    async def opened_orders(self) -> list[OpenedOrder]:
        rows = await self._http.request("GET", "/OpenedOrders")
        return [OpenedOrder.model_validate(r) for r in rows]

    async def pending_orders(self) -> list[PendingOrder]:
        """Async mirror of :meth:`TerminalClient.pending_orders`."""
        rows = await self._http.request("GET", "/PendingOrders")
        return [PendingOrder.model_validate(r) for r in rows]

    async def order_history(
        self,
        from_: str | datetime | date | None = None,
        to: str | datetime | date | None = None,
    ) -> list[HistoryTrade]:
        params = _clean({"from": _fmt_time(from_), "to": _fmt_time(to)})
        rows = await self._http.request("GET", "/OrderHistory", params=params)
        return [HistoryTrade.model_validate(r) for r in rows]

    async def position_history(
        self,
        from_: str | datetime | date | None = None,
        to: str | datetime | date | None = None,
    ) -> list[PositionTrade]:
        params = _clean({"from": _fmt_time(from_), "to": _fmt_time(to)})
        rows = await self._http.request("GET", "/PositionHistory", params=params)
        return [PositionTrade.model_validate(r) for r in rows]

    async def server_timezone(self) -> ServerTimezone:
        return ServerTimezone.model_validate(
            await self._http.request("GET", "/ServerTimezone")
        )

    async def journal(
        self,
        *,
        limit: int | None = None,
        since: str | datetime | date | None = None,
        kind: JournalKind | str | None = None,
    ) -> list[JournalEntry]:
        """Async mirror of :meth:`TerminalClient.journal`."""
        params = _journal_params(self.platform, limit, since, kind)
        body = await self._http.request("GET", "/Journal", params=params)
        return [JournalEntry.model_validate(r) for r in (body or {}).get("entries", [])]

    async def symbols(self) -> list[str]:
        return list(await self._http.request("GET", "/symbols"))

    async def quote(self, symbol: str) -> Quote:
        return Quote.model_validate(
            await self._http.request("GET", "/getQuote", params={"symbol": symbol})
        )

    async def symbol_info(self, symbol: str) -> SymbolInfo:
        return SymbolInfo.model_validate(
            await self._http.request("GET", "/SymbolInfo", params={"symbol": symbol})
        )

    async def symbol_infos(self, symbols: Sequence[str]) -> SymbolInfoBatch:
        """Async mirror of :meth:`TerminalClient.symbol_infos`."""
        names = _check_symbols(symbols)
        return SymbolInfoBatch.model_validate(
            await self._http.request(
                "GET", "/SymbolInfo", params={"symbols": ",".join(names)}
            )
        )

    async def symbol_select(self, symbols: Sequence[str]) -> list[SymbolSelectResult]:
        """Async mirror of :meth:`TerminalClient.symbol_select`."""
        names = _check_symbols(symbols)
        rows = await self._http.request(
            "POST", "/SymbolSelect", json={"symbols": names}
        )
        return [SymbolSelectResult.model_validate(r) for r in rows]

    async def price_history(
        self,
        symbol: str,
        timeframe: Timeframe | str,
        from_: str | datetime | date | None = None,
        to: str | datetime | date | None = None,
    ) -> list[Candle]:
        tf = coerce_timeframe(timeframe)
        params = _clean(
            {
                "symbol": symbol,
                "timeframe": tf.value,
                "from": _fmt_time(from_),
                "to": _fmt_time(to),
            }
        )
        rows = await self._http.request("GET", "/PriceHistory", params=params)
        return [Candle.model_validate(r) for r in rows]

    async def calc_margin(
        self, symbol: str, operation: OrderOperation | str, volume: float, price: float
    ) -> MarginCalc:
        op = coerce_operation(operation)
        _require_positive_volume(volume)
        params = {
            "symbol": symbol,
            "operation": op.value,
            "volume": volume,
            "price": price,
        }
        return MarginCalc.model_validate(
            await self._http.request("GET", "/OrderCalcMargin", params=params)
        )

    async def calc_profit(
        self,
        symbol: str,
        operation: OrderOperation | str,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> ProfitCalc:
        op = coerce_operation(operation)
        _require_positive_volume(volume)
        params = {
            "symbol": symbol,
            "operation": op.value,
            "volume": volume,
            "priceOpen": price_open,
            "priceClose": price_close,
        }
        return ProfitCalc.model_validate(
            await self._http.request("GET", "/OrderCalcProfit", params=params)
        )

    async def order_send(
        self,
        *,
        symbol: str,
        operation: OrderOperation | str,
        volume: float,
        price: float | None = None,
        slippage: int | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        stop_limit_price: float | None = None,
        expiration: str | datetime | date | None = None,
        comment: str | None = None,
        magic: int | None = None,
        expiration_utc: str | datetime | date | None = None,
        client_order_id: str | None = None,
        execution: Execution | str | None = None,
        trailing_stop: bool | None = None,
        guaranteed_stop_loss: bool | None = None,
    ) -> OrderResult:
        """Async mirror of :meth:`TerminalClient.order_send`."""
        body = _prepare_order_send(
            self.platform,
            symbol=symbol,
            operation=operation,
            volume=volume,
            price=price,
            slippage=slippage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            stop_limit_price=stop_limit_price,
            expiration=expiration,
            comment=comment,
            magic=magic,
            expiration_utc=expiration_utc,
            client_order_id=client_order_id,
            execution=execution,
            trailing_stop=trailing_stop,
            guaranteed_stop_loss=guaranteed_stop_loss,
        )
        return _order_result(
            *await self._http.request_with_headers("POST", "/OrderSend", json=body)
        )

    async def order_modify(
        self,
        ticket: int,
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        price: float | None = None,
        stop_limit_price: float | None = None,
        expiration: str | datetime | date | None = None,
        clear_stop_loss: bool = False,
        clear_take_profit: bool = False,
        expiration_utc: str | datetime | date | None = None,
        trailing_stop: bool | None = None,
        guaranteed_stop_loss: bool | None = None,
        client_order_id: str | None = None,
    ) -> OrderResult:
        """Async mirror of :meth:`TerminalClient.order_modify`."""
        body = _prepare_order_modify(
            self.platform,
            ticket=ticket,
            stop_loss=stop_loss,
            take_profit=take_profit,
            price=price,
            stop_limit_price=stop_limit_price,
            expiration=expiration,
            clear_stop_loss=clear_stop_loss,
            clear_take_profit=clear_take_profit,
            expiration_utc=expiration_utc,
            trailing_stop=trailing_stop,
            guaranteed_stop_loss=guaranteed_stop_loss,
            client_order_id=client_order_id,
        )
        return _order_result(
            *await self._http.request_with_headers("POST", "/OrderModify", json=body)
        )

    async def order_close(
        self,
        ticket: int,
        *,
        volume: float | None = None,
        slippage: int | None = None,
        pending_only: bool = False,
        client_order_id: str | None = None,
    ) -> OrderResult:
        """Async mirror of :meth:`TerminalClient.order_close`."""
        body = _prepare_order_close(
            self.platform,
            ticket=ticket,
            volume=volume,
            slippage=slippage,
            pending_only=pending_only,
            client_order_id=client_order_id,
        )
        return _order_result(
            *await self._http.request_with_headers("POST", "/OrderClose", json=body)
        )

    async def close_all(
        self,
        *,
        symbol: str | None = None,
        magic: int | None = None,
        delete_pending: bool = False,
    ) -> CloseAllSummary:
        """Async mirror of :meth:`TerminalClient.close_all`."""
        body = _close_all_body(
            symbol=symbol, magic=magic, delete_pending=delete_pending
        )
        return CloseAllSummary.model_validate(
            await self._http.request("POST", "/CloseAll", json=body)
        )

    async def status(self) -> Health:
        return Health.model_validate(await self._http.request("GET", "/status"))

    async def healthz(self) -> HealthChecks:
        return await self._probe("/healthz")

    async def livez(self) -> HealthChecks:
        return await self._probe("/livez")

    async def _probe(self, path: str) -> HealthChecks:
        resp = await self._client.get(path)
        if resp.status_code not in (200, 503):
            raise error_from_response(resp)
        return HealthChecks.model_validate(resp.json())

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncTerminalClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
