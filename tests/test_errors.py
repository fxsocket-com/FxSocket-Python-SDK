"""Tests for HTTP-response → exception mapping."""

from __future__ import annotations

import httpx

from fxsocket.errors import (
    ConnectFailedError,
    FxSocketError,
    NotFoundError,
    RateLimitError,
    TerminalNotReadyError,
    TerminalTimeoutError,
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
