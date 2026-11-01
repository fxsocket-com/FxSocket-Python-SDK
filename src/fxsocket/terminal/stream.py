"""Per-account WebSocket streaming for the terminal API (MT4 + MT5).

The wire protocol is identical on both platforms. A connection authenticates
with ``?api_key=`` (WebSocket upgrades can't carry custom headers), then the
client subscribes to topics:

================  ================  =========================================
topic             needs             yields
================  ================  =========================================
``prices``        symbol            :class:`Tick`
``bars``          symbol+timeframe  :class:`Bar`
``account``       –                 :class:`AccountUpdate`
``positions``     –                 :class:`PositionsUpdate`
``trades``        – (``since``)     :class:`TradeUpdate`, :class:`ModifyUpdate`,
                                    :class:`Replayed`
``orders``        –                 :class:`OrdersUpdate` (snapshot),
                                    :class:`OrderUpdate`
``terminal``      –                 :class:`TerminalUpdate`
================  ================  =========================================

Plus control events: :class:`StreamWarning` (slow-client drop count),
:class:`Subscribed` / :class:`Unsubscribed` / :class:`StreamErrorEvent` /
:class:`Subscriptions`.

Data events carry the frame envelope (bridges MT5 0.17.0 / MT4 0.15.0 on):
``seq`` numbers the live frames of a topic (a gap means frames were dropped
for this client), ``ts`` is when the bridge sent the frame (Unix ms, UTC),
and ``snapshot`` marks the one current-state frame sent right after a
subscribe (it has no ``seq``). On older bridges ``seq``/``ts`` are ``None``.

The server pings every 5 s and drops a client that has not answered for 30 s;
the WebSocket library answers those pings by itself, so nothing is needed on
your side as long as the stream is iterated.

**Trades after a reconnect.** With ``resume_trades`` (the default) the stream
remembers the newest trade ``time`` it yielded and, after an automatic
reconnect, resubscribes ``trades`` with ``since`` set to it: the terminal
first replays the deals from that moment on (``replay=True`` frames, then a
:class:`Replayed`), then the live frames continue. Deals seen twice around
the switch-over are dropped. The replay reaches at most 24 h back and covers
the newest 1000 deals; a :class:`Replayed` with ``truncated=True``, or an
error frame for a longer gap, means you should reconcile via
``order_history``.

:class:`AsyncStream` is the primary, async interface; :class:`Stream` is a
thread-backed synchronous wrapper over it.
"""

from __future__ import annotations

import asyncio
import json
import queue
import ssl as _ssl
import threading
from collections import deque
from collections.abc import AsyncIterator, Hashable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from websockets.asyncio.client import connect as _ws_connect
from websockets.exceptions import ConnectionClosed

from ..enums import Platform, Timeframe
from ..errors import StreamError, ValidationError
from ..models import (
    AccountSummary,
    Candle,
    OpenedOrder,
    OrderEventData,
    PendingOrder,
    PositionModifyData,
    Quote,
    TerminalStatusData,
    TradeEventData,
)
from .client import coerce_timeframe

_TOPICS = frozenset(
    {"account", "positions", "trades", "orders", "terminal", "prices", "bars"}
)

#: How many trade keys the resume dedupe remembers.
_DEDUPE_WINDOW = 2048

# --------------------------------------------------------------------------- #
# Events (the tagged union yielded by a stream)
# --------------------------------------------------------------------------- #
#
# Data events end with the frame envelope: ``seq`` (per-topic frame number,
# ``None`` on snapshots, replays and older bridges), ``ts`` (send time, Unix
# ms UTC) and ``snapshot``.


@dataclass(frozen=True)
class Tick:
    symbol: str
    data: Quote
    seq: int | None = None
    ts: int | None = None
    snapshot: bool = False


@dataclass(frozen=True)
class Bar:
    symbol: str
    timeframe: str
    data: Candle
    seq: int | None = None
    ts: int | None = None
    snapshot: bool = False


