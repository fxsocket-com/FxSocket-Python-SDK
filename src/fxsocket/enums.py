"""Enumerations mirroring the FxSocket / terminal API vocabulary.

All are ``str`` enums, so they compare equal to the raw wire values and
serialize back to them unchanged.
"""

from __future__ import annotations

from enum import Enum


class Platform(str, Enum):
    """Trading platform of a linked account."""

    MT4 = "mt4"
    MT5 = "mt5"


class TradingStatus(str, Enum):
    """Unified, public connection status of an account (v1 ``status``)."""

    CONNECTED = "connected"
    CONNECTING = "connecting"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class OrderOperation(str, Enum):
    """Order operation accepted by the terminal ``/OrderSend``.

    All eight are accepted by both the MT4 and MT5 terminal APIs (the MT4
    server's ``parse_operation`` maps ``BuyStopLimit``/``SellStopLimit`` too),
    so the SDK does not gate operations by platform.
    """

    BUY = "Buy"
    SELL = "Sell"
    BUY_LIMIT = "BuyLimit"
    SELL_LIMIT = "SellLimit"
    BUY_STOP = "BuyStop"
    SELL_STOP = "SellStop"
    BUY_STOP_LIMIT = "BuyStopLimit"
    SELL_STOP_LIMIT = "SellStopLimit"


#: Operations that require a ``stop_limit_price``.
STOP_LIMIT_OPERATIONS = frozenset(
    {OrderOperation.BUY_STOP_LIMIT, OrderOperation.SELL_STOP_LIMIT}
)

#: Operations that are pending orders (require an entry ``price``).
PENDING_OPERATIONS = frozenset(
    {
        OrderOperation.BUY_LIMIT,
        OrderOperation.SELL_LIMIT,
        OrderOperation.BUY_STOP,
        OrderOperation.SELL_STOP,
        OrderOperation.BUY_STOP_LIMIT,
        OrderOperation.SELL_STOP_LIMIT,
    }
)


class OrderKind(str, Enum):
    """Whether an opened row is a live position or a resting pending order."""

    POSITION = "Position"
    PENDING = "Pending"


class DealEntry(str, Enum):
    """Direction of a deal in trade history / the ``trades`` stream."""

    IN = "In"
    OUT = "Out"
    IN_OUT = "InOut"


class HealthStatus(str, Enum):
    """Roll-up status reported by the terminal ``/status`` endpoint."""

    READY = "ready"
    STARTING = "starting"
    DEGRADED = "degraded"
    DOWN = "down"


class Timeframe(str, Enum):
    """Candle timeframes.

    The MT5-only members (``M2``, ``M3``, ``H2``, ``H6``, ``H8``, ``H12``)
    are rejected client-side for MT4 accounts.
    """

    M1 = "M1"
    M2 = "M2"
    M3 = "M3"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H2 = "H2"
    H4 = "H4"
    H6 = "H6"
    H8 = "H8"
    H12 = "H12"
    D1 = "D1"
    W1 = "W1"
    MN1 = "MN1"


#: Timeframes that only MT5 supports.
MT5_ONLY_TIMEFRAMES = frozenset(
    {
        Timeframe.M2,
        Timeframe.M3,
        Timeframe.H2,
        Timeframe.H6,
        Timeframe.H8,
        Timeframe.H12,
    }
)
