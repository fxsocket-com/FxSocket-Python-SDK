"""Per-account WebSocket streaming for the terminal API (MT4 + MT5).

The wire protocol is identical on both platforms. A connection authenticates
with ``?api_key=`` (WebSocket upgrades can't carry custom headers), then the
client subscribes to topics:

================  ==============  =====================================
topic             needs           yields
================  ==============  =====================================
``prices``        symbol          :class:`Tick`
``bars``          symbol+timeframe :class:`Bar`
``account``       –               :class:`AccountUpdate`
``positions``     –               :class:`PositionsUpdate`
``trades``        –               :class:`TradeUpdate`
``terminal``      –               :class:`TerminalUpdate`
================  ==============  =====================================

Plus control events: :class:`StreamWarning` (slow-client drop count),
:class:`Subscribed` / :class:`Unsubscribed` / :class:`StreamErrorEvent` /
:class:`Subscriptions`.

:class:`AsyncStream` is the primary, async interface; :class:`Stream` is a
thread-backed synchronous wrapper over it.
"""

from __future__ import annotations

import asyncio
import json
import queue
import ssl as _ssl
import threading
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from websockets.asyncio.client import connect as _ws_connect
from websockets.exceptions import ConnectionClosed

from ..enums import MT5_ONLY_TIMEFRAMES, Platform, Timeframe
from ..errors import StreamError, UnsupportedOnPlatformError, ValidationError
from ..models import (
    AccountSummary,
    Candle,
    OpenedOrder,
    Quote,
    TerminalStatusData,
    TradeEventData,
)
from .client import coerce_timeframe

_TOPICS = frozenset(
    {"account", "positions", "trades", "terminal", "prices", "bars"}
)

# --------------------------------------------------------------------------- #
# Events (the tagged union yielded by a stream)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Tick:
    symbol: str
    data: Quote


@dataclass(frozen=True)
class Bar:
    symbol: str
    timeframe: str
    data: Candle


@dataclass(frozen=True)
class AccountUpdate:
    data: AccountSummary


@dataclass(frozen=True)
class PositionsUpdate:
    data: list[OpenedOrder]


@dataclass(frozen=True)
class TradeUpdate:
    data: TradeEventData


@dataclass(frozen=True)
class TerminalUpdate:
    data: TerminalStatusData


@dataclass(frozen=True)
class StreamWarning:
    """The server dropped ``dropped`` messages because this client lagged."""

    dropped: int


@dataclass(frozen=True)
class Subscribed:
    topic: str
    message: str


@dataclass(frozen=True)
class Unsubscribed:
    topic: str
    message: str


@dataclass(frozen=True)
class StreamErrorEvent:
    """A server-sent ``error`` frame (e.g. bad timeframe). Not an exception."""

    topic: str
    message: str


@dataclass(frozen=True)
class Subscriptions:
    data: list[dict[str, Any]]


@dataclass(frozen=True)
class UnknownEvent:
    type: str
    raw: dict[str, Any] = field(default_factory=dict)


StreamEvent = (
    Tick
    | Bar
    | AccountUpdate
    | PositionsUpdate
    | TradeUpdate
    | TerminalUpdate
    | StreamWarning
    | Subscribed
    | Unsubscribed
    | StreamErrorEvent
    | Subscriptions
    | UnknownEvent
)


