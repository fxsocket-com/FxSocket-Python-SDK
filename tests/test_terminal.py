"""Tests for the terminal REST client (sync + async), respx-mocked."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from fxsocket import (
    AsyncClient,
    Client,
    OrderOperation,
    Platform,
    TerminalNotReadyError,
    TerminalTimeoutError,
    Timeframe,
    UnsupportedOnPlatformError,
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


def test_price_history_rejects_mt5_timeframe_on_mt4() -> None:
    with _term(platform="mt4") as t, pytest.raises(UnsupportedOnPlatformError):
        t.price_history("EURUSD", "H6")


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
def test_order_send_allows_stop_limit_on_mt4() -> None:
    # MT4's terminal API accepts BuyStopLimit/SellStopLimit (parse_operation
    # maps them), so the SDK must NOT block them client-side.
    route = respx.post(f"{TERM}/OrderSend").mock(
        return_value=httpx.Response(200, json=_ORDER_OK),
    )
    with _term(platform="mt4") as t:
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
