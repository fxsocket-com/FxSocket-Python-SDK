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
    PositionsUpdate,
    Stream,
    StreamWarning,
    Subscribed,
    TerminalUpdate,
    Tick,
    TradeUpdate,
    UnsupportedOnPlatformError,
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
                "comment": "",
                "time": "t",
            },
        }
    )
    assert isinstance(trade, TradeUpdate) and trade.data.entry == "In"

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


# --------------------------------------------------------------------------- #
# Validation (no connection needed — raised before send)
# --------------------------------------------------------------------------- #


async def test_bars_requires_timeframe() -> None:
    from fxsocket import ValidationError

    s = AsyncStream(ws_url="ws://x/ws", api_key="k", platform="mt5")
    with pytest.raises(ValidationError):
        await s.subscribe("bars", symbol="EURUSD")


async def test_bars_mt5_only_timeframe_rejected_on_mt4() -> None:
    s = AsyncStream(ws_url="ws://x/ws", api_key="k", platform="mt4")
    with pytest.raises(UnsupportedOnPlatformError):
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
