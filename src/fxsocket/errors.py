"""Exception hierarchy and HTTP-response → exception mapping.

A single :func:`error_from_response` maps both error envelopes the platform
uses — the management API's ``{"error", "detail"}`` and the terminal API's
``{"error", "message", "command_id"}`` — onto typed exceptions.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx


class FxSocketError(Exception):
    """Base class for every error raised by the SDK."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        response: httpx.Response | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.response = response


class AuthError(FxSocketError):
    """Missing or invalid API key (HTTP 401), or no key configured."""


class ForbiddenError(FxSocketError):
    """The key is valid but may not do this (HTTP 403) — typically a
    read-only ``fxs_ro_…`` key on an endpoint that mutates state, such as
    multi-account trading."""


class RateLimitError(FxSocketError):
    """Too many requests (HTTP 429). ``retry_after`` is seconds, if given."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kw: Any):
        super().__init__(message, **kw)
        self.retry_after = retry_after


class ValidationError(FxSocketError):
    """The request was rejected as malformed (HTTP 400 / ``MRPC_VALIDATION``)."""


class NotFoundError(FxSocketError):
    """The referenced account or resource does not exist (HTTP 404)."""


class PaymentRequiredError(FxSocketError):
    """Your plan or prepaid balance does not allow this (HTTP 402).

    Base class for :class:`AccountCapError`, :class:`NoSubscriptionError`,
    :class:`InsufficientBalanceError` and :class:`SeatLapsedError`; raised
    directly only for a 402 whose ``code`` the SDK does not know yet.
    """


class AccountCapError(PaymentRequiredError):
    """Plan account limit reached (HTTP 402 ``account_cap_reached``)."""

    def __init__(
        self,
        message: str,
        *,
        cap: int | None = None,
        current: int | None = None,
        **kw: Any,
    ):
        super().__init__(message, **kw)
        self.cap = cap
        self.current = current


class NoSubscriptionError(PaymentRequiredError):
    """No plan permits linking accounts (HTTP 402 ``no_subscription``)."""


class InsufficientBalanceError(PaymentRequiredError):
    """The prepaid balance is too low (HTTP 402 ``insufficient_balance``).

    Raised when linking an account would buy a seat the balance can't
    cover, and by balance-funded private-server purchases / resizes.
    ``shortfall_eur_cents`` is how much is missing when the API says so
    (``None`` otherwise); ``balance_eur_cents`` the current balance if
    reported. Top up in the dashboard, or check ``client.wallet.get()``.
    """

    def __init__(
        self,
        message: str,
        *,
        shortfall_eur_cents: int | None = None,
        balance_eur_cents: int | None = None,
        **kw: Any,
    ):
        super().__init__(message, **kw)
        self.shortfall_eur_cents = shortfall_eur_cents
        self.balance_eur_cents = balance_eur_cents

    @property
    def shortfall_eur(self) -> Decimal | None:
        """The missing amount in euros, if known."""
        if self.shortfall_eur_cents is None:
            return None
        return Decimal(self.shortfall_eur_cents) / Decimal(100)


class SeatLapsedError(PaymentRequiredError):
    """Account seats have lapsed, so existing accounts are unseated (HTTP
    402 ``seat_lapsed``). Renew — top up the balance or fix the payment
    method in the dashboard — before linking more accounts."""


class DuplicateAccountError(FxSocketError):
    """This account is already linked (HTTP 409)."""


class SlotsFullError(FxSocketError):
    """Every purchased slot on the private server is taken (HTTP 409
    ``slots_full``). Raise the server's limit from the dashboard."""

    def __init__(
        self,
        message: str,
        *,
        used: int | None = None,
        cap: int | None = None,
        **kw: Any,
    ):
        super().__init__(message, **kw)
        self.used = used
        self.cap = cap


