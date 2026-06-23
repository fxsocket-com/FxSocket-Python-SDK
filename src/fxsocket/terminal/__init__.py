"""Per-account terminal API — REST trading/market-data and WebSocket streaming.

The endpoints for an account come from ``Account.rest_url`` / ``Account.ws_url``
(populated by the management API), which already resolve shared-pod vs
private-droplet hosting.
"""

from .client import AsyncTerminalClient, TerminalClient
from .stream import (
    AccountUpdate,
    AsyncStream,
    Bar,
    PositionsUpdate,
    Stream,
    StreamErrorEvent,
    StreamEvent,
    StreamWarning,
    Subscribed,
    Subscriptions,
    TerminalUpdate,
    Tick,
    TradeUpdate,
    UnknownEvent,
    Unsubscribed,
)

__all__ = [
    "TerminalClient",
    "AsyncTerminalClient",
    "AsyncStream",
    "Stream",
    "StreamEvent",
    "Tick",
    "Bar",
    "AccountUpdate",
    "PositionsUpdate",
    "TradeUpdate",
    "TerminalUpdate",
    "StreamWarning",
    "Subscribed",
    "Unsubscribed",
    "StreamErrorEvent",
    "Subscriptions",
    "UnknownEvent",
]