@dataclass(frozen=True)
class AccountUpdate:
    data: AccountSummary
    seq: int | None = None
    ts: int | None = None
    snapshot: bool = False


@dataclass(frozen=True)
class PositionsUpdate:
    data: list[OpenedOrder]
    seq: int | None = None
    ts: int | None = None
    snapshot: bool = False


@dataclass(frozen=True)
class TradeUpdate:
    """A deal on the ``trades`` stream. ``replay=True`` marks a deal resent
    by a ``since`` replay (those have no ``seq``)."""

    data: TradeEventData
    seq: int | None = None
    ts: int | None = None
    snapshot: bool = False
    replay: bool = False


@dataclass(frozen=True)
class ModifyUpdate:
    """An open position's SL/TP moved (``modify`` frame, ``trades`` topic)."""

    data: PositionModifyData
    seq: int | None = None
    ts: int | None = None
    snapshot: bool = False


@dataclass(frozen=True)
class OrderUpdate:
    """One step in a pending order's life (``order`` frame, ``orders``
    topic); ``data.event`` says which."""

    data: OrderEventData
    seq: int | None = None
    ts: int | None = None
    snapshot: bool = False


@dataclass(frozen=True)
class OrdersUpdate:
    """The active pending orders, sent once right after subscribing to
    ``orders`` (``snapshot`` is ``True``)."""

    data: list[PendingOrder]
    seq: int | None = None
    ts: int | None = None
    snapshot: bool = False


@dataclass(frozen=True)
class TerminalUpdate:
    data: TerminalStatusData
    seq: int | None = None
    ts: int | None = None
    snapshot: bool = False


@dataclass(frozen=True)
class StreamWarning:
    """The server dropped ``dropped`` messages because this client lagged.

    ``topic`` (and ``symbol`` / ``timeframe`` for ``prices`` / ``bars``)
    names the stream that lost them; empty on older bridges.
    """

    dropped: int
    topic: str = ""
    symbol: str = ""
    timeframe: str = ""


@dataclass(frozen=True)
class Replayed:
    """A ``since`` replay is complete: ``count`` deals were resent.

    ``truncated=True`` means the terminal hit its replay limit (the newest
    1000 deals), so older ones are missing: reconcile via ``order_history``.
    """

    topic: str
    count: int
    truncated: bool


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
    | ModifyUpdate
    | OrderUpdate
    | OrdersUpdate
    | TerminalUpdate
    | StreamWarning
    | Replayed
    | Subscribed
    | Unsubscribed
    | StreamErrorEvent
    | Subscriptions
    | UnknownEvent
)


def _opt_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _envelope(msg: dict[str, Any]) -> dict[str, Any]:
    return {
        "seq": _opt_int(msg.get("seq")),
        "ts": _opt_int(msg.get("ts")),
        "snapshot": bool(msg.get("snapshot", False)),
    }


