"""Exception hierarchy and HTTP-response → exception mapping.

A single :func:`error_from_response` maps both error envelopes the platform
uses — the management API's ``{"error", "detail"}`` and the terminal API's
``{"error", "message", "command_id"}`` — onto typed exceptions.
"""

from __future__ import annotations

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


class RateLimitError(FxSocketError):
    """Too many requests (HTTP 429). ``retry_after`` is seconds, if given."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kw: Any):
        super().__init__(message, **kw)
        self.retry_after = retry_after


class ValidationError(FxSocketError):
    """The request was rejected as malformed (HTTP 400 / ``MRPC_VALIDATION``)."""


class NotFoundError(FxSocketError):
    """The referenced account or resource does not exist (HTTP 404)."""


class AccountCapError(FxSocketError):
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


class NoSubscriptionError(FxSocketError):
    """No plan permits linking accounts (HTTP 402 ``no_subscription``)."""


class DuplicateAccountError(FxSocketError):
    """This account is already linked (HTTP 409)."""


class ConnectFailedError(FxSocketError):
    """The broker rejected the login during account creation (HTTP 400).

    ``code`` is one of ``invalid_credentials``, ``server_not_found``,
    ``unknown``.
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


_CONNECT_CODES = frozenset({"invalid_credentials", "server_not_found", "unknown"})


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
        return DuplicateAccountError(message, **common)
    if status == 402:
        if code == "account_cap_reached" and isinstance(body, dict):
            return AccountCapError(
                message, cap=body.get("cap"), current=body.get("current"), **common
            )
        return NoSubscriptionError(message, **common)
    if status == 400:
        if code in _CONNECT_CODES:
            return ConnectFailedError(message, **common)
        return ValidationError(message, **common)
    if status == 503:
        return TerminalNotReadyError(message, **common)
    if status == 504:
        return TerminalTimeoutError(message, **common)
    return FxSocketError(message, **common)
