"""Tests for multi-account trading (``client.orders``), sync + async."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from fxsocket import (
    Account,
    AsyncClient,
    Client,
    CloseDefaults,
    ClosedTicketStatus,
    CloseKind,
    CloseLeg,
    CloseLegStatus,
    ForbiddenError,
    IdempotencyError,
    OrderDefaults,
    OrderLeg,
    OrderLegStatus,
    OrderOperation,
    ValidationError,
)

BASE = "https://api.fxsocket.com/v1"
A1 = "d04096e8-79cd-4078-bc8c-0fd245198938"
A2 = "11111111-1111-1111-1111-111111111111"

ACCOUNT = Account.model_validate(
    {
        "id": A1,
        "platform": "mt5",
        "server": "ICMarkets-Demo",
        "login": 1150125,
        "status": "connected",
        "created_at": "2026-06-22T16:53:56Z",
    }
)


def _leg_result(account_id: str, status: str = "filled", **kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "account_id": account_id,
        "platform": "mt5",
        "symbol": "EURUSD",
        "operation": "Buy",
        "volume": 0.1,
        "status": status,
        "order": 123,
        "deal": 456,
        "price": 1.1,
        "bid": 1.0999,
        "ask": 1.1001,
        "retcode": 10009,
        "retcode_description": "Request completed",
        "message": "",
        "latency_ms": 120,
    }
    row.update(kw)
    return row


ORDER_RESPONSE = {
    "batch_id": "batch-1",
    "idempotent_replay": False,
    "summary": {"requested": 2, "filled": 1, "failed": 0, "unknown": 1},
    "results": [
        _leg_result(A1),
        _leg_result(
            A2,
            status="timeout",
            order=0,
            deal=0,
            retcode=0,
            retcode_description="",
            message="terminal did not answer",
        ),
    ],
}

CLOSE_RESPONSE = {
    "batch_id": "batch-2",
    "idempotent_replay": False,
    "summary": {
        "accounts": 2,
        "matched": 2,
        "closed": 1,
        "failed": 0,
        "unknown": 1,
        "accounts_unknown": 0,
    },
    "results": [
        {
            "account_id": A1,
            "platform": "mt5",
            "status": "partial",
            "matched": 2,
            "closed": 1,
            "message": "",
            "latency_ms": 300,
            "results": [
                {
                    "ticket": 1,
                    "symbol": "EURUSD",
                    "type": "Buy",
                    "kind": "position",
                    "volume": 0.1,
                    "status": "closed",
                    "retcode": 10009,
                    "retcode_description": "Request completed",
                    "price": 1.1,
                    "message": "",
                    "latency_ms": 100,
                },
                {
                    "ticket": 2,
                    "symbol": "EURUSD",
                    "type": "BuyLimit",
                    "kind": "pending",
                    "volume": 0.1,
                    "status": "timeout",
                    "retcode": 0,
                    "retcode_description": "",
                    "price": 0.0,
                    "message": "no reply",
                    "latency_ms": 200,
                },
            ],
        },
        {
            "account_id": A2,
            "platform": "mt4",
            "status": "nothing_matched",
            "matched": 0,
            "closed": 0,
            "message": "",
            "latency_ms": 50,
            "results": [],
        },
    ],
}


def _client() -> Client:
    return Client(api_key="fxs_live_test")


def _sent(route: respx.Route) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(route.calls.last.request.content)
    return body


# --------------------------------------------------------------------------- #
# send
# --------------------------------------------------------------------------- #


@respx.mock
def test_send_merges_defaults_and_legs() -> None:
    route = respx.post(f"{BASE}/orders").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )
    with _client() as fx:
        fx.orders.send(
            [
                OrderLeg(account_id=A1, volume=0.1),
                {"account_id": A2, "volume": 0.2, "magic": 7, "slippage": 0},
            ],
            defaults=OrderDefaults(symbol="EURUSD", operation="buy", stop_loss=1.07),
        )

    assert _sent(route) == {
        "orders": [
            {"account_id": A1, "volume": 0.1},
            {"account_id": A2, "volume": 0.2, "slippage": 0, "expert_id": 7},
        ],
        "defaults": {"symbol": "EURUSD", "operation": "Buy", "stop_loss": 1.07},
    }
    assert "Idempotency-Key" not in route.calls.last.request.headers


@respx.mock
def test_send_accepts_bare_accounts_and_enum_operation() -> None:
    route = respx.post(f"{BASE}/orders").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )
    with _client() as fx:
        fx.orders.send(
            [ACCOUNT, A2],
            defaults={
                "symbol": "EURUSD",
                "operation": OrderOperation.SELL_LIMIT,
                "volume": 0.5,
                "price": 1.2,
            },
            require_reachable=True,
        )

    assert _sent(route) == {
        "orders": [{"account_id": A1}, {"account_id": A2}],
        "defaults": {
            "symbol": "EURUSD",
            "operation": "SellLimit",
            "volume": 0.5,
            "price": 1.2,
        },
        "require_reachable": True,
    }


@respx.mock
def test_send_leg_overrides_and_expert_id_alias() -> None:
    route = respx.post(f"{BASE}/orders").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )
    with _client() as fx:
        fx.orders.send(
            [
                OrderLeg(
                    account_id=ACCOUNT,
                    symbol="EURUSD.sd",
                    operation="SELL",
                    volume=0.3,
                    expert_id=42,  # API spelling accepted too
                    comment="hedge",
                ),
                OrderLeg(account_id=A2, expiration="2026-12-31T00:00:00"),
            ],
            defaults=OrderDefaults(symbol="EURUSD", operation="buy", volume=0.1),
        )

    sent = _sent(route)
    assert sent["orders"][0] == {
        "account_id": A1,
        "symbol": "EURUSD.sd",
        "operation": "Sell",
        "volume": 0.3,
        "comment": "hedge",
        "expert_id": 42,
    }
    assert sent["orders"][1] == {
        "account_id": A2,
        "expiration": "2026-12-31T00:00:00",
    }


@respx.mock
def test_send_idempotency_key_header_and_replay_flag() -> None:
    route = respx.post(f"{BASE}/orders").mock(
        return_value=httpx.Response(
            200, json={**ORDER_RESPONSE, "idempotent_replay": True}
        )
    )
    with _client() as fx:
        result = fx.orders.send(
            [A1],
            defaults={"symbol": "EURUSD", "operation": "buy", "volume": 0.1},
            idempotency_key="signal-2026-09-09-1",
        )

    assert route.calls.last.request.headers["Idempotency-Key"] == "signal-2026-09-09-1"
    assert result.idempotent_replay is True


@respx.mock
def test_send_results_are_positional_with_helpers() -> None:
    respx.post(f"{BASE}/orders").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )
    legs = [OrderLeg(account_id=A1, volume=0.1), OrderLeg(account_id=A2, volume=0.2)]
    with _client() as fx:
        result = fx.orders.send(
            legs, defaults=OrderDefaults(symbol="EURUSD", operation="buy")
        )

    assert result.batch_id == "batch-1"
    assert result.summary.requested == 2
    assert result.all_filled is False
    first, second = result.results
    paired = list(zip(legs, result.results, strict=True))
    assert [leg.account_id for leg, _ in paired] == [A1, A2]
    assert first.status == OrderLegStatus.FILLED
    assert first.is_filled and not first.is_unknown
    assert first.order == 123 and first.deal == 456
    assert second.status == "timeout"
    assert second.is_unknown and not second.is_filled
    assert result.filled_legs == [first]
    assert result.unknown_legs == [second]
    assert result.failed_legs == []


def test_send_validates_before_sending() -> None:
    defaults = OrderDefaults(symbol="EURUSD", operation="buy")
    with _client() as fx:
        with pytest.raises(ValidationError, match=r"orders\[0\]: volume is required"):
            fx.orders.send([OrderLeg(account_id=A1)], defaults=defaults)
        with pytest.raises(ValidationError, match=r"orders\[1\]: price is required"):
            fx.orders.send(
                [
                    OrderLeg(account_id=A1, volume=0.1),
                    OrderLeg(account_id=A2, volume=0.1, operation="buyLimit"),
                ],
                defaults=defaults,
            )
        with pytest.raises(ValidationError, match=r"orders\[0\]: volume must be > 0"):
            fx.orders.send([OrderLeg(account_id=A1, volume=0)], defaults=defaults)
        with pytest.raises(ValidationError, match="Unknown order operation"):
            fx.orders.send(
                [OrderLeg(account_id=A1, volume=0.1, operation="bogus")],
                defaults=defaults,
            )
        with pytest.raises(ValidationError, match=r"orders\[0\]: symbol is required"):
            fx.orders.send([OrderLeg(account_id=A1, volume=0.1, operation="buy")])
        with pytest.raises(ValidationError, match="at least one leg"):
            fx.orders.send([], defaults=defaults)
        with pytest.raises(ValidationError, match=r"orders\[0\]"):
            fx.orders.send(
                [{"account_id": A1, "volume": 0.1, "lots": 1}], defaults=defaults
            )
        with pytest.raises(ValidationError, match="idempotency_key"):
            fx.orders.send(
                [OrderLeg(account_id=A1, volume=0.1)],
                defaults=defaults,
                idempotency_key="x" * 129,
            )


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (409, "idempotency_in_flight"),
        (422, "idempotency_key_reused"),
        (503, "idempotency_unavailable"),
    ],
)
@respx.mock
def test_idempotency_failures_map_to_typed_error(status: int, code: str) -> None:
    respx.post(f"{BASE}/orders").mock(
        return_value=httpx.Response(status, json={"error": code, "detail": "nope"})
    )
    with _client() as fx, pytest.raises(IdempotencyError) as err:
        fx.orders.send(
            [A1],
            defaults={"symbol": "EURUSD", "operation": "buy", "volume": 0.1},
            idempotency_key="k",
        )
    assert err.value.code == code
    assert err.value.status_code == status


@respx.mock
def test_invalid_batch_and_readonly_key_errors() -> None:
    respx.post(f"{BASE}/orders").mock(
        side_effect=[
            httpx.Response(
                400, json={"error": "unknown_account", "detail": "not yours"}
            ),
            httpx.Response(403, json={"error": "read_only", "detail": "read-only key"}),
        ]
    )
    defaults = {"symbol": "EURUSD", "operation": "buy", "volume": 0.1}
    with _client() as fx:
        with pytest.raises(ValidationError) as err:
            fx.orders.send([A1], defaults=defaults)
        assert err.value.code == "unknown_account"
        with pytest.raises(ForbiddenError):
            fx.orders.send([A1], defaults=defaults)


# --------------------------------------------------------------------------- #
# close
# --------------------------------------------------------------------------- #


@respx.mock
def test_close_payload_selector_and_tickets() -> None:
    route = respx.post(f"{BASE}/orders/close").mock(
        return_value=httpx.Response(200, json=CLOSE_RESPONSE)
    )
    with _client() as fx:
        fx.orders.close(
            [
                CloseLeg(account_id=ACCOUNT, side="short", volume=0.05),
                {"account_id": A2, "tickets": [11, 12], "slippage": 20},
            ],
            defaults=CloseDefaults(
                symbol="EURUSD", symbol_match="BASE", kind=CloseKind.ANY, magic=0
            ),
            idempotency_key="close-1",
        )

    assert _sent(route) == {
        "accounts": [
            {"account_id": A1, "side": "short", "volume": 0.05},
            {"account_id": A2, "tickets": [11, 12], "slippage": 20},
        ],
        "defaults": {
            "symbol": "EURUSD",
            "symbol_match": "base",
            "kind": "any",
            "magic": 0,
        },
    }
    assert route.calls.last.request.headers["Idempotency-Key"] == "close-1"


@respx.mock
def test_close_everything_needs_literal_star() -> None:
    route = respx.post(f"{BASE}/orders/close").mock(
        return_value=httpx.Response(200, json=CLOSE_RESPONSE)
    )
    with _client() as fx:
        fx.orders.close([A1, A2], defaults={"symbol": "*"})
    assert _sent(route) == {
        "accounts": [{"account_id": A1}, {"account_id": A2}],
        "defaults": {"symbol": "*"},
    }


def test_close_validates_before_sending() -> None:
    with _client() as fx:
        with pytest.raises(ValidationError, match=r"accounts\[0\]: symbol is required"):
            fx.orders.close([A1])
        with pytest.raises(ValidationError, match="tickets can't be combined"):
            fx.orders.close([CloseLeg(account_id=A1, symbol="EURUSD", tickets=[1])])
        with pytest.raises(ValidationError, match="tickets must not be empty"):
            fx.orders.close([CloseLeg(account_id=A1, tickets=[])])
        with pytest.raises(ValidationError, match="tickets must be >= 1"):
            fx.orders.close([CloseLeg(account_id=A1, tickets=[0])])
        with pytest.raises(ValidationError, match="volume must be > 0"):
            fx.orders.close([CloseLeg(account_id=A1, symbol="EURUSD", volume=0)])
        with pytest.raises(ValidationError, match="side must be one of"):
            fx.orders.close([CloseLeg(account_id=A1, symbol="EURUSD", side="up")])
        with pytest.raises(ValidationError, match="at least one entry"):
            fx.orders.close([], defaults={"symbol": "*"})


@respx.mock
def test_close_result_parsing_and_helpers() -> None:
    respx.post(f"{BASE}/orders/close").mock(
        return_value=httpx.Response(200, json=CLOSE_RESPONSE)
    )
    with _client() as fx:
        result = fx.orders.close([A1, A2], defaults={"symbol": "EURUSD"})

    assert result.batch_id == "batch-2"
    assert result.summary.unknown == 1
    assert result.all_closed is False
    first, second = result.results
    assert first.status == CloseLegStatus.PARTIAL
    assert first.matched == 2 and first.closed == 1
    closed, timed_out = first.results
    assert closed.status == ClosedTicketStatus.CLOSED and closed.is_closed
    assert timed_out.is_unknown and timed_out.is_pending
    assert second.nothing_matched and not second.is_unknown
    assert result.unknown_accounts == []


@respx.mock
def test_close_all_closed_when_nothing_pending() -> None:
    clean = {
        **CLOSE_RESPONSE,
        "summary": {
            "accounts": 1,
            "matched": 0,
            "closed": 0,
            "failed": 0,
            "unknown": 0,
            "accounts_unknown": 0,
        },
        "results": [CLOSE_RESPONSE["results"][1]],
    }
    respx.post(f"{BASE}/orders/close").mock(
        return_value=httpx.Response(200, json=clean)
    )
    with _client() as fx:
        result = fx.orders.close([A2], defaults={"symbol": "*"})
    assert result.all_closed is True


# --------------------------------------------------------------------------- #
# async
# --------------------------------------------------------------------------- #


@respx.mock
async def test_async_send_and_close() -> None:
    send_route = respx.post(f"{BASE}/orders").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )
    close_route = respx.post(f"{BASE}/orders/close").mock(
        return_value=httpx.Response(200, json=CLOSE_RESPONSE)
    )
    async with AsyncClient(api_key="fxs_live_test") as fx:
        sent = await fx.orders.send(
            [OrderLeg(account_id=A1, volume=0.1)],
            defaults=OrderDefaults(symbol="EURUSD", operation="buy"),
            idempotency_key="async-1",
        )
        closed = await fx.orders.close(
            [CloseLeg(account_id=A1)], defaults=CloseDefaults(symbol="EURUSD")
        )

    assert send_route.calls.last.request.headers["Idempotency-Key"] == "async-1"
    assert _sent(send_route)["orders"] == [{"account_id": A1, "volume": 0.1}]
    assert sent.summary.filled == 1
    assert _sent(close_route) == {
        "accounts": [{"account_id": A1}],
        "defaults": {"symbol": "EURUSD"},
    }
    assert closed.results[0].status == CloseLegStatus.PARTIAL