def parse_event(msg: dict[str, Any]) -> StreamEvent:
    """Map a decoded server frame to a typed event."""
    t = msg.get("type")
    if t == "tick":
        return Tick(
            symbol=msg.get("symbol", ""), data=Quote.model_validate(msg["data"])
        )
    if t == "bar":
        return Bar(
            symbol=msg.get("symbol", ""),
            timeframe=msg.get("timeframe", ""),
            data=Candle.model_validate(msg["data"]),
        )
    if t == "account":
        return AccountUpdate(data=AccountSummary.model_validate(msg["data"]))
    if t == "positions":
        return PositionsUpdate(
            data=[OpenedOrder.model_validate(r) for r in msg.get("data", [])]
        )
    if t == "trade":
        return TradeUpdate(data=TradeEventData.model_validate(msg["data"]))
    if t == "terminal":
        return TerminalUpdate(data=TerminalStatusData.model_validate(msg["data"]))
    if t == "warning":
        return StreamWarning(dropped=int(msg.get("dropped", 0)))
    if t == "subscribed":
        return Subscribed(topic=msg.get("topic", ""), message=msg.get("message", ""))
    if t == "unsubscribed":
        return Unsubscribed(topic=msg.get("topic", ""), message=msg.get("message", ""))
    if t == "error":
        return StreamErrorEvent(
            topic=msg.get("topic", ""), message=msg.get("message", "")
        )
    if t == "subscriptions":
        return Subscriptions(data=list(msg.get("data", [])))
    return UnknownEvent(type=str(t or ""), raw=msg)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _with_api_key(ws_url: str, api_key: str) -> str:
    parts = urlparse(ws_url)
    params = dict(parse_qsl(parts.query))
    params["api_key"] = api_key
    return urlunparse(parts._replace(query=urlencode(params)))


def _ssl_context(uri: str, verify: bool) -> _ssl.SSLContext | None:
    if not uri.startswith("wss://"):
        return None  # ws:// — no TLS (websockets requires ssl=None here)
    # For wss:// the websockets client rejects ssl=None, so always pass an
    # explicit context — a verifying default, or one that skips verification
    # for a private droplet's self-signed certificate.
    ctx = _ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
    return ctx


def _sub_payload(
    topic: str, symbol: str | None, timeframe: str | None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"action": "subscribe", "topic": topic}
    if symbol is not None:
        payload["symbol"] = symbol
    if timeframe is not None:
        payload["timeframe"] = timeframe
    return payload


def _validate_sub(
    topic: str,
    symbol: str | None,
    timeframe: Timeframe | str | None,
    platform: Platform,
) -> str | None:
    """Validate a subscription; returns the canonical timeframe label (or None)."""
    if topic not in _TOPICS:
        raise ValidationError(
            f"unknown topic {topic!r}; one of {sorted(_TOPICS)}"
        )
    if topic in ("prices", "bars") and not symbol:
        raise ValidationError(f"topic {topic!r} requires a symbol")
    if topic == "bars":
        if not timeframe:
            raise ValidationError("topic 'bars' requires a timeframe")
        tf = coerce_timeframe(timeframe)
        if platform is Platform.MT4 and tf in MT5_ONLY_TIMEFRAMES:
            raise UnsupportedOnPlatformError(
                f"{tf.value} is an MT5-only timeframe; not available on MT4."
            )
        return tf.value
    return None


# --------------------------------------------------------------------------- #
# Async stream
# --------------------------------------------------------------------------- #