def parse_event(msg: dict[str, Any]) -> StreamEvent:
    """Map a decoded server frame to a typed event."""
    t = msg.get("type")
    if t == "tick":
        return Tick(
            symbol=msg.get("symbol", ""),
            data=Quote.model_validate(msg["data"]),
            **_envelope(msg),
        )
    if t == "bar":
        return Bar(
            symbol=msg.get("symbol", ""),
            timeframe=msg.get("timeframe", ""),
            data=Candle.model_validate(msg["data"]),
            **_envelope(msg),
        )
    if t == "account":
        return AccountUpdate(
            data=AccountSummary.model_validate(msg["data"]), **_envelope(msg)
        )
    if t == "positions":
        return PositionsUpdate(
            data=[OpenedOrder.model_validate(r) for r in msg.get("data", [])],
            **_envelope(msg),
        )
    if t == "trade":
        return TradeUpdate(
            data=TradeEventData.model_validate(msg["data"]),
            replay=bool(msg.get("replay", False)),
            **_envelope(msg),
        )
    if t == "modify":
        return ModifyUpdate(
            data=PositionModifyData.model_validate(msg["data"]), **_envelope(msg)
        )
    if t == "order":
        return OrderUpdate(
            data=OrderEventData.model_validate(msg["data"]), **_envelope(msg)
        )
    if t == "orders":
        return OrdersUpdate(
            data=[PendingOrder.model_validate(r) for r in msg.get("data", [])],
            **_envelope(msg),
        )
    if t == "terminal":
        return TerminalUpdate(
            data=TerminalStatusData.model_validate(msg["data"]), **_envelope(msg)
        )
    if t == "warning":
        return StreamWarning(
            dropped=int(msg.get("dropped", 0)),
            topic=str(msg.get("topic") or ""),
            symbol=str(msg.get("symbol") or ""),
            timeframe=str(msg.get("timeframe") or ""),
        )
    if t == "replayed":
        return Replayed(
            topic=str(msg.get("topic") or "trades"),
            count=int(msg.get("count", 0)),
            truncated=bool(msg.get("truncated", False)),
        )
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


def _parse_frame_time(value: str) -> datetime | None:
    """A frame's ``time`` as a comparable datetime (``None`` if unparsable).

    The ``Z`` is stylistic (broker server time); it only has to compare
    consistently. Python 3.10's ``fromisoformat`` does not take ``Z``.
    """
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _is_newer(candidate: str, current: str) -> bool:
    """Whether trade time ``candidate`` is later than ``current``."""
    a, b = _parse_frame_time(candidate), _parse_frame_time(current)
    if a is not None and b is not None and a != b:
        return a > b
    return candidate > current


def _trade_key(data: TradeEventData) -> Hashable:
    """Identity of a deal for the resume dedupe: the deal ticket on MT5;
    MT4 has none (``deal`` is 0), so its order ticket plus direction."""
    if data.deal:
        return ("deal", data.deal)
    return ("order", data.order, data.entry)


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
    topic: str,
    symbol: str | None,
    timeframe: str | None,
    since: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"action": "subscribe", "topic": topic}
    if symbol is not None:
        payload["symbol"] = symbol
    if timeframe is not None:
        payload["timeframe"] = timeframe
    if since is not None:
        payload["since"] = since
    return payload


