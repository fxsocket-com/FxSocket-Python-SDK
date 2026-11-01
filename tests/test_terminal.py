"""Tests for the terminal REST client (sync + async), respx-mocked."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
import respx

from fxsocket import (
    AsyncClient,
    Client,
    ClientOrderIdReusedError,
    ExpirationMode,
    OrderOperation,
    Platform,
    TerminalNotReadyError,
    TerminalTimeoutError,
    Timeframe,
    TradeAbandonedError,
    TradeOutcomeUnknownError,
    UnsupportedOnPlatformError,
    ValidationError,
)
from fxsocket.models import Account
from fxsocket.terminal.client import (
    AsyncTerminalClient,
    TerminalClient,
    coerce_operation,
    coerce_timeframe,
)

TERM = "https://term.test"

_ORDER_OK = {
    "success": True,
    "retcode": 10009,
    "retcodeDescription": "Done",
    "deal": 0,
    "order": 100,
    "volume": 0.1,
    "price": 1.085,
    "bid": 1.0849,
    "ask": 1.0851,
    "comment": "",
}


def _term(platform: str = "mt5") -> TerminalClient:
    return TerminalClient(base_url=TERM, api_key="fxs_live_k", platform=platform)


def _account(rest_url: str = f"{TERM}", platform: str = "mt5") -> Account:
    return Account.model_validate(
        {
            "id": "acc-1",
            "platform": platform,
            "server": "Demo",
            "login": 1,
            "status": "connected",
            "rest_url": rest_url,
            "ws_url": "wss://term.test/ws",
            "created_at": "2026-06-22T16:53:56Z",
        }
    )


# --------------------------------------------------------------------------- #
# Coercion helpers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value,expected",
    [
        ("buy", OrderOperation.BUY),
        ("Buy", OrderOperation.BUY),
        ("buy_limit", OrderOperation.BUY_LIMIT),
        ("BUY-STOP-LIMIT", OrderOperation.BUY_STOP_LIMIT),
        (OrderOperation.SELL, OrderOperation.SELL),
    ],
)
def test_coerce_operation(value: object, expected: OrderOperation) -> None:
    assert coerce_operation(value) is expected  # type: ignore[arg-type]


def test_coerce_operation_unknown() -> None:
    from fxsocket import ValidationError

    with pytest.raises(ValidationError):
        coerce_operation("teleport")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("M5", Timeframe.M5),
        ("m5", Timeframe.M5),
        ("5min", Timeframe.M5),
        ("1h", Timeframe.H1),
        ("1d", Timeframe.D1),
        (Timeframe.H4, Timeframe.H4),
    ],
)
def test_coerce_timeframe(value: object, expected: Timeframe) -> None:
    assert coerce_timeframe(value) is expected  # type: ignore[arg-type]


def test_timeframe_matches_terminal_api() -> None:
    # The nine labels the MT4 and MT5 terminal APIs parse (REST + `bars`).
    assert [tf.value for tf in Timeframe] == [
        "M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"
    ]


@pytest.mark.parametrize("value", ["H6", "M2", "h12", "2h", "3min", "7s"])
def test_coerce_timeframe_unsupported(value: str) -> None:
    from fxsocket import ValidationError

    with pytest.raises(ValidationError, match="M1, M5, M15"):
        coerce_timeframe(value)


# --------------------------------------------------------------------------- #
# Parsing — camelCase aliases, str timestamps
# --------------------------------------------------------------------------- #


@respx.mock
def test_account_summary_parses_camelcase() -> None:
    respx.get(f"{TERM}/AccountSummary").mock(
        return_value=httpx.Response(
            200,
            json={
                "balance": 100000.0,
                "credit": 0.0,
                "profit": 12.5,
                "equity": 100012.5,
                "margin": 500.0,
                "freeMargin": 99512.5,
                "marginLevel": 2002.5,
                "leverage": 200,
                "currency": "USD",
                "type": "Demo",
            },
        )
    )
    with _term() as t:
        s = t.account_summary()
    assert s.free_margin == 99512.5
    assert s.margin_level == 2002.5
    assert s.leverage == 200


@respx.mock
def test_opened_orders_and_is_pending() -> None:
    respx.get(f"{TERM}/OpenedOrders").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "ticket": 1,
                    "symbol": "EURUSD",
                    "type": "Buy",
                    "kind": "Position",
                    "lots": 0.1,
                    "openPrice": 1.08,
                    "currentPrice": 1.085,
                    "stopLoss": 0.0,
                    "takeProfit": 0.0,
                    "swap": 0.0,
                    "profit": 5.0,
                    "magic": 0,
                    "comment": "",
                    "openTime": "2026-06-22T15:30:00Z",
                },
                {
                    "ticket": 2,
                    "symbol": "EURUSD",
                    "type": "BuyLimit",
                    "kind": "Pending",
                    "lots": 0.1,
                    "openPrice": 1.07,
                    "currentPrice": 1.07,
                    "stopLoss": 0.0,
                    "takeProfit": 0.0,
                    "swap": 0.0,
                    "profit": 0.0,
                    "magic": 0,
                    "comment": "",
                    "openTime": "2026-06-22T15:30:00Z",
                },
            ],
        )
    )
    with _term() as t:
        orders = t.opened_orders()
    assert [o.is_pending for o in orders] == [False, True]
    # Timestamps stay raw strings (broker server time), never datetime.
    assert isinstance(orders[0].open_time, str)


@respx.mock
def test_status_nested_health() -> None:
    respx.get(f"{TERM}/status").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ready",
                "terminal": {"alive": True, "build": 5836, "pingMs": 108},
                "broker": {"connected": True, "server": "VTMarkets-Demo"},
                "account": {
                    "loggedIn": True,
                    "login": 1150125,
                    "currency": "USD",
                    "type": "Demo",
                    "tradeAllowed": True,
                },
                "bridge": {
                    "version": "0.5.0",
                    "tradeEaReady": True,
                    "symbolsSynced": True,
                },
                "serverTime": "2026-06-22T16:53:56.000Z",
            },
        )
    )
    with _term() as t:
        h = t.status()
    assert h.is_ready
    assert h.terminal.ping_ms == 108
    assert h.account.logged_in is True
    assert h.bridge.trade_ea_ready is True
    # Pre-0.10 bridges omit the heartbeat — defaults to -1 (never/unknown).
    assert h.bridge.trade_ea_heartbeat_age_ms == -1


def test_bridge_health_heartbeat_parses() -> None:
    from fxsocket.models import BridgeHealth

    b = BridgeHealth.model_validate(
        {
            "version": "0.10.0",
            "tradeEaReady": True,
            "tradeEaHeartbeatAgeMs": 8,
            "symbolsSynced": True,
        }
    )
    assert b.trade_ea_heartbeat_age_ms == 8


# --------------------------------------------------------------------------- #
# Market data — query params + timeframe guard
# --------------------------------------------------------------------------- #


@respx.mock
def test_price_history_sends_canonical_timeframe() -> None:
    route = respx.get(f"{TERM}/PriceHistory").mock(
        return_value=httpx.Response(200, json=[])
    )
    with _term() as t:
        t.price_history("EURUSD", "5min", from_="2026-06-01")
    req = route.calls.last.request
    assert req.url.params["timeframe"] == "M5"
    assert req.url.params["symbol"] == "EURUSD"
    assert req.url.params["from"] == "2026-06-01"
    assert "to" not in req.url.params  # None omitted


@respx.mock
@pytest.mark.parametrize("platform", ["mt4", "mt5"])
def test_price_history_rejects_unsupported_timeframe(platform: str) -> None:
    from fxsocket import ValidationError

    route = respx.get(f"{TERM}/PriceHistory").mock(
        return_value=httpx.Response(200, json=[])
    )
    with _term(platform=platform) as t, pytest.raises(ValidationError):
        t.price_history("EURUSD", "H6")
    assert not route.called


# --------------------------------------------------------------------------- #
# Trading — body building, platform guard, error mapping
# --------------------------------------------------------------------------- #


@respx.mock
def test_order_send_body_and_aliases() -> None:
    route = respx.post(f"{TERM}/OrderSend").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "retcode": 10009,
                "retcodeDescription": "Done",
                "deal": 99,
                "order": 100,
                "volume": 0.1,
                "price": 1.085,
                "bid": 1.0849,
                "ask": 1.0851,
                "comment": "",
            },
        )
    )
    with _term() as t:
        res = t.order_send(
            symbol="EURUSD", operation="buy_limit", volume=0.1, price=1.07, magic=42
        )
    assert res.success and res.order == 100
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "symbol": "EURUSD",
        "operation": "BuyLimit",  # canonical
        "volume": 0.1,
        "price": 1.07,
        "expertId": 42,  # magic → expertId
    }
    # None-valued optionals are omitted entirely.
    assert "stopLoss" not in sent and "comment" not in sent


@respx.mock
@pytest.mark.parametrize("op", ["BuyStopLimit", "SellStopLimit"])
def test_order_send_stop_limit_unsupported_on_mt4(op: str) -> None:
    # MT4 has no stop-limit order type; the bridge answers 400
    # MRPC_UNSUPPORTED, so the SDK refuses before sending.
    route = respx.post(f"{TERM}/OrderSend").mock(
        return_value=httpx.Response(200, json=_ORDER_OK),
    )
    with _term(platform="mt4") as t, pytest.raises(UnsupportedOnPlatformError):
        t.order_send(
            symbol="EURUSD",
            operation=op,
            volume=0.1,
            price=1.1,
            stop_limit_price=1.09,
        )
    assert not route.called


@respx.mock
def test_order_send_stop_limit_allowed_on_mt5() -> None:
    route = respx.post(f"{TERM}/OrderSend").mock(
        return_value=httpx.Response(200, json=_ORDER_OK),
    )
    with _term(platform="mt5") as t:
        t.order_send(
            symbol="EURUSD",
            operation="BuyStopLimit",
            volume=0.1,
            price=1.1,
            stop_limit_price=1.09,
        )
    sent = json.loads(route.calls.last.request.content)
    assert sent["operation"] == "BuyStopLimit"
    assert sent["stopLimitPrice"] == 1.09


@respx.mock
def test_order_modify_stop_limit_price_unsupported_on_mt4() -> None:
    # MT4 would silently ignore stopLimitPrice on modify.
    route = respx.post(f"{TERM}/OrderModify").mock(
        return_value=httpx.Response(200, json=_ORDER_OK),
    )
    with _term(platform="mt4") as t, pytest.raises(UnsupportedOnPlatformError):
        t.order_modify(100, stop_limit_price=1.09)
    assert not route.called
    # The rest of a modify still works on MT4.
    with _term(platform="mt4") as t:
        t.order_modify(100, price=1.1)
    assert route.called


@respx.mock
def test_order_modify_no_change_is_effective() -> None:
    # An idempotent re-send (SL/TP already match) returns retcode 10025 with
    # success=False; outcome distinguishes the benign no-op from a rejection,
    # and is_effective treats it as "requested state is in effect".
    respx.post(f"{TERM}/OrderModify").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": False,
                "outcome": "no_change",
                "retcode": 10025,
                "retcodeDescription": "No changes",
                "deal": 0,
                "order": 100,
                "volume": 0.1,
                "price": 0.0,
                "bid": 1.0849,
                "ask": 1.0851,
                "comment": "No changes",
            },
        ),
    )
    with _term() as t:
        res = t.order_modify(100, stop_loss=1.07)
    assert res.success is False
    assert res.outcome == "no_change"
    assert res.is_no_change is True
    assert res.is_effective is True


def test_order_result_outcome_absent_defaults_empty() -> None:
    # Bridges older than MT5 0.6.1 / MT4 0.5.1 omit `outcome` — it must default
    # to "" (backward-compatible), not fail validation.
    from fxsocket import OrderResult

    res = OrderResult.model_validate(_ORDER_OK)
    assert res.outcome == ""
    assert res.success is True
    assert res.is_effective is True


def test_order_send_rejects_nonpositive_volume() -> None:
    from fxsocket import ValidationError

    with _term() as t, pytest.raises(ValidationError):
        t.order_send(symbol="EURUSD", operation="Buy", volume=0)


def test_order_send_pending_requires_price() -> None:
    from fxsocket import ValidationError

    with _term() as t, pytest.raises(ValidationError, match="price is required"):
        t.order_send(symbol="EURUSD", operation="BuyLimit", volume=0.1)


def test_order_send_stop_limit_requires_stop_limit_price() -> None:
    from fxsocket import ValidationError

    with _term() as t, pytest.raises(ValidationError, match="stop_limit_price"):
        t.order_send(
            symbol="EURUSD", operation="BuyStopLimit", volume=0.1, price=1.1
        )


def test_order_modify_rejects_bare_zero_sl() -> None:
    # A literal 0.0 would silently REMOVE the stop-loss — must be explicit.
    from fxsocket import ValidationError

    with _term() as t, pytest.raises(ValidationError, match="clear_stop_loss"):
        t.order_modify(100, stop_loss=0.0)


@respx.mock
def test_order_modify_clear_sends_zero() -> None:
    route = respx.post(f"{TERM}/OrderModify").mock(
        return_value=httpx.Response(200, json=_ORDER_OK),
    )
    with _term() as t:
        t.order_modify(100, clear_stop_loss=True)
    assert json.loads(route.calls.last.request.content) == {
        "ticket": 100,
        "stopLoss": 0.0,
    }


def test_order_modify_clear_and_value_conflict() -> None:
    from fxsocket import ValidationError

    with _term() as t, pytest.raises(ValidationError):
        t.order_modify(100, stop_loss=1.0, clear_stop_loss=True)


@respx.mock
def test_order_send_504_maps_to_timeout() -> None:
    respx.post(f"{TERM}/OrderSend").mock(
        return_value=httpx.Response(
            504, json={"error": "MRPC_TIMEOUT", "message": "timed out", "command_id": 7}
        )
    )
    with _term() as t, pytest.raises(TerminalTimeoutError):
        t.order_send(symbol="EURUSD", operation="Buy", volume=0.1)


@respx.mock
def test_order_modify_omits_unset_fields() -> None:
    route = respx.post(f"{TERM}/OrderModify").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "retcode": 10009,
                "retcodeDescription": "Done",
                "deal": 0,
                "order": 100,
                "volume": 0.1,
                "price": 0.0,
                "bid": 0.0,
                "ask": 0.0,
                "comment": "",
            },
        )
    )
    with _term() as t:
        t.order_modify(100, take_profit=1.2)
    assert json.loads(route.calls.last.request.content) == {
        "ticket": 100,
        "takeProfit": 1.2,
    }


@respx.mock
def test_close_all_sends_filters_and_parses() -> None:
    route = respx.post(f"{TERM}/CloseAll").mock(
        return_value=httpx.Response(
            200,
            json={
                "requested": 2,
                "closed": 1,
                "failed": 1,
                "results": [
                    {
                        "ticket": 100,
                        "kind": "position",
                        "success": True,
                        "retcode": 10009,
                        "retcodeDescription": "Done",
                    },
                    {
                        "ticket": 101,
                        "kind": "pending",
                        "success": False,
                        "retcode": 10006,
                        "retcodeDescription": "Rejected",
                    },
                ],
            },
        )
    )
    with _term() as t:
        summary = t.close_all(symbol="EURUSD", magic=0, delete_pending=True)
    # magic=0 is a real filter (manual orders) and must be sent, not dropped.
    assert json.loads(route.calls.last.request.content) == {
        "symbol": "EURUSD",
        "magic": 0,
        "deletePending": True,
    }
    assert (summary.requested, summary.closed, summary.failed) == (2, 1, 1)
    assert [r.is_pending for r in summary.results] == [False, True]
    assert summary.results[1].retcode_description == "Rejected"


@respx.mock
def test_close_all_no_filters_sends_empty_json_body() -> None:
    # /CloseAll requires a JSON body; with no filters it must be exactly {}.
    route = respx.post(f"{TERM}/CloseAll").mock(
        return_value=httpx.Response(
            200, json={"requested": 0, "closed": 0, "failed": 0, "results": []}
        )
    )
    with _term() as t:
        summary = t.close_all()
    req = route.calls.last.request
    assert json.loads(req.content) == {}
    assert req.headers["content-type"] == "application/json"
    assert summary.requested == 0 and summary.results == []


@respx.mock
def test_close_all_504_maps_to_timeout() -> None:
    # The pass continues inside the terminal after a 504 — the SDK must
    # surface the timeout (never retry), so callers re-check /OpenedOrders.
    respx.post(f"{TERM}/CloseAll").mock(
        return_value=httpx.Response(
            504, json={"error": "MRPC_TIMEOUT", "message": "timed out", "command_id": 3}
        )
    )
    with _term() as t, pytest.raises(TerminalTimeoutError):
        t.close_all()


# --------------------------------------------------------------------------- #
# Health probes — 503 carries a body, not an error
# --------------------------------------------------------------------------- #


@respx.mock
def test_healthz_503_still_parses_body() -> None:
    respx.get(f"{TERM}/healthz").mock(
        return_value=httpx.Response(
            503,
            json={
                "status": "starting",
                "terminal": True,
                "broker": True,
                "account": False,
            },
        )
    )
    with _term() as t:
        checks = t.healthz()
    assert checks.status == "starting"
    assert checks.account is False


# --------------------------------------------------------------------------- #
# Client.terminal wiring
# --------------------------------------------------------------------------- #


@respx.mock
def test_client_terminal_resolves_and_caches() -> None:
    respx.get(f"{TERM}/symbols").mock(
        return_value=httpx.Response(200, json=["EURUSD", "XAUUSD"])
    )
    with Client(api_key="fxs_live_k") as fx:
        acct = _account(platform="mt4")
        term = fx.terminal(acct)
        assert isinstance(term, TerminalClient)
        assert term.platform is Platform.MT4
        assert fx.terminal(acct) is term  # cached per endpoint
        assert term.symbols() == ["EURUSD", "XAUUSD"]


def test_client_terminal_without_endpoint_raises() -> None:
    with Client(api_key="fxs_live_k") as fx:
        bridge = _account(rest_url="")
        with pytest.raises(TerminalNotReadyError):
            fx.terminal(bridge)


# --------------------------------------------------------------------------- #
# Coverage — remaining endpoints parse + send correctly
# --------------------------------------------------------------------------- #


@respx.mock
def test_account_info_parses_camelcase() -> None:
    respx.get(f"{TERM}/AccountInfo").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "Jane",
                "login": 1150125,
                "server": "VTMarkets-Demo",
                "company": "VT",
                "currency": "USD",
                "currencyDigits": 2,
                "leverage": 200,
                "type": "Demo",
                "marginMode": "Hedging",
                "marginSoMode": "Percent",
                "marginCallLevel": 100.0,
                "stopOutLevel": 50.0,
                "tradeAllowed": True,
                "tradeExpert": True,
                "limitOrders": 0,
                "fifoClose": False,
            },
        )
    )
    with _term() as t:
        info = t.account_info()
    assert info.margin_so_mode == "Percent"
    assert info.fifo_close is False
    assert info.currency_digits == 2


@respx.mock
def test_symbol_info_parses_camelcase() -> None:
    respx.get(f"{TERM}/SymbolInfo").mock(
        return_value=httpx.Response(
            200,
            json={
                "symbol": "EURUSD",
                "description": "Euro vs US Dollar",
                "digits": 5,
                "point": 0.00001,
                "tickSize": 0.00001,
                "tickValue": 1.0,
                "contractSize": 100000.0,
                "volumeMin": 0.01,
                "volumeMax": 500.0,
                "volumeStep": 0.01,
                "stopsLevel": 0,
                "freezeLevel": 0,
                "spread": 15,
                "tradeMode": "Full",
                "swapLong": -2.5,
                "swapShort": -2.3,
                "bid": 1.0849,
                "ask": 1.0851,
                "currencyBase": "EUR",
                "currencyProfit": "USD",
                "currencyMargin": "USD",
            },
        )
    )
    with _term() as t:
        si = t.symbol_info("EURUSD")
    assert si.tick_size == 0.00001
    assert si.volume_min == 0.01
    assert si.currency_base == "EUR"
    # Pods older than bridge 0.10 omit commissions/sessions — default empty.
    assert si.commissions == [] and si.sessions == []


def test_symbol_info_commissions_and_sessions_parse() -> None:
    from fxsocket import CommissionRule, TradingSession

    rule = CommissionRule.model_validate(
        {
            "currency": "USD",
            "rangeMode": "SYMBOL_COMMISSION_RANGE_VOLUME",
            "chargeMode": "SYMBOL_COMMISSION_CHARGE_INSTANT",
            "entryMode": "SYMBOL_COMMISSION_ENTRY_INOUT",
            "directionMode": "SYMBOL_COMMISSION_DIRECTION_BOTH",
            "profitMode": "SYMBOL_COMMISSION_PROFIT_ALL",
            "tiers": [
                {
                    "mode": "SYMBOL_COMMISSION_MONEY_DEPOSIT",
                    "volumeType": "SYMBOL_COMMISSION_VOLUME_TYPE_VOLUME",
                    "value": 3.5,
                    "minValue": 0.0,
                    "maxValue": 0.0,
                    "rangeFrom": 0.0,
                    "rangeTo": 1000000.0,
                    "currency": "USD",
                }
            ],
        }
    )
    assert rule.range_mode == "SYMBOL_COMMISSION_RANGE_VOLUME"
    assert rule.tiers[0].value == 3.5
    assert rule.tiers[0].range_to == 1000000.0

    # `from` is a Python keyword — the wire field maps to `from_`.
    session = TradingSession.model_validate(
        {"day": "FRIDAY", "from": "00:01", "to": "23:57"}
    )
    assert session.day == "FRIDAY"
    assert session.from_ == "00:01"
    assert session.to == "23:57"


@respx.mock
def test_server_timezone_parses() -> None:
    respx.get(f"{TERM}/ServerTimezone").mock(
        return_value=httpx.Response(
            200, json={"serverTime": "2026-06-22T18:00:00Z", "utcOffsetSeconds": 7200}
        )
    )
    with _term() as t:
        tz = t.server_timezone()
    assert tz.utc_offset_seconds == 7200
    assert isinstance(tz.server_time, str)


@respx.mock
def test_order_history_parses_position() -> None:
    # Second row mimics a pod older than bridge MT5 0.14 / MT4 0.13 (no
    # ``position`` yet) — must default to 0, not fail validation.
    respx.get(f"{TERM}/OrderHistory").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "ticket": 14541780,
                    "order": 13173527,
                    "position": 13173524,
                    "symbol": "EURUSD",
                    "type": "Sell",
                    "entry": "Out",
                    "volume": 0.01,
                    "price": 1.16635,
                    "profit": -0.02,
                    "commission": -0.04,
                    "swap": 0.0,
                    "magic": 0,
                    "comment": "",
                    "time": "2026-08-19T18:53:47.000Z",
                },
                {
                    "ticket": 14541777,
                    "order": 13173524,
                    "symbol": "EURUSD",
                    "type": "Buy",
                    "entry": "In",
                    "volume": 0.01,
                    "price": 1.16637,
                    "profit": 0.0,
                    "commission": -0.04,
                    "swap": 0.0,
                    "magic": 1000999,
                    "comment": "CN_9999_8888",
                    "time": "2026-08-19T18:53:41.000Z",
                },
            ],
        )
    )
    with _term() as t:
        rows = t.order_history()
    assert rows[0].position == 13173524
    assert rows[1].position == 0


@respx.mock
def test_position_history_sends_dates_and_parses() -> None:
    route = respx.get(f"{TERM}/PositionHistory").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "positionId": 5,
                    "symbol": "EURUSD",
                    "type": "Buy",
                    "volume": 0.1,
                    "openTime": "2026-06-22T15:00:00Z",
                    "openPrice": 1.08,
                    "closeTime": "2026-06-22T16:00:00Z",
                    "closePrice": 1.085,
                    "profit": 5.0,
                    "swap": 0.0,
                    "commission": -1.0,
                    "netProfit": 4.0,
                    "magic": 0,
                    "comment": "",
                }
            ],
        )
    )
    with _term() as t:
        rows = t.position_history(from_="2026-06-01", to="2026-06-23")
    assert rows[0].net_profit == 4.0
    params = route.calls.last.request.url.params
    assert params["from"] == "2026-06-01" and params["to"] == "2026-06-23"


@respx.mock
def test_calc_margin_and_profit() -> None:
    respx.get(f"{TERM}/OrderCalcMargin").mock(
        return_value=httpx.Response(
            200,
            json={
                "symbol": "EURUSD",
                "operation": "Buy",
                "volume": 1.0,
                "price": 1.08,
                "margin": 540.0,
                "currency": "USD",
            },
        )
    )
    respx.get(f"{TERM}/OrderCalcProfit").mock(
        return_value=httpx.Response(
            200,
            json={
                "symbol": "EURUSD",
                "operation": "Buy",
                "volume": 1.0,
                "priceOpen": 1.08,
                "priceClose": 1.09,
                "profit": 1000.0,
                "currency": "USD",
            },
        )
    )
    with _term() as t:
        assert t.calc_margin("EURUSD", "Buy", 1.0, 1.08).margin == 540.0
        assert t.calc_profit("EURUSD", "Buy", 1.0, 1.08, 1.09).profit == 1000.0


def test_calc_margin_rejects_nonpositive_volume() -> None:
    from fxsocket import ValidationError

    with _term() as t, pytest.raises(ValidationError):
        t.calc_margin("EURUSD", "Buy", 0.0, 1.08)


@respx.mock
def test_order_close_omits_unset_fields() -> None:
    route = respx.post(f"{TERM}/OrderClose").mock(
        return_value=httpx.Response(200, json=_ORDER_OK)
    )
    with _term() as t:
        t.order_close(100, volume=0.05)
    assert json.loads(route.calls.last.request.content) == {
        "ticket": 100,
        "volume": 0.05,
    }


@respx.mock
def test_livez_503_still_parses_body() -> None:
    respx.get(f"{TERM}/livez").mock(
        return_value=httpx.Response(
            503,
            json={
                "status": "down",
                "terminal": False,
                "broker": False,
                "account": False,
            },
        )
    )
    with _term() as t:
        assert t.livez().status == "down"


# --------------------------------------------------------------------------- #
# Async smoke
# --------------------------------------------------------------------------- #


@respx.mock
async def test_async_quote() -> None:
    respx.get(f"{TERM}/getQuote").mock(
        return_value=httpx.Response(
            200,
            json={
                "symbol": "EURUSD",
                "bid": 1.0849,
                "ask": 1.0851,
                "time": "2026-06-22T16:53:56Z",
                "last": 0.0,
                "volume": 0,
            },
        )
    )
    async with AsyncClient(api_key="fxs_live_k") as fx:
        term = fx.terminal(_account())
        assert isinstance(term, AsyncTerminalClient)
        q = await term.quote("EURUSD")
    assert q.ask == 1.0851
    assert isinstance(q.time, str)


@respx.mock
async def test_async_order_send_body_and_validation() -> None:
    route = respx.post(f"{TERM}/OrderSend").mock(
        return_value=httpx.Response(200, json=_ORDER_OK)
    )
    async with AsyncClient(api_key="fxs_live_k") as fx:
        term = fx.terminal(_account())
        res = await term.order_send(
            symbol="EURUSD", operation="sell", volume=0.2, magic=7
        )
        # Same client-side validation as the sync path.
        from fxsocket import ValidationError

        with pytest.raises(ValidationError):
            await term.order_send(symbol="EURUSD", operation="Buy", volume=-1)
    assert res.order == 100
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "symbol": "EURUSD",
        "operation": "Sell",
        "volume": 0.2,
        "expertId": 7,
    }


@respx.mock
async def test_async_close_all() -> None:
    route = respx.post(f"{TERM}/CloseAll").mock(
        return_value=httpx.Response(
            200,
            json={
                "requested": 1,
                "closed": 1,
                "failed": 0,
                "results": [
                    {
                        "ticket": 100,
                        "kind": "position",
                        "success": True,
                        "retcode": 10009,
                        "retcodeDescription": "Done",
                    }
                ],
            },
        )
    )
    async with AsyncClient(api_key="fxs_live_k") as fx:
        term = fx.terminal(_account())
        summary = await term.close_all(symbol="EURUSD")
    assert json.loads(route.calls.last.request.content) == {"symbol": "EURUSD"}
    assert summary.closed == 1 and summary.results[0].success


# --------------------------------------------------------------------------- #
# Bridge MT5 0.17.0 / MT4 0.15.0: pending orders, symbols, trade replies
# --------------------------------------------------------------------------- #

_PENDING = {
    "ticket": 555,
    "symbol": "EURUSD",
    "type": "BuyLimit",
    "volumeInitial": 0.2,
    "volume": 0.2,
    "price": 1.08,
    "stopLimitPrice": 0.0,
    "sl": 1.075,
    "tp": 1.09,
    "expirationMode": "specified",
    "expiration": "2026-11-03T19:00:00Z",
    "expirationUtc": "2026-11-03T17:00:00Z",
    "magic": 7,
    "comment": "copy",
    "time": "2026-10-30T10:00:00.123Z",
    "timeUtc": "2026-10-30T08:00:00.123Z",
}


@respx.mock
def test_pending_orders_parse() -> None:
    respx.get(f"{TERM}/PendingOrders").mock(
        return_value=httpx.Response(200, json=[_PENDING])
    )
    with _term() as t:
        rows = t.pending_orders()
    assert len(rows) == 1
    po = rows[0]
    assert po.ticket == 555 and po.volume_initial == 0.2
    assert po.expiration_mode == ExpirationMode.SPECIFIED
    assert po.expiration_utc == "2026-11-03T17:00:00Z"
    assert po.time_utc == "2026-10-30T08:00:00.123Z"


def test_pending_order_time_utc_absent_is_none() -> None:
    from fxsocket import PendingOrder

    row = {k: v for k, v in _PENDING.items() if k != "timeUtc"}
    assert PendingOrder.model_validate(row).time_utc is None


@respx.mock
def test_symbol_select_sends_list_and_parses() -> None:
    route = respx.post(f"{TERM}/SymbolSelect").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"symbol": "EURUSD", "selected": True, "quoting": True},
                {"symbol": "NOPE", "selected": False, "quoting": False},
            ],
        )
    )
    with _term() as t:
        rows = t.symbol_select(["EURUSD", "NOPE"])
    assert json.loads(route.calls.last.request.content) == {
        "symbols": ["EURUSD", "NOPE"]
    }
    assert rows[0].quoting is True and rows[1].selected is False


_SPEC = {
    "symbol": "EURUSD",
    "description": "Euro vs US Dollar",
    "digits": 5,
    "point": 0.00001,
    "tickSize": 0.00001,
    "tickValue": 1.0,
    "contractSize": 100000.0,
    "volumeMin": 0.01,
    "volumeMax": 100.0,
    "volumeStep": 0.01,
    "stopsLevel": 0,
    "freezeLevel": 0,
    "spread": 2,
    "tradeMode": "Full",
    "swapLong": -1.0,
    "swapShort": 0.5,
    "bid": 1.0849,
    "ask": 1.0851,
    "currencyBase": "EUR",
    "currencyProfit": "USD",
    "currencyMargin": "EUR",
    "orderModes": ["market", "limit", "stop", "stopLimit", "sl", "tp"],
    "expirationModes": ["gtc", "day", "specified", "specifiedDay"],
    "fillingModes": ["fok", "ioc"],
}


@respx.mock
def test_symbol_infos_bulk() -> None:
    route = respx.get(f"{TERM}/SymbolInfo").mock(
        return_value=httpx.Response(
            200,
            json={
                "symbols": [_SPEC],
                "errors": [
                    {
                        "symbol": "NOPE",
                        "error": "MRPC_UNKNOWN_SYMBOL",
                        "message": "unknown symbol",
                    }
                ],
            },
        )
    )
    with _term() as t:
        batch = t.symbol_infos(["EURUSD", "NOPE"])
    assert route.calls.last.request.url.params["symbols"] == "EURUSD,NOPE"
    assert "symbol" not in route.calls.last.request.url.params
    assert batch.symbols[0].filling_modes == ["fok", "ioc"]
    assert "stopLimit" in batch.symbols[0].order_modes
    assert batch.errors[0].symbol == "NOPE"
    assert batch.errors[0].error == "MRPC_UNKNOWN_SYMBOL"


def test_symbol_info_modes_default_empty() -> None:
    from fxsocket import SymbolInfo

    spec = {
        k: v
        for k, v in _SPEC.items()
        if k not in ("orderModes", "expirationModes", "fillingModes")
    }
    info = SymbolInfo.model_validate(spec)
    assert info.order_modes == [] and info.expiration_modes == []
    assert info.filling_modes == []


@pytest.mark.parametrize(
    "symbols",
    [[], "EURUSD", ["EURUSD", ""], ["EUR,USD"], [f"S{i}" for i in range(101)]],
)
def test_symbol_lists_validated(symbols: object) -> None:
    with _term() as t:
        with pytest.raises(ValidationError):
            t.symbol_select(symbols)  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            t.symbol_infos(symbols)  # type: ignore[arg-type]


@respx.mock
def test_symbol_lists_accept_100() -> None:
    respx.post(f"{TERM}/SymbolSelect").mock(
        return_value=httpx.Response(200, json=[])
    )
    with _term() as t:
        assert t.symbol_select([f"S{i}" for i in range(100)]) == []


@respx.mock
def test_order_send_client_order_id_and_reply_fields() -> None:
    reply = {
        **_ORDER_OK,
        "deal": 900,
        "position": 100,
        "time": "2026-10-30T10:00:00.123Z",
        "timeUtc": "2026-10-30T08:00:00.123Z",
        "remainderTicket": 0,
        "clientOrderId": "copy-1",
    }
    route = respx.post(f"{TERM}/OrderSend").mock(
        return_value=httpx.Response(
            200,
            json=reply,
            headers={
                "Idempotent-Replayed": "true",
                "Server-Timing": "queue;dur=1.5, exec;dur=40.25",
            },
        )
    )
    with _term() as t:
        res = t.order_send(
            symbol="EURUSD", operation="Buy", volume=0.1, client_order_id="copy-1"
        )
    sent = json.loads(route.calls.last.request.content)
    assert sent["clientOrderId"] == "copy-1"
    assert res.position == 100
    assert res.time_utc == "2026-10-30T08:00:00.123Z"
    assert res.client_order_id == "copy-1"
    assert res.idempotent_replayed is True
    assert res.server_timing is not None
    assert res.server_timing.queue_ms == 1.5
    assert res.server_timing.exec_ms == 40.25


@respx.mock
def test_trade_reply_without_new_headers_defaults() -> None:
    respx.post(f"{TERM}/OrderClose").mock(
        return_value=httpx.Response(200, json=_ORDER_OK)
    )
    with _term() as t:
        res = t.order_close(100)
    assert res.idempotent_replayed is False
    assert res.server_timing is None
    assert res.position == 0 and res.time == "" and res.time_utc is None
    assert res.remainder_ticket == 0 and res.client_order_id is None


@respx.mock
def test_order_close_reply_remainder_ticket_mt4() -> None:
    respx.post(f"{TERM}/OrderClose").mock(
        return_value=httpx.Response(
            200,
            json={**_ORDER_OK, "position": 100, "remainderTicket": 101},
            headers={"idempotent-replayed": "TRUE", "server-timing": "exec;dur=12"},
        )
    )
    with _term(platform="mt4") as t:
        res = t.order_close(100, volume=0.05)
    assert res.remainder_ticket == 101
    assert res.idempotent_replayed is True
    assert res.server_timing is not None
    assert res.server_timing.queue_ms is None and res.server_timing.exec_ms == 12.0


@pytest.mark.parametrize(
    "header,queue,exec_",
    [
        ("queue;dur=1.2, exec;dur=40.3", 1.2, 40.3),
        ('exec;desc="x";dur=7, queue;dur="0.5"', 0.5, 7.0),
        ("queue;dur=abc, exec;dur=3", None, 3.0),
    ],
)
def test_server_timing_parse(header: str, queue: float | None, exec_: float) -> None:
    from fxsocket import ServerTiming

    st = ServerTiming.from_header(header)
    assert st is not None
    assert st.queue_ms == queue and st.exec_ms == exec_


@pytest.mark.parametrize("header", [None, "", "db;dur=3", "queue"])
def test_server_timing_parse_nothing(header: str | None) -> None:
    from fxsocket import ServerTiming

    assert ServerTiming.from_header(header) is None


@pytest.mark.parametrize("bad", ["", "x" * 65, 123])
def test_order_send_client_order_id_validated(bad: object) -> None:
    with _term() as t, pytest.raises(ValidationError):
        t.order_send(
            symbol="EURUSD",
            operation="Buy",
            volume=0.1,
            client_order_id=bad,  # type: ignore[arg-type]
        )


@respx.mock
def test_order_send_client_order_id_64_chars_ok() -> None:
    route = respx.post(f"{TERM}/OrderSend").mock(
        return_value=httpx.Response(200, json=_ORDER_OK)
    )
    with _term() as t:
        t.order_send(
            symbol="EURUSD", operation="Buy", volume=0.1, client_order_id="x" * 64
        )
    assert json.loads(route.calls.last.request.content)["clientOrderId"] == "x" * 64


@respx.mock
@pytest.mark.parametrize(
    "value,expected",
    [
        (
            datetime(2026, 11, 3, 19, 0, 0, tzinfo=timezone(timedelta(hours=2))),
            "2026-11-03T17:00:00Z",
        ),
        (datetime(2026, 11, 3, 17, 0, 0, tzinfo=timezone.utc), "2026-11-03T17:00:00Z"),
        (datetime(2026, 11, 3, 17, 0, 0, 500000), "2026-11-03T17:00:00Z"),  # naive
        (date(2026, 11, 3), "2026-11-03T00:00:00Z"),
        ("2026-11-03T17:00:00Z", "2026-11-03T17:00:00Z"),
    ],
)
def test_order_send_expiration_utc_formatting(value: object, expected: str) -> None:
    route = respx.post(f"{TERM}/OrderSend").mock(
        return_value=httpx.Response(200, json=_ORDER_OK)
    )
    with _term() as t:
        t.order_send(
            symbol="EURUSD",
            operation="BuyLimit",
            volume=0.1,
            price=1.08,
            expiration_utc=value,  # type: ignore[arg-type]
        )
    sent = json.loads(route.calls.last.request.content)
    assert sent["expirationUtc"] == expected
    assert "expiration" not in sent


@respx.mock
def test_order_modify_expiration_utc() -> None:
    route = respx.post(f"{TERM}/OrderModify").mock(
        return_value=httpx.Response(200, json=_ORDER_OK)
    )
    with _term() as t:
        t.order_modify(
            555, expiration_utc=datetime(2026, 11, 3, 17, 0, tzinfo=timezone.utc)
        )
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"ticket": 555, "expirationUtc": "2026-11-03T17:00:00Z"}


def test_expiration_and_expiration_utc_are_exclusive() -> None:
    with _term() as t:
        with pytest.raises(ValidationError, match="not both"):
            t.order_send(
                symbol="EURUSD",
                operation="BuyLimit",
                volume=0.1,
                price=1.08,
                expiration="2026-11-03T19:00:00",
                expiration_utc="2026-11-03T17:00:00Z",
            )
        with pytest.raises(ValidationError, match="not both"):
            t.order_modify(
                555,
                expiration="2026-11-03T19:00:00",
                expiration_utc="2026-11-03T17:00:00Z",
            )


@respx.mock
@pytest.mark.parametrize(
    "code,exc",
    [
        ("MRPC_TRADE_OUTCOME_UNKNOWN", TradeOutcomeUnknownError),
        ("MRPC_ABANDONED", TradeAbandonedError),
    ],
)
def test_order_send_504_split(code: str, exc: type[Exception]) -> None:
    respx.post(f"{TERM}/OrderSend").mock(
        return_value=httpx.Response(
            504, json={"error": code, "message": "m", "commandId": 9}
        )
    )
    with _term() as t, pytest.raises(exc) as info:
        t.order_send(symbol="EURUSD", operation="Buy", volume=0.1)
    # Still a TerminalTimeoutError, so existing handlers keep catching it.
    assert isinstance(info.value, TerminalTimeoutError)


@respx.mock
def test_order_send_409_client_order_id_reused() -> None:
    respx.post(f"{TERM}/OrderSend").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": "MRPC_CLIENT_ORDER_ID_REUSED",
                "message": "this clientOrderId was already used for a different order",
                "commandId": 0,
            },
        )
    )
    with _term() as t, pytest.raises(ClientOrderIdReusedError):
        t.order_send(
            symbol="EURUSD", operation="Buy", volume=0.1, client_order_id="dup"
        )


def test_status_trade_pipeline_fields() -> None:
    from fxsocket.models import BridgeHealth

    b = BridgeHealth.model_validate(
        {
            "version": "0.17.0",
            "tradeEaReady": True,
            "tradeEaHeartbeatAgeMs": 3,
            "symbolsSynced": True,
            "asyncTrades": True,
            "tradeInFlight": 2,
            "oldestTradeInFlightAgeMs": 180,
            "dealerRttMsEwma": 95,
            "tradeRequestsSent": 41,
            "lateTradeVerdicts": 0,
            "tradeQueueDepth": 1,
            "nonTradeQueueDepth": 0,
            "tradeDeferred": 1,
        }
    )
    assert b.async_trades is True and b.trade_in_flight == 2
    assert b.oldest_trade_in_flight_age_ms == 180 and b.dealer_rtt_ms_ewma == 95
    assert b.trade_requests_sent == 41 and b.trade_deferred == 1
    old = BridgeHealth.model_validate({"version": "0.14.0"})
    assert old.oldest_trade_in_flight_age_ms == -1 and old.dealer_rtt_ms_ewma == -1
    assert old.async_trades is False and old.trade_queue_depth == 0


@respx.mock
async def test_async_new_endpoints() -> None:
    respx.get(f"{TERM}/PendingOrders").mock(
        return_value=httpx.Response(200, json=[_PENDING])
    )
    respx.post(f"{TERM}/SymbolSelect").mock(
        return_value=httpx.Response(
            200, json=[{"symbol": "EURUSD", "selected": True, "quoting": True}]
        )
    )
    respx.get(f"{TERM}/SymbolInfo").mock(
        return_value=httpx.Response(200, json={"symbols": [_SPEC], "errors": []})
    )
    send = respx.post(f"{TERM}/OrderSend").mock(
        return_value=httpx.Response(
            200,
            json={**_ORDER_OK, "clientOrderId": "a"},
            headers={"Idempotent-Replayed": "true"},
        )
    )
    async with AsyncClient(api_key="fxs_live_k") as fx:
        term = fx.terminal(_account())
        assert (await term.pending_orders())[0].ticket == 555
        assert (await term.symbol_select(["EURUSD"]))[0].quoting is True
        assert (await term.symbol_infos(["EURUSD"])).symbols[0].symbol == "EURUSD"
        res = await term.order_send(
            symbol="EURUSD",
            operation="BuyLimit",
            volume=0.1,
            price=1.08,
            expiration_utc=datetime(2026, 11, 3, 17, 0, tzinfo=timezone.utc),
            client_order_id="a",
        )
        with pytest.raises(ValidationError):
            await term.symbol_select([])
    assert res.idempotent_replayed is True
    sent = json.loads(send.calls.last.request.content)
    assert sent["clientOrderId"] == "a"
    assert sent["expirationUtc"] == "2026-11-03T17:00:00Z"


async def test_async_mt4_stop_limit_guard() -> None:
    term = AsyncTerminalClient(base_url=TERM, api_key="k", platform="mt4")
    try:
        with pytest.raises(UnsupportedOnPlatformError):
            await term.order_send(
                symbol="EURUSD",
                operation="SellStopLimit",
                volume=0.1,
                price=1.1,
                stop_limit_price=1.11,
            )
        with pytest.raises(UnsupportedOnPlatformError):
            await term.order_modify(1, stop_limit_price=1.1)
    finally:
        await term.aclose()