class AsyncStream:
    """An async WebSocket stream bound to one account's terminal.

    Use as an async context manager and iterate it::

        async with client.stream(account) as s:
            await s.subscribe_prices("EURUSD")
            async for event in s:
                match event:
                    case Tick():
                        ...

    With ``auto_reconnect`` (default), a dropped connection is transparently
    re-established and all active subscriptions are replayed.
    """

    def __init__(
        self,
        *,
        ws_url: str,
        api_key: str,
        platform: Platform | str,
        verify: bool = True,
        auto_reconnect: bool = True,
        open_timeout: float = 10.0,
        ping_interval: float = 20.0,
        max_reconnect_attempts: int = 5,
    ) -> None:
        self.platform = Platform(platform)
        self._uri = _with_api_key(ws_url, api_key)
        self._ssl = _ssl_context(self._uri, verify)
        self._auto_reconnect = auto_reconnect
        self._open_timeout = open_timeout
        self._ping_interval = ping_interval
        self._max_attempts = max_reconnect_attempts
        self._conn: Any = None
        self._closing = False
        #: serializes connection swaps (reconnect) against sends (subscribe)
        self._lock = asyncio.Lock()
        #: active subscriptions as (topic, symbol, timeframe), replayed on reconnect
        self._subs: set[tuple[str, str | None, str | None]] = set()

    async def _open(self) -> None:
        self._conn = await _ws_connect(
            self._uri,
            ssl=self._ssl,
            open_timeout=self._open_timeout,
            ping_interval=self._ping_interval,
        )

    async def connect(self) -> None:
        try:
            await self._open()
        except OSError as exc:  # DNS/refused/TLS at connect time
            raise StreamError(f"failed to connect to terminal stream: {exc}") from exc

    async def _send(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            if self._conn is None:
                raise StreamError("stream is not connected")
            await self._conn.send(json.dumps(payload))

    async def subscribe(
        self,
        topic: str,
        *,
        symbol: str | None = None,
        timeframe: Timeframe | str | None = None,
    ) -> None:
        tf = _validate_sub(topic, symbol, timeframe, self.platform)
        self._subs.add((topic, symbol, tf))
        await self._send(_sub_payload(topic, symbol, tf))

    async def unsubscribe(
        self,
        topic: str,
        *,
        symbol: str | None = None,
        timeframe: Timeframe | str | None = None,
    ) -> None:
        tf = _validate_sub(topic, symbol, timeframe, self.platform)
        self._subs.discard((topic, symbol, tf))
        payload = _sub_payload(topic, symbol, tf)
        payload["action"] = "unsubscribe"
        await self._send(payload)

    # convenience wrappers ------------------------------------------------- #

    async def subscribe_prices(self, symbol: str) -> None:
        await self.subscribe("prices", symbol=symbol)

    async def subscribe_bars(self, symbol: str, timeframe: Timeframe | str) -> None:
        await self.subscribe("bars", symbol=symbol, timeframe=timeframe)

    async def subscribe_account(self) -> None:
        await self.subscribe("account")

    async def subscribe_positions(self) -> None:
        await self.subscribe("positions")

    async def subscribe_trades(self) -> None:
        await self.subscribe("trades")

    async def subscribe_terminal(self) -> None:
        await self.subscribe("terminal")

    async def unsubscribe_prices(self, symbol: str) -> None:
        await self.unsubscribe("prices", symbol=symbol)

    async def unsubscribe_bars(self, symbol: str, timeframe: Timeframe | str) -> None:
        await self.unsubscribe("bars", symbol=symbol, timeframe=timeframe)

    async def unsubscribe_account(self) -> None:
        await self.unsubscribe("account")

    async def unsubscribe_positions(self) -> None:
        await self.unsubscribe("positions")

    async def unsubscribe_trades(self) -> None:
        await self.unsubscribe("trades")

    async def unsubscribe_terminal(self) -> None:
        await self.unsubscribe("terminal")

    async def list_subscriptions(self) -> None:
        """Ask the server to echo current subscriptions (a ``Subscriptions``)."""
        await self._send({"action": "list"})

    # iteration ------------------------------------------------------------ #

    async def __aiter__(self) -> AsyncIterator[StreamEvent]:
        while True:
            conn = self._conn
            if self._closing or conn is None:
                return
            try:
                raw = await conn.recv()
            # ConnectionClosed is the clean case; OSError covers transport
            # errors (TLS/socket); both mean "the connection is gone".
            except (ConnectionClosed, OSError) as exc:
                if self._closing or not self._auto_reconnect:
                    return
                if not await self._reconnect():
                    raise StreamError(
                        "terminal stream dropped and could not be re-established"
                    ) from exc
                continue
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if isinstance(msg, dict):
                yield parse_event(msg)

    async def _reconnect(self) -> bool:
        """Reconnect and replay subscriptions. Returns False if it gave up.

        A failure mid-replay (the fresh socket drops again) is treated as a
        failed attempt and retried — it must never escape and kill iteration.
        Duplicate replays after a partial failure are harmless: the server
        answers ``already subscribed``.
        """
        delay = 0.5
        for attempt in range(self._max_attempts):
            if attempt:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
            if self._closing:
                return False
            try:
                async with self._lock:
                    await self._open()
                    for topic, symbol, tf in list(self._subs):
                        payload = _sub_payload(topic, symbol, tf)
                        await self._conn.send(json.dumps(payload))
                return True
            except (StreamError, ConnectionClosed, OSError):
                continue
        return False

    async def aclose(self) -> None:
        self._closing = True
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> AsyncStream:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


# --------------------------------------------------------------------------- #
# Sync wrapper
# --------------------------------------------------------------------------- #

_CLOSED = object()


class Stream:
    """Synchronous wrapper over :class:`AsyncStream`.

    Runs the async stream on a dedicated event loop in a background thread and
    exposes a blocking iterator::

        with client.stream(account) as s:
            s.subscribe_prices("EURUSD")
            for event in s:
                ...
    """

    def __init__(self, factory: Any) -> None:
        # ``factory`` builds the (unconnected) AsyncStream inside the loop thread.
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._astream: AsyncStream = factory()
        self._queue: queue.Queue[Any] = queue.Queue()
        self._consumer: Any = None
        self._closed = False

    def _run(self, coro: Any, *, timeout: float | None = None) -> Any:
        try:
            return asyncio.run_coroutine_threadsafe(coro, self._loop).result(
                timeout=timeout
            )
        except TimeoutError as exc:
            raise StreamError("terminal stream operation timed out") from exc

    async def _consume(self) -> None:
        try:
            async for event in self._astream:
                self._queue.put(event)
        finally:
            self._queue.put(_CLOSED)

    # lifecycle ------------------------------------------------------------ #

    def connect(self) -> Stream:
        self._run(self._astream.connect())
        self._consumer = asyncio.run_coroutine_threadsafe(self._consume(), self._loop)
        return self

    def subscribe(
        self,
        topic: str,
        *,
        symbol: str | None = None,
        timeframe: Timeframe | str | None = None,
    ) -> None:
        self._run(self._astream.subscribe(topic, symbol=symbol, timeframe=timeframe))

    def subscribe_prices(self, symbol: str) -> None:
        self._run(self._astream.subscribe_prices(symbol))

    def subscribe_bars(self, symbol: str, timeframe: Timeframe | str) -> None:
        self._run(self._astream.subscribe_bars(symbol, timeframe))

    def subscribe_account(self) -> None:
        self._run(self._astream.subscribe_account())

    def subscribe_positions(self) -> None:
        self._run(self._astream.subscribe_positions())

    def subscribe_trades(self) -> None:
        self._run(self._astream.subscribe_trades())

    def subscribe_terminal(self) -> None:
        self._run(self._astream.subscribe_terminal())

    def unsubscribe(
        self,
        topic: str,
        *,
        symbol: str | None = None,
        timeframe: Timeframe | str | None = None,
    ) -> None:
        self._run(self._astream.unsubscribe(topic, symbol=symbol, timeframe=timeframe))

    def unsubscribe_prices(self, symbol: str) -> None:
        self._run(self._astream.unsubscribe_prices(symbol))

    def unsubscribe_bars(self, symbol: str, timeframe: Timeframe | str) -> None:
        self._run(self._astream.unsubscribe_bars(symbol, timeframe))

    def unsubscribe_account(self) -> None:
        self._run(self._astream.unsubscribe_account())

    def unsubscribe_positions(self) -> None:
        self._run(self._astream.unsubscribe_positions())

    def unsubscribe_trades(self) -> None:
        self._run(self._astream.unsubscribe_trades())

    def unsubscribe_terminal(self) -> None:
        self._run(self._astream.unsubscribe_terminal())

    def list_subscriptions(self) -> None:
        self._run(self._astream.list_subscriptions())

    def __iter__(self) -> Iterator[StreamEvent]:
        if self._consumer is None:
            raise RuntimeError(
                "Stream is not connected — use `with client.stream(account) as s:` "
                "(or call .connect()) before iterating."
            )
        while True:
            event = self._queue.get()
            if event is _CLOSED:
                return
            yield event

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            # aclose() flags the stream closing, so the consumer's async-for
            # ends instead of reconnecting; then wait for it to drain.
            try:
                self._run(self._astream.aclose(), timeout=5.0)
            except Exception:
                pass
            if self._consumer is not None:
                try:
                    self._consumer.result(timeout=5.0)
                except Exception:
                    pass
        finally:
            # Guarantee a waiting iterator wakes even if the consumer's own
            # sentinel never landed (hung task, killed loop).
            self._queue.put(_CLOSED)
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=2.0)

    def __enter__(self) -> Stream:
        return self.connect()

    def __exit__(self, *exc: object) -> None:
        self.close()