def _validate_sub(
    topic: str,
    symbol: str | None,
    timeframe: Timeframe | str | None,
    since: str | None = None,
) -> str | None:
    """Validate a subscription; returns the canonical timeframe label (or None)."""
    if topic not in _TOPICS:
        raise ValidationError(
            f"unknown topic {topic!r}; one of {sorted(_TOPICS)}"
        )
    if since is not None:
        if topic != "trades":
            raise ValidationError("since only applies to the 'trades' topic")
        if not isinstance(since, str) or not since.strip():
            raise ValidationError(
                "since must be a non-empty broker server time string, e.g. "
                "the `time` of a trade frame"
            )
    if topic in ("prices", "bars") and not symbol:
        raise ValidationError(f"topic {topic!r} requires a symbol")
    if topic == "bars":
        if not timeframe:
            raise ValidationError("topic 'bars' requires a timeframe")
        return coerce_timeframe(timeframe).value
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

    With ``resume_trades`` (default) a ``trades`` subscription is resumed
    from the newest trade ``time`` it yielded after a reconnect, so deals executed
    while the connection was down are replayed, and duplicate deals are
    dropped (by ``deal`` on MT5, by ``order`` + ``entry`` on MT4). Set it to
    ``False`` to get exactly the frames the server sends, with a plain
    resubscribe after a reconnect.
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
        resume_trades: bool = True,
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
        self._resume_trades = resume_trades
        #: the ``since`` the caller subscribed ``trades`` with
        self._trades_since: str | None = None
        #: newest ``time`` of the trade frames yielded (resume point)
        self._last_trade_time: str | None = None
        #: recently yielded trade keys, oldest first (bounded dedupe window)
        self._seen_order: deque[Hashable] = deque()
        self._seen: set[Hashable] = set()

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
        since: str | None = None,
    ) -> None:
        """Subscribe to ``topic``.

        ``since`` (``trades`` only) first replays the deals at or after that
        moment. It is **broker server time** as the frames print it (pass a
        trade frame's ``time``), not UTC, and at most 24 h back.
        """
        tf = _validate_sub(topic, symbol, timeframe, since)
        if topic == "trades":
            self._trades_since = since
        self._subs.add((topic, symbol, tf))
        await self._send(_sub_payload(topic, symbol, tf, since))

    async def unsubscribe(
        self,
        topic: str,
        *,
        symbol: str | None = None,
        timeframe: Timeframe | str | None = None,
    ) -> None:
        tf = _validate_sub(topic, symbol, timeframe)
        if topic == "trades":
            # A later subscribe starts afresh rather than resuming this one,
            # so a deliberate ``since`` replay is not swallowed by the dedupe.
            self._trades_since = None
            self._last_trade_time = None
            self._seen.clear()
            self._seen_order.clear()
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

    async def subscribe_trades(self, since: str | None = None) -> None:
        """Subscribe to deals; see :meth:`subscribe` for ``since``."""
        await self.subscribe("trades", since=since)

    async def subscribe_orders(self) -> None:
        await self.subscribe("orders")

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

    async def unsubscribe_orders(self) -> None:
        await self.unsubscribe("orders")

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
            if not isinstance(msg, dict):
                continue
            event = parse_event(msg)
            if isinstance(event, TradeUpdate) and self._resume_trades:
                if not self._remember_trade(event.data):
                    continue  # a deal already yielded (replay overlap)
            yield event

    def _remember_trade(self, data: TradeEventData) -> bool:
        """Record a trade frame for resume; ``False`` if it is a duplicate."""
        key = _trade_key(data)
        if key in self._seen:
            return False
        self._seen.add(key)
        self._seen_order.append(key)
        if len(self._seen_order) > _DEDUPE_WINDOW:
            self._seen.discard(self._seen_order.popleft())
        # The newest time, not the latest frame's: an MT4 remainder's ``In``
        # can carry its original open time, which must not move the resume
        # point back (a ``since`` over 24 h old is refused).
        if data.time and (
            self._last_trade_time is None
            or _is_newer(data.time, self._last_trade_time)
        ):
            self._last_trade_time = data.time
        return True

    def _resume_since(self, topic: str) -> str | None:
        """The ``since`` to resubscribe ``topic`` with after a reconnect."""
        if topic != "trades" or not self._resume_trades:
            return None
        return self._last_trade_time or self._trades_since

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
                        payload = _sub_payload(
                            topic, symbol, tf, self._resume_since(topic)
                        )
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
        since: str | None = None,
    ) -> None:
        self._run(
            self._astream.subscribe(
                topic, symbol=symbol, timeframe=timeframe, since=since
            )
        )

    def subscribe_prices(self, symbol: str) -> None:
        self._run(self._astream.subscribe_prices(symbol))

    def subscribe_bars(self, symbol: str, timeframe: Timeframe | str) -> None:
        self._run(self._astream.subscribe_bars(symbol, timeframe))

    def subscribe_account(self) -> None:
        self._run(self._astream.subscribe_account())

    def subscribe_positions(self) -> None:
        self._run(self._astream.subscribe_positions())

    def subscribe_trades(self, since: str | None = None) -> None:
        self._run(self._astream.subscribe_trades(since))

    def subscribe_orders(self) -> None:
        self._run(self._astream.subscribe_orders())

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

    def unsubscribe_orders(self) -> None:
        self._run(self._astream.unsubscribe_orders())

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