class IdempotencyError(FxSocketError):
    """A multi-account batch was refused because of its ``Idempotency-Key``.

    Nothing was sent. ``code`` says why: ``idempotency_in_flight`` (HTTP
    409 — a batch with this key is still running), ``idempotency_key_reused``
    (HTTP 422 — the key was already used for a *different* body) or
    ``idempotency_unavailable`` (HTTP 503 — the guarantee can't currently be
    honoured; retry, or drop the key to trade without it).
    """


class ConnectFailedError(FxSocketError):
    """The account could not be linked (HTTP 400).

    ``code`` is one of ``invalid_credentials``, ``server_not_found``,
    ``unknown`` (the broker rejected the login) or ``proxy_unreachable``
    (the supplied outbound proxy could not be reached — the proxy is
    verified before the account is created).
    """


class TerminalNotReadyError(FxSocketError):
    """The account's terminal isn't reachable yet.

    Raised on HTTP 503 (trade EA not registered) and when an account has no
    ``rest_url`` — it is still provisioning, or is bridge-only and exposes no
    per-account terminal API.
    """


class TerminalTimeoutError(FxSocketError):
    """The terminal didn't answer in time (HTTP 504 / ``MRPC_TIMEOUT``)."""


class UnsupportedOnPlatformError(FxSocketError):
    """A requested feature doesn't exist on the account's platform.

    Enforced client-side — e.g. stop-limit orders or MT5-only timeframes on
    an MT4 account.
    """


class StreamError(FxSocketError):
    """A WebSocket-level error (server error frame, or dropped connection)."""


_CONNECT_CODES = frozenset(
    {"invalid_credentials", "server_not_found", "unknown", "proxy_unreachable"}
)
_IDEMPOTENCY_CODES = frozenset(
    {"idempotency_in_flight", "idempotency_key_reused", "idempotency_unavailable"}
)


def _int_or_none(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def error_from_response(resp: httpx.Response) -> FxSocketError:
    """Build the most specific :class:`FxSocketError` for a failed response."""
    status = resp.status_code
    body: Any = None
    try:
        body = resp.json()
    except ValueError:
        body = None

    code: str | None = None
    detail: str | None = None
    if isinstance(body, dict):
        code = body.get("error")
        detail = body.get("detail") or body.get("message")
    message = detail or code or f"HTTP {status}"
    common: dict[str, Any] = {
        "status_code": status,
        "code": code,
        "response": resp,
    }

    if status == 401:
        return AuthError(message, **common)
    if status == 403:
        return ForbiddenError(message, **common)
    if code in _IDEMPOTENCY_CODES:
        return IdempotencyError(message, **common)
    if status == 429:
        raw = resp.headers.get("Retry-After")
        retry = None
        if raw:
            try:
                retry = float(raw)
            except ValueError:
                retry = None
        return RateLimitError(message, retry_after=retry, **common)
    if status == 404:
        return NotFoundError(message, **common)
    if status == 409:
        if code == "slots_full" and isinstance(body, dict):
            return SlotsFullError(
                message, used=body.get("used"), cap=body.get("cap"), **common
            )
        return DuplicateAccountError(message, **common)
    if status == 402:
        fields: dict[str, Any] = body if isinstance(body, dict) else {}
        if code == "account_cap_reached":
            return AccountCapError(
                message, cap=fields.get("cap"), current=fields.get("current"), **common
            )
        if code == "insufficient_balance":
            return InsufficientBalanceError(
                message,
                shortfall_eur_cents=_int_or_none(
                    fields.get("shortfall_eur_cents", fields.get("shortfall"))
                ),
                balance_eur_cents=_int_or_none(fields.get("balance_eur_cents")),
                **common,
            )
        if code == "seat_lapsed":
            return SeatLapsedError(message, **common)
        if code in (None, "no_subscription"):
            return NoSubscriptionError(message, **common)
        return PaymentRequiredError(message, **common)
    if status == 400:
        if code in _CONNECT_CODES:
            return ConnectFailedError(message, **common)
        return ValidationError(message, **common)
    if status == 503:
        return TerminalNotReadyError(message, **common)
    if status == 504:
        return TerminalTimeoutError(message, **common)
    return FxSocketError(message, **common)
