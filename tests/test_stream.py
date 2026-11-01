"""Tests for WebSocket streaming (async + sync) against a real local server."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from websockets.asyncio.server import serve

from fxsocket import (
    AccountUpdate,
    AsyncStream,
    Bar,
    DealEntry,
    ExpirationMode,
    ModifyUpdate,
    OrderEventKind,
    OrdersUpdate,
    OrderUpdate,
    PositionsUpdate,
    Replayed,
    Stream,
    StreamWarning,
    Subscribed,
    TerminalUpdate,
    Tick,
    TradeUpdate,
    ValidationError,
)
from fxsocket.terminal.stream import (
    StreamErrorEvent,
    Subscriptions,
    Unsubscribed,
    _ssl_context,
    _with_api_key,
    parse_event,
)


def test_ssl_context_selection() -> None:
    import ssl as _ssl

    # ws:// — no TLS (websockets requires ssl=None here)
    assert _ssl_context("ws://h/ws", verify=True) is None
    # wss:// + verify must be an explicit verifying context (not None)
    verifying = _ssl_context("wss://h/ws", verify=True)
    assert isinstance(verifying, _ssl.SSLContext)
    assert verifying.verify_mode == _ssl.CERT_REQUIRED
    # wss:// + verify=False — skip verification (self-signed droplet)
    insecure = _ssl_context("wss://h/ws", verify=False)
    assert isinstance(insecure, _ssl.SSLContext)
    assert insecure.verify_mode == _ssl.CERT_NONE


def test_with_api_key_sets_query() -> None:
    url = _with_api_key("wss://api.fxsocket.com/mt5/abc/ws", "fxs_live_x")
    assert url == "wss://api.fxsocket.com/mt5/abc/ws?api_key=fxs_live_x"
    # preserves existing query params
    url2 = _with_api_key("wss://h/ws?foo=1", "k")
    assert "foo=1" in url2 and "api_key=k" in url2

_TICK = {
    "type": "tick",
    "symbol": "EURUSD",
    "data": {
        "symbol": "EURUSD",
        "bid": 1.0849,
        "ask": 1.0851,
        "time": "2026-06-23T12:00:00Z",
        "last": 0.0,
        "volume": 0,
    },
}


async def _start_server(handler: Any) -> tuple[Any, str]:
    server = await serve(handler, "localhost", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://localhost:{port}/ws"


# --------------------------------------------------------------------------- #
# parse_event — every message type, pure function
# --------------------------------------------------------------------------- #


def test_parse_event_all_types() -> None:
    assert isinstance(parse_event(_TICK), Tick)
    bar = parse_event(
        {
            "type": "bar",
            "symbol": "EURUSD",
            "timeframe": "M5",
            "data": {
                "time": "t",
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.05,
                "tickVolume": 5,
                "realVolume": 0,
                "spread": 2,
            },
        }
    )
    assert isinstance(bar, Bar) and bar.timeframe == "M5" and bar.data.tick_volume == 5

    acct = parse_event(
        {
            "type": "account",
            "data": {
                "balance": 100.0,
                "credit": 0.0,
                "profit": 1.0,
                "equity": 101.0,
                "margin": 0.0,
                "freeMargin": 101.0,
                "marginLevel": 0.0,
                "leverage": 100,
                "currency": "USD",
                "type": "Demo",
            },
        }
    )
    assert isinstance(acct, AccountUpdate) and acct.data.free_margin == 101.0

    pos = parse_event(
        {
            "type": "positions",
            "data": [
                {
                    "ticket": 1,
                    "symbol": "EURUSD",
                    "type": "Buy",
                    "kind": "Position",
                    "lots": 0.1,
                    "openPrice": 1.0,
                    "currentPrice": 1.0,
                    "stopLoss": 0.0,
                    "takeProfit": 0.0,
                    "swap": 0.0,
                    "profit": 0.0,
                    "magic": 0,
                    "comment": "",
                    "openTime": "t",
                }
            ],
        }
    )
    assert isinstance(pos, PositionsUpdate) and pos.data[0].ticket == 1

    trade = parse_event(
        {
            "type": "trade",
            "data": {
                "deal": 9,
                "order": 10,
                "position": 10,
                "symbol": "EURUSD",
                "type": "Buy",
                "entry": "In",
                "volume": 0.1,
                "price": 1.0,
                "profit": 0.0,
                "commission": -0.04,
                "swap": 0.0,
                "magic": 1000999,
                "comment": "CN_9999_8888",
                "time": "t",
                "degraded": False,
            },
        }
    )
    assert isinstance(trade, TradeUpdate) and trade.data.entry == "In"
    assert trade.data.commission == -0.04 and trade.data.magic == 1000999
    assert trade.data.degraded is False
    assert trade.data.net_profit == pytest.approx(-0.04)

    term = parse_event(
        {
            "type": "terminal",
            "data": {
                "connected": True,
                "tradeAllowed": True,
                "serverTime": "t",
            },
        }
    )
    assert isinstance(term, TerminalUpdate) and term.data.trade_allowed is True

    assert parse_event({"type": "warning", "dropped": 7}) == StreamWarning(dropped=7)
    assert isinstance(
        parse_event({"type": "subscribed", "topic": "prices", "message": ""}),
        Subscribed,
    )
    assert isinstance(
        parse_event({"type": "unsubscribed", "topic": "prices", "message": ""}),
        Unsubscribed,
    )
    assert isinstance(
        parse_event({"type": "error", "topic": "bars", "message": "bad tf"}),
        StreamErrorEvent,
    )
    assert isinstance(parse_event({"type": "subscriptions", "data": []}), Subscriptions)


def test_parse_trade_pre_012_bridge_defaults() -> None:
    """Bridges older than MT5 0.12 / MT4 0.11 omit the enrichment fields."""
    trade = parse_event(
        {
            "type": "trade",
            "data": {
                "deal": 9,
                "order": 10,
                "position": 10,
                "symbol": "EURUSD",
                "type": "Buy",
                "entry": "In",
                "volume": 0.1,
                "price": 1.0,
                "profit": 0.0,
                "comment": "",
                "time": "t",
            },
        }
    )
    assert isinstance(trade, TradeUpdate)
    assert trade.data.commission == 0.0 and trade.data.swap == 0.0
    assert trade.data.magic == 0 and trade.data.degraded is False


def test_parse_trade_degraded_frame() -> None:
    """A degraded frame: identifiers/direction real, costs zeroed, entry Unknown."""
    trade = parse_event(
        {
            "type": "trade",
            "data": {
                "deal": 9,
                "order": 10,
                "position": 10,
                "symbol": "EURUSD",
                "type": "Buy",
                "entry": "Unknown",
                "volume": 0.1,
                "price": 1.0,
                "profit": 0.0,
                "commission": 0.0,
                "swap": 0.0,
                "magic": 0,
                "comment": "",
                "time": "2026-08-19T18:53:41.000Z",
                "degraded": True,
            },
        }
    )
    assert isinstance(trade, TradeUpdate)
    assert trade.data.degraded is True
    assert trade.data.entry == DealEntry.UNKNOWN
    assert trade.data.symbol == "EURUSD" and trade.data.position == 10


# --------------------------------------------------------------------------- #
# Validation (no connection needed — raised before send)
# --------------------------------------------------------------------------- #


async def test_bars_requires_timeframe() -> None:
    from fxsocket import ValidationError

    s = AsyncStream(ws_url="ws://x/ws", api_key="k", platform="mt5")
    with pytest.raises(ValidationError):
        await s.subscribe("bars", symbol="EURUSD")


@pytest.mark.parametrize("platform", ["mt4", "mt5"])
async def test_bars_unsupported_timeframe_rejected(platform: str) -> None:
    from fxsocket import ValidationError

    s = AsyncStream(ws_url="ws://x/ws", api_key="k", platform=platform)
    with pytest.raises(ValidationError):
        await s.subscribe_bars("EURUSD", "H6")


async def test_prices_requires_symbol() -> None:
    from fxsocket import ValidationError

    s = AsyncStream(ws_url="ws://x/ws", api_key="k", platform="mt5")
    with pytest.raises(ValidationError):
        await s.subscribe("prices")


# --------------------------------------------------------------------------- #
# Async stream end-to-end against a local server
# --------------------------------------------------------------------------- #


async def test_async_subscribe_sends_payload_and_yields_tick() -> None:
    received: list[dict] = []

    async def handler(ws: Any) -> None:
        try:
            received.append(json.loads(await ws.recv()))
            await ws.send(
                json.dumps({"type": "subscribed", "topic": "prices", "message": ""})
            )
            await ws.send(json.dumps(_TICK))
            await ws.recv()  # block until client closes
        except Exception:
            pass

    server, url = await _start_server(handler)
    try:
        events: list[Any] = []
        async with AsyncStream(
            ws_url=url, api_key="secret", platform="mt5", auto_reconnect=False
        ) as s:
            await s.subscribe_prices("EURUSD")
            async for ev in s:
                events.append(ev)
                if isinstance(ev, Tick):
                    break
    finally:
        server.close()
        await server.wait_closed()

    # api_key went on the query string; subscribe payload is exact.
    assert received[0] == {"action": "subscribe", "topic": "prices", "symbol": "EURUSD"}
    ticks = [e for e in events if isinstance(e, Tick)]
    assert ticks and ticks[0].data.ask == 1.0851


async def test_aclose_stops_iteration_even_with_autoreconnect() -> None:
    # aclose() must end the async-for cleanly, not trigger a reconnect.
    async def handler(ws: Any) -> None:
        try:
            await ws.recv()
            await ws.send(json.dumps(_TICK))
            await ws.recv()
        except Exception:
            pass

    server, url = await _start_server(handler)
    try:
        s = AsyncStream(
            ws_url=url, api_key="k", platform="mt5", auto_reconnect=True
        )
        await s.connect()
        await s.subscribe_prices("EURUSD")
        events: list[Any] = []

        async def consume() -> None:
            async for ev in s:
                events.append(ev)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.3)
        await s.aclose()
        # If aclose reconnected instead of stopping, this would time out.
        await asyncio.wait_for(task, timeout=3.0)
    finally:
        server.close()
        await server.wait_closed()
    assert any(isinstance(e, Tick) for e in events)


async def test_async_reconnect_replays_subscriptions() -> None:
    subs: list[dict] = []
    conn_count = {"n": 0}

    async def handler(ws: Any) -> None:
        conn_count["n"] += 1
        n = conn_count["n"]
        try:
            subs.append(json.loads(await ws.recv()))
            await ws.send(json.dumps(_TICK))
            if n == 1:
                await ws.close()  # force a reconnect
            else:
                await ws.recv()
        except Exception:
            pass

    server, url = await _start_server(handler)
    try:
        ticks = 0
        async with AsyncStream(
            ws_url=url,
            api_key="k",
            platform="mt5",
            auto_reconnect=True,
            max_reconnect_attempts=3,
        ) as s:
            await s.subscribe_prices("EURUSD")
            async for ev in s:
                if isinstance(ev, Tick):
                    ticks += 1
                    if ticks == 2:
                        break
    finally:
        server.close()
        await server.wait_closed()

    # The subscription was replayed on the second connection.
    expected = {"action": "subscribe", "topic": "prices", "symbol": "EURUSD"}
    assert len(subs) == 2
    assert all(s == expected for s in subs)


async def test_reconnect_survives_replay_failure() -> None:
    # Critical: if the freshly reconnected socket drops *during* subscription
    # replay, _reconnect must retry — not let the exception kill iteration.
    conns = {"n": 0}
    subs: list[dict] = []

    async def handler(ws: Any) -> None:
        conns["n"] += 1
        n = conns["n"]
        try:
            if n == 1:
                subs.append(json.loads(await ws.recv()))
                await ws.send(json.dumps(_TICK))
                await ws.close()  # drop -> client reconnects
            elif n == 2:
                await ws.close()  # drop immediately -> replay send fails
            else:
                subs.append(json.loads(await ws.recv()))  # replay succeeds
                await ws.send(json.dumps(_TICK))
                await ws.recv()
        except Exception:
            pass

    server, url = await _start_server(handler)
    try:
        ticks = 0
        async with AsyncStream(
            ws_url=url, api_key="k", platform="mt5",
            auto_reconnect=True, max_reconnect_attempts=5,
        ) as s:
            await s.subscribe_prices("EURUSD")
            async for ev in s:
                if isinstance(ev, Tick):
                    ticks += 1
                    if ticks == 2:
                        break
    finally:
        server.close()
        await server.wait_closed()

    assert ticks == 2  # recovered across a mid-replay drop
    assert conns["n"] >= 3


@pytest.mark.parametrize("tf", ["M5", "5min"])
async def test_subscribe_bars_sends_canonical_timeframe(tf: str) -> None:
    received: list[dict] = []

    async def handler(ws: Any) -> None:
        try:
            received.append(json.loads(await ws.recv()))
            await ws.recv()
        except Exception:
            pass

    server, url = await _start_server(handler)
    try:
        async with AsyncStream(ws_url=url, api_key="k", platform="mt5") as s:
            await s.subscribe_bars("EURUSD", tf)
            await asyncio.sleep(0.1)
    finally:
        server.close()
        await server.wait_closed()
    assert received[0] == {
        "action": "subscribe",
        "topic": "bars",
        "symbol": "EURUSD",
        "timeframe": "M5",
    }


async def test_unsubscribe_sends_payload() -> None:
    received: list[dict] = []

    async def handler(ws: Any) -> None:
        try:
            for _ in range(2):
                received.append(json.loads(await ws.recv()))
            await ws.recv()
        except Exception:
            pass

    server, url = await _start_server(handler)
    try:
        async with AsyncStream(ws_url=url, api_key="k", platform="mt5") as s:
            await s.subscribe_prices("EURUSD")
            await s.unsubscribe_prices("EURUSD")
            await asyncio.sleep(0.1)
    finally:
        server.close()
        await server.wait_closed()
    assert received[1] == {
        "action": "unsubscribe",
        "topic": "prices",
        "symbol": "EURUSD",
    }


async def test_data_events_and_warning_from_socket() -> None:
    frames = [
        {"type": "warning", "dropped": 42},
        {"type": "account", "data": {
            "balance": 1.0, "credit": 0.0, "profit": 0.0, "equity": 1.0,
            "margin": 0.0, "freeMargin": 1.0, "marginLevel": 0.0,
            "leverage": 100, "currency": "USD", "type": "Demo"}},
        {"type": "terminal", "data": {
            "connected": True, "tradeAllowed": True, "serverTime": "t"}},
        {"type": "subscriptions", "data": [{"topic": "account"}]},
    ]

    async def handler(ws: Any) -> None:
        try:
            await ws.recv()
            for f in frames:
                await ws.send(json.dumps(f))
            await ws.recv()
        except Exception:
            pass

    server, url = await _start_server(handler)
    got: list[Any] = []
    try:
        async with AsyncStream(ws_url=url, api_key="k", platform="mt5") as s:
            await s.subscribe_account()
            await s.list_subscriptions()

            async def consume() -> None:
                async for ev in s:
                    got.append(ev)
                    if len(got) >= len(frames):
                        return

            await asyncio.wait_for(consume(), timeout=5)
    finally:
        server.close()
        await server.wait_closed()
    types = {type(e).__name__ for e in got}
    expected = {"StreamWarning", "AccountUpdate", "TerminalUpdate", "Subscriptions"}
    assert expected <= types
    assert any(isinstance(e, StreamWarning) and e.dropped == 42 for e in got)


def test_sync_iterate_before_connect_raises() -> None:
    from fxsocket.terminal.stream import AsyncStream as _AS

    s = Stream(lambda: _AS(ws_url="ws://x/ws", api_key="k", platform="mt5"))
    try:
        with pytest.raises(RuntimeError):
            for _ in s:  # never connected -> would deadlock without the guard
                break
    finally:
        s.close()


def test_double_close_is_safe() -> None:
    from fxsocket.terminal.stream import AsyncStream as _AS

    s = Stream(lambda: _AS(ws_url="ws://x/ws", api_key="k", platform="mt5"))
    s.close()
    s.close()  # idempotent — must not raise


# --------------------------------------------------------------------------- #
# Sync wrapper against the same server (driven from a worker thread)
# --------------------------------------------------------------------------- #


async def test_sync_stream_smoke() -> None:
    async def handler(ws: Any) -> None:
        try:
            await ws.recv()
            await ws.send(json.dumps(_TICK))
            await ws.recv()
        except Exception:
            pass

    server, url = await _start_server(handler)

    def run_sync() -> list[Any]:
        def factory() -> AsyncStream:
            return AsyncStream(
                ws_url=url, api_key="k", platform="mt5", auto_reconnect=False
            )

        out: list[Any] = []
        with Stream(factory) as s:
            s.subscribe_prices("EURUSD")
            for ev in s:
                out.append(ev)
                if isinstance(ev, Tick):
                    break
        return out

    try:
        events = await asyncio.get_running_loop().run_in_executor(None, run_sync)
    finally:
        server.close()
        await server.wait_closed()

    assert any(isinstance(e, Tick) for e in events)


# --------------------------------------------------------------------------- #
# Bridge MT5 0.17.0 / MT4 0.15.0: envelope, new frames, orders, resume
# --------------------------------------------------------------------------- #


def _trade_frame(
    deal: int,
    time: str,
    *,
    order: int = 0,
    entry: str = "In",
    replay: bool = False,
    seq: int | None = None,
) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "type": "trade",
        "ts": 1761811200000,
        "data": {
            "deal": deal,
            "order": order or deal,
            "position": order or deal,
            "symbol": "EURUSD",
            "type": "Buy",
            "entry": entry,
            "volume": 0.1,
            "price": 1.0,
            "profit": 0.0,
            "commission": 0.0,
            "swap": 0.0,
            "magic": 0,
            "comment": "",
            "time": time,
            "degraded": False,
        },
    }
    if replay:
        frame["replay"] = True
    if seq is not None:
        frame["seq"] = seq
    return frame


def test_parse_envelope_and_snapshot() -> None:
    tick = parse_event({**_TICK, "seq": 41, "ts": 1761811200123})
    assert isinstance(tick, Tick)
    assert tick.seq == 41 and tick.ts == 1761811200123 and tick.snapshot is False
    snap = parse_event({**_TICK, "snapshot": True, "ts": 5})
    assert isinstance(snap, Tick) and snap.snapshot is True and snap.seq is None
    # Older bridges send no envelope.
    old = parse_event(_TICK)
    assert isinstance(old, Tick) and old.seq is None and old.ts is None


def test_parse_trade_new_fields_and_replay_flag() -> None:
    frame = _trade_frame(9, "2026-10-30T10:00:00.123Z", replay=True)
    frame["data"].update(
        {
            "sl": 1.07,
            "tp": 1.09,
            "positionVolume": 0.1,
            "timeUtc": "2026-10-30T08:00:00.123Z",
            "remainderTicket": 0,
            "remainderOf": 0,
        }
    )
    ev = parse_event(frame)
    assert isinstance(ev, TradeUpdate) and ev.replay is True and ev.seq is None
    assert ev.data.sl == 1.07 and ev.data.tp == 1.09
    assert ev.data.position_volume == 0.1
    assert ev.data.time_utc == "2026-10-30T08:00:00.123Z"
    # Defaults on bridges before MT5 0.17.0 / MT4 0.15.0.
    old = parse_event(_trade_frame(9, "t"))
    assert isinstance(old, TradeUpdate) and old.replay is False
    assert old.data.position_volume == -1.0 and old.data.time_utc is None
    assert old.data.sl == 0.0 and old.data.remainder_of == 0


def test_parse_mt4_partial_close_links() -> None:
    out = parse_event(_trade_frame(0, "t", order=100, entry="Out"))
    assert isinstance(out, TradeUpdate)
    frame = _trade_frame(0, "t", order=100, entry="Out")
    frame["data"]["remainderTicket"] = 101
    out = parse_event(frame)
    assert isinstance(out, TradeUpdate) and out.data.remainder_ticket == 101
    frame = _trade_frame(0, "t", order=101, entry="In")
    frame["data"]["remainderOf"] = 100
    rem = parse_event(frame)
    assert isinstance(rem, TradeUpdate) and rem.data.remainder_of == 100


def test_parse_modify_order_orders_replayed() -> None:
    modify = parse_event(
        {
            "type": "modify",
            "seq": 12,
            "ts": 1,
            "data": {
                "position": 100,
                "symbol": "EURUSD",
                "type": "Buy",
                "volume": 0.1,
                "price": 1.08,
                "sl": 1.07,
                "tp": 1.1,
                "magic": 7,
                "comment": "",
                "time": "2026-10-30T10:00:01Z",
                "timeUtc": "2026-10-30T08:00:01Z",
            },
        }
    )
    assert isinstance(modify, ModifyUpdate) and modify.seq == 12
    assert modify.data.position == 100 and modify.data.sl == 1.07

    pending = {
        "ticket": 555,
        "symbol": "EURUSD",
        "type": "BuyLimit",
        "volumeInitial": 0.2,
        "volume": 0.2,
        "price": 1.08,
        "stopLimitPrice": 0.0,
        "sl": 0.0,
        "tp": 0.0,
        "expirationMode": "gtc",
        "expiration": "",
        "expirationUtc": "",
        "magic": 0,
        "comment": "",
        "time": "2026-10-30T10:00:00Z",
    }
    order = parse_event(
        {
            "type": "order",
            "seq": 3,
            "data": {**pending, "event": "triggered", "position": 777},
        }
    )
    assert isinstance(order, OrderUpdate)
    assert order.data.event == OrderEventKind.TRIGGERED
    assert order.data.position == 777 and order.data.ticket == 555
    assert order.data.expiration_mode == ExpirationMode.GTC

    orders = parse_event({"type": "orders", "snapshot": True, "data": [pending]})
    assert isinstance(orders, OrdersUpdate) and orders.snapshot is True
    assert orders.data[0].ticket == 555

    done = parse_event(
        {"type": "replayed", "topic": "trades", "count": 3, "truncated": False}
    )
    assert done == Replayed(topic="trades", count=3, truncated=False)


def test_parse_warning_names_topic() -> None:
    w = parse_event(
        {"topic": "prices", "symbol": "EURUSD", "type": "warning", "dropped": 5}
    )
    assert w == StreamWarning(dropped=5, topic="prices", symbol="EURUSD")
    bars = parse_event(
        {
            "topic": "bars",
            "symbol": "EURUSD",
            "timeframe": "M5",
            "type": "warning",
            "dropped": 1,
            "ts": 9,
        }
    )
    assert isinstance(bars, StreamWarning) and bars.timeframe == "M5"


async def test_since_validation() -> None:
    s = AsyncStream(ws_url="ws://x/ws", api_key="k", platform="mt5")
    with pytest.raises(ValidationError, match="trades"):
        await s.subscribe("prices", symbol="EURUSD", since="2026-10-30T10:00:00Z")
    with pytest.raises(ValidationError):
        await s.subscribe_trades(since="")
    with pytest.raises(ValidationError):
        await s.subscribe("trades", since=123)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        await s.subscribe("deals")


async def test_subscribe_trades_since_and_orders_payloads() -> None:
    received: list[dict] = []

    async def handler(ws: Any) -> None:
        try:
            for _ in range(4):
                received.append(json.loads(await ws.recv()))
            await ws.recv()
        except Exception:
            pass

    server, url = await _start_server(handler)
    try:
        async with AsyncStream(ws_url=url, api_key="k", platform="mt5") as s:
            await s.subscribe_trades(since="2026-10-30T10:00:00.000Z")
            await s.subscribe_orders()
            await s.unsubscribe_orders()
            await s.subscribe_trades()
            await asyncio.sleep(0.1)
    finally:
        server.close()
        await server.wait_closed()
    assert received == [
        {"action": "subscribe", "topic": "trades", "since": "2026-10-30T10:00:00.000Z"},
        {"action": "subscribe", "topic": "orders"},
        {"action": "unsubscribe", "topic": "orders"},
        {"action": "subscribe", "topic": "trades"},
    ]


def _resume_handler(subs: list[dict]) -> Any:
    """Conn 1 sends deals 1 and 2, then drops. Conn 2 replays from the
    resume point (deal 2 again, then 3), reports it, then goes live with
    deal 3 once more (the switch-over overlap) and deal 4."""
    conns = {"n": 0}

    async def handler(ws: Any) -> None:
        conns["n"] += 1
        n = conns["n"]
        try:
            subs.append(json.loads(await ws.recv()))
            if n == 1:
                await ws.send(json.dumps(_trade_frame(1, "T1", seq=1)))
                await ws.send(json.dumps(_trade_frame(2, "T2", seq=2)))
                await ws.close()
                return
            for frame in (
                _trade_frame(2, "T2", replay=True),
                _trade_frame(3, "T3", replay=True),
                {"type": "replayed", "topic": "trades", "count": 2,
                 "truncated": False},
                _trade_frame(3, "T3", seq=3),
                _trade_frame(4, "T4", seq=4),
            ):
                await ws.send(json.dumps(frame))
            await ws.recv()
        except Exception:
            pass

    return handler


async def _collect_deals(url: str, *, resume_trades: bool) -> list[Any]:
    got: list[Any] = []
    async with AsyncStream(
        ws_url=url,
        api_key="k",
        platform="mt5",
        auto_reconnect=True,
        max_reconnect_attempts=3,
        resume_trades=resume_trades,
    ) as s:
        await s.subscribe_trades()

        async def consume() -> None:
            async for ev in s:
                got.append(ev)
                if isinstance(ev, TradeUpdate) and ev.data.deal == 4:
                    return

        await asyncio.wait_for(consume(), timeout=5)
    return got


async def test_resume_trades_after_reconnect_dedupes() -> None:
    subs: list[dict] = []
    server, url = await _start_server(_resume_handler(subs))
    try:
        got = await _collect_deals(url, resume_trades=True)
    finally:
        server.close()
        await server.wait_closed()

    assert subs == [
        {"action": "subscribe", "topic": "trades"},
        {"action": "subscribe", "topic": "trades", "since": "T2"},
    ]
    deals = [e.data.deal for e in got if isinstance(e, TradeUpdate)]
    assert deals == [1, 2, 3, 4]  # the replayed 2 and the live 3 were dropped
    replayed_3 = next(
        e for e in got if isinstance(e, TradeUpdate) and e.data.deal == 3
    )
    assert replayed_3.replay is True
    assert any(isinstance(e, Replayed) and e.count == 2 for e in got)


async def test_resume_trades_off_keeps_old_behavior() -> None:
    subs: list[dict] = []
    server, url = await _start_server(_resume_handler(subs))
    try:
        got = await _collect_deals(url, resume_trades=False)
    finally:
        server.close()
        await server.wait_closed()

    # Plain resubscribe, every frame passed through.
    assert subs == [
        {"action": "subscribe", "topic": "trades"},
        {"action": "subscribe", "topic": "trades"},
    ]
    deals = [e.data.deal for e in got if isinstance(e, TradeUpdate)]
    assert deals == [1, 2, 2, 3, 3, 4]


async def test_resume_uses_callers_since_until_a_frame_arrives() -> None:
    subs: list[dict] = []
    conns = {"n": 0}

    async def handler(ws: Any) -> None:
        conns["n"] += 1
        try:
            subs.append(json.loads(await ws.recv()))
            if conns["n"] == 1:
                await ws.close()  # drop before any trade frame
                return
            await ws.send(json.dumps(_trade_frame(5, "T5", seq=1)))
            await ws.recv()
        except Exception:
            pass

    server, url = await _start_server(handler)
    try:
        async with AsyncStream(
            ws_url=url, api_key="k", platform="mt5", max_reconnect_attempts=3
        ) as s:
            await s.subscribe_trades(since="T0")

            async def consume() -> None:
                async for ev in s:
                    if isinstance(ev, TradeUpdate):
                        return

            await asyncio.wait_for(consume(), timeout=5)
    finally:
        server.close()
        await server.wait_closed()
    assert [sub.get("since") for sub in subs] == ["T0", "T0"]


def test_trade_dedupe_keys_and_window() -> None:
    from fxsocket.models import TradeEventData
    from fxsocket.terminal.stream import _DEDUPE_WINDOW

    def data(deal: int, order: int, entry: str, time: str = "t") -> TradeEventData:
        return TradeEventData.model_validate(
            _trade_frame(deal, time, order=order, entry=entry)["data"]
        )

    s = AsyncStream(ws_url="ws://x/ws", api_key="k", platform="mt4")
    # MT4: deal is 0, so order + entry identify the frame.
    assert s._remember_trade(data(0, 100, "In", "A")) is True
    assert s._remember_trade(data(0, 100, "Out", "B")) is True
    assert s._remember_trade(data(0, 100, "In", "C")) is False
    assert s._last_trade_time == "B"  # duplicates don't move the resume point
    # The window is bounded: the oldest key is forgotten once it overflows.
    for i in range(_DEDUPE_WINDOW):
        s._remember_trade(data(i + 1, i + 1, "In"))
    assert len(s._seen) == _DEDUPE_WINDOW
    assert s._remember_trade(data(0, 100, "In")) is True


def test_resume_point_is_the_newest_trade_time() -> None:
    from fxsocket.models import TradeEventData

    def data(order: int, entry: str, time: str) -> TradeEventData:
        return TradeEventData.model_validate(
            _trade_frame(0, time, order=order, entry=entry)["data"]
        )

    s = AsyncStream(ws_url="ws://x/ws", api_key="k", platform="mt4")
    s._remember_trade(data(100, "Out", "2026-10-30T10:00:05.250Z"))
    # An MT4 remainder's In carries the original open time: older, so the
    # resume point must stay on the Out.
    s._remember_trade(data(101, "In", "2026-10-29T08:00:00.000Z"))
    assert s._last_trade_time == "2026-10-30T10:00:05.250Z"
    # Same second without and with milliseconds still orders by time.
    s._remember_trade(data(102, "In", "2026-10-30T10:00:05Z"))
    assert s._last_trade_time == "2026-10-30T10:00:05.250Z"
    s._remember_trade(data(103, "In", "2026-10-30T10:00:06Z"))
    assert s._last_trade_time == "2026-10-30T10:00:06Z"


async def test_unsubscribe_trades_resets_resume_state() -> None:
    from fxsocket.models import TradeEventData

    s = AsyncStream(ws_url="ws://x/ws", api_key="k", platform="mt5")
    sent: list[dict] = []

    async def fake_send(payload: dict) -> None:
        sent.append(payload)

    s._send = fake_send  # type: ignore[method-assign]
    await s.subscribe_trades(since="2026-10-30T09:00:00Z")
    frame = TradeEventData.model_validate(
        _trade_frame(7, "2026-10-30T10:00:00Z")["data"]
    )
    assert s._remember_trade(frame) is True
    await s.unsubscribe_trades()
    assert s._last_trade_time is None
    assert s._trades_since is None
    # A deliberate replay after a fresh subscribe is not swallowed.
    await s.subscribe_trades(since="2026-10-30T09:00:00Z")
    assert s._remember_trade(frame) is True
    assert sent[-1] == {
        "action": "subscribe",
        "topic": "trades",
        "since": "2026-10-30T09:00:00Z",
    }


async def test_modify_frames_are_never_deduped() -> None:
    modify = {
        "type": "modify",
        "data": {
            "position": 1,
            "symbol": "EURUSD",
            "type": "Buy",
            "volume": 0.1,
            "price": 1.0,
            "sl": 0.9,
            "tp": 0.0,
            "magic": 0,
            "comment": "",
            "time": "T1",
        },
    }

    async def handler(ws: Any) -> None:
        try:
            await ws.recv()
            for frame in (modify, modify, _trade_frame(1, "T1"), _trade_frame(1, "T1")):
                await ws.send(json.dumps(frame))
            await ws.send(json.dumps(_TICK))
            await ws.recv()
        except Exception:
            pass

    server, url = await _start_server(handler)
    got: list[Any] = []
    try:
        async with AsyncStream(ws_url=url, api_key="k", platform="mt5") as s:
            await s.subscribe_trades()

            async def consume() -> None:
                async for ev in s:
                    got.append(ev)
                    if isinstance(ev, Tick):
                        return

            await asyncio.wait_for(consume(), timeout=5)
    finally:
        server.close()
        await server.wait_closed()
    assert sum(isinstance(e, ModifyUpdate) for e in got) == 2
    assert sum(isinstance(e, TradeUpdate) for e in got) == 1


def test_client_stream_factories_pass_resume_trades() -> None:
    from fxsocket import AsyncClient, Client
    from fxsocket.models import Account

    account = Account.model_validate(
        {
            "id": "acc-1",
            "platform": "mt5",
            "server": "Demo",
            "login": 1,
            "status": "connected",
            "rest_url": "https://term.test",
            "ws_url": "wss://term.test/ws",
            "created_at": "2026-06-22T16:53:56Z",
        }
    )
    with Client(api_key="fxs_live_k") as fx:
        s = fx.stream(account, resume_trades=False)
        try:
            assert s._astream._resume_trades is False
        finally:
            s.close()
        s = fx.stream(account)
        try:
            assert s._astream._resume_trades is True
        finally:
            s.close()
    afx = AsyncClient(api_key="fxs_live_k")
    assert afx.stream(account, resume_trades=False)._resume_trades is False


async def test_sync_stream_trades_since_and_orders() -> None:
    received: list[dict] = []

    async def handler(ws: Any) -> None:
        try:
            for _ in range(2):
                received.append(json.loads(await ws.recv()))
            await ws.send(json.dumps(_trade_frame(1, "T1", seq=1)))
            await ws.recv()
        except Exception:
            pass

    server, url = await _start_server(handler)

    def run_sync() -> list[Any]:
        def factory() -> AsyncStream:
            return AsyncStream(
                ws_url=url, api_key="k", platform="mt5", auto_reconnect=False
            )

        out: list[Any] = []
        with Stream(factory) as s:
            s.subscribe_trades(since="T0")
            s.subscribe_orders()
            for ev in s:
                out.append(ev)
                if isinstance(ev, TradeUpdate):
                    break
        return out

    try:
        events = await asyncio.get_running_loop().run_in_executor(None, run_sync)
    finally:
        server.close()
        await server.wait_closed()
    assert received == [
        {"action": "subscribe", "topic": "trades", "since": "T0"},
        {"action": "subscribe", "topic": "orders"},
    ]
    assert any(isinstance(e, TradeUpdate) for e in events)
