"""Tests for HTTP-response → exception mapping."""

from __future__ import annotations

import httpx

from fxsocket.errors import (
    ClientOrderIdReusedError,
    ConnectFailedError,
    DuplicateAccountError,
    FxSocketError,
    NotFoundError,
    RateLimitError,
    TerminalNotReadyError,
    TerminalTimeoutError,
    TradeAbandonedError,
    TradeOutcomeUnknownError,
    ValidationError,
    error_from_response,
)


def _resp(status: int, *, json: object | None = None, headers: dict | None = None):
    return httpx.Response(status, json=json, headers=headers or {})


def test_404_maps_to_not_found() -> None:
    err = error_from_response(_resp(404, json={"detail": "nope"}))
    assert isinstance(err, NotFoundError)
    assert err.message == "nope"


def test_429_parses_retry_after() -> None:
    err = error_from_response(_resp(429, json={}, headers={"Retry-After": "12"}))
    assert isinstance(err, RateLimitError)
    assert err.retry_after == 12.0


def test_400_connect_code_maps_to_connect_failed() -> None:
    err = error_from_response(
        _resp(400, json={"error": "invalid_credentials", "detail": "bad"})
    )
    assert isinstance(err, ConnectFailedError)
    assert err.code == "invalid_credentials"


def test_400_validation_default() -> None:
    err = error_from_response(
        _resp(400, json={"error": "MRPC_VALIDATION", "message": "bad volume"})
    )
    assert isinstance(err, ValidationError)
    assert err.message == "bad volume"


def test_terminal_503_and_504() -> None:
    assert isinstance(error_from_response(_resp(503, json={})), TerminalNotReadyError)
    assert isinstance(
        error_from_response(_resp(504, json={"error": "MRPC_TIMEOUT"})),
        TerminalTimeoutError,
    )


def test_unknown_status_is_base_error() -> None:
    err = error_from_response(_resp(418))
    assert type(err) is FxSocketError
    assert err.status_code == 418


def test_402_codes_map_to_typed_errors() -> None:
    from decimal import Decimal

    from fxsocket import (
        AccountCapError,
        InsufficientBalanceError,
        NoSubscriptionError,
        PaymentRequiredError,
        SeatLapsedError,
    )

    cap = error_from_response(
        _resp(402, json={"error": "account_cap_reached", "cap": 3, "current": 3})
    )
    assert isinstance(cap, AccountCapError) and isinstance(cap, PaymentRequiredError)

    low = error_from_response(
        _resp(
            402,
            json={
                "error": "insufficient_balance",
                "detail": "Balance does not cover it.",
                "shortfall_eur_cents": 1250,
                "balance_eur_cents": 450,
            },
        )
    )
    assert isinstance(low, InsufficientBalanceError)
    assert low.shortfall_eur_cents == 1250
    assert low.shortfall_eur == Decimal("12.5")
    assert low.balance_eur_cents == 450

    bare = error_from_response(_resp(402, json={"error": "insufficient_balance"}))
    assert isinstance(bare, InsufficientBalanceError)
    assert bare.shortfall_eur_cents is None and bare.shortfall_eur is None

    lapsed = error_from_response(_resp(402, json={"error": "seat_lapsed"}))
    assert isinstance(lapsed, SeatLapsedError)

    none = error_from_response(_resp(402, json={"error": "no_subscription"}))
    assert isinstance(none, NoSubscriptionError)
    assert isinstance(error_from_response(_resp(402, json={})), NoSubscriptionError)

    unknown = error_from_response(_resp(402, json={"error": "new_code"}))
    assert type(unknown) is PaymentRequiredError
    assert unknown.code == "new_code"


def test_504_split_by_code() -> None:
    unknown = error_from_response(
        _resp(504, json={"error": "MRPC_TRADE_OUTCOME_UNKNOWN", "message": "m"})
    )
    assert isinstance(unknown, TradeOutcomeUnknownError)
    assert isinstance(unknown, TerminalTimeoutError)
    assert unknown.code == "MRPC_TRADE_OUTCOME_UNKNOWN"

    abandoned = error_from_response(_resp(504, json={"error": "MRPC_ABANDONED"}))
    assert isinstance(abandoned, TradeAbandonedError)
    assert isinstance(abandoned, TerminalTimeoutError)

    read = error_from_response(_resp(504, json={"error": "MRPC_TIMEOUT"}))
    assert type(read) is TerminalTimeoutError
    # A 504 without a body (e.g. from a proxy) is a plain timeout too.
    assert type(error_from_response(_resp(504))) is TerminalTimeoutError


def test_409_client_order_id_reused() -> None:
    err = error_from_response(
        _resp(409, json={"error": "MRPC_CLIENT_ORDER_ID_REUSED", "message": "m"})
    )
    assert isinstance(err, ClientOrderIdReusedError)
    assert not isinstance(err, DuplicateAccountError)
    # Other 409s keep their old mapping.
    assert isinstance(
        error_from_response(_resp(409, json={"detail": "already linked"})),
        DuplicateAccountError,
    )


def test_trade_ea_stopped_is_not_ready() -> None:
    err = error_from_response(
        _resp(500, json={"error": "MRPC_TRADE_EA_STOPPED", "message": "m"})
    )
    assert isinstance(err, TerminalNotReadyError)
    assert err.status_code == 500


def test_unknown_symbol_400_is_validation() -> None:
    err = error_from_response(
        _resp(400, json={"error": "MRPC_UNKNOWN_SYMBOL", "message": "unknown"})
    )
    assert isinstance(err, ValidationError)
    assert err.code == "MRPC_UNKNOWN_SYMBOL"


def test_terminal_error_code_enum_matches_codes() -> None:
    from fxsocket import TerminalErrorCode

    for member in TerminalErrorCode:
        assert member.name == member.value
    assert TerminalErrorCode.MRPC_ABANDONED == "MRPC_ABANDONED"
    assert "MRPC" in {c.value for c in TerminalErrorCode}
