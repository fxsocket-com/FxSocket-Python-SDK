"""Pydantic models for FxSocket API payloads.

Two families:

* The management model (:class:`Account`) — the v1 API, which already speaks
  ``snake_case`` and returns a genuine UTC ``created_at``.
* Terminal payloads — ``camelCase`` on the wire (accepted via aliases). Two
  deliberate typing choices keep these robust:

  - **MetaTrader vocabulary fields** (``type``, ``kind``, ``entry``,
    ``status``) are plain ``str``, not enums: the exact serialized casing
    varies and a strict enum would raise on an unrecognized value. Compare
    them against the str-enums in :mod:`fxsocket.enums` (``OrderOperation``,
    ``HealthStatus``, …) — those compare equal to the raw string.
  - **Timestamps** are ``str``, not ``datetime``: they are in *broker server
    time* with a stylistic trailing ``Z``, so decoding them as UTC would be
    silently wrong. Use :class:`ServerTimezone` to convert if needed.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from .enums import Platform, TradingStatus


class _Camel(BaseModel):
    """Terminal payloads: read camelCase aliases, also accept field names."""

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
        extra="ignore",
    )


# --------------------------------------------------------------------------- #
# Management API (v1)
# --------------------------------------------------------------------------- #


class Account(BaseModel):
    """A linked trading account, as returned by the management API (v1).

    ``rest_url`` / ``ws_url`` are where this account's terminal REST and
    WebSocket APIs live. Both are empty until the account has a reachable
    terminal (shared pod or private droplet); a bridge-only account exposes
    none.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    nickname: str = ""
    platform: Platform
    server: str
    login: int
    status: TradingStatus
    error: str = ""
    rest_url: str = ""
    ws_url: str = ""
    created_at: datetime

    @property
    def has_terminal(self) -> bool:
        """True when this account exposes a reachable terminal API."""
        return bool(self.rest_url)


# --------------------------------------------------------------------------- #
# Terminal — account state
# --------------------------------------------------------------------------- #


class AccountSummary(_Camel):
    """Live financial snapshot (``GET /AccountSummary``)."""

    balance: float
    credit: float
    profit: float
    equity: float
    margin: float
    free_margin: float
    margin_level: float
    leverage: int
    currency: str
    type: str


class AccountInfo(_Camel):
    """Static account identity + configuration (``GET /AccountInfo``).

    On MT4 ``margin_mode`` is always ``"Hedging"`` and ``fifo_close`` always
    ``False`` (the platform has no native equivalent).
    """

    name: str
    login: int
    server: str
    company: str
    currency: str
    currency_digits: int
    leverage: int
    type: str
    margin_mode: str
    margin_so_mode: str
    margin_call_level: float
    stop_out_level: float
    trade_allowed: bool
    trade_expert: bool
    limit_orders: int
    fifo_close: bool


class OpenedOrder(_Camel):
    """An open position or resting pending order (``GET /OpenedOrders``)."""

    ticket: int
    symbol: str
    type: str
    kind: str
    lots: float
    open_price: float
    current_price: float
    stop_loss: float
    take_profit: float
    swap: float
    profit: float
    magic: int
    comment: str
    open_time: str

    @property
    def is_pending(self) -> bool:
        """True for a resting pending order (vs. a live position)."""
        return self.kind.lower() == "pending"


class HistoryTrade(_Camel):
    """A historical deal / closed order (``GET /OrderHistory``).

    On MT4 this is one row per closed *order* (no per-deal granularity);
    ``order`` aliases the ticket and ``entry`` is constant.
    """

    ticket: int
    order: int
    symbol: str
    type: str
    entry: str
    volume: float
    price: float
    commission: float
    swap: float
    profit: float
    magic: int
    comment: str
    time: str


class PositionTrade(_Camel):
    """A closed round-trip position (``GET /PositionHistory``)."""

    position_id: int
    symbol: str
    type: str
    volume: float
    open_time: str
    open_price: float
    close_time: str
    close_price: float
    profit: float
    swap: float
    commission: float
    net_profit: float
    magic: int
    comment: str


class ServerTimezone(_Camel):
    """Broker server clock + UTC offset (``GET /ServerTimezone``).

    ``utc_offset_seconds`` is ``server_time - UTC``; subtract it from a
    broker-server timestamp to get UTC.
    """

    server_time: str
    utc_offset_seconds: int


# --------------------------------------------------------------------------- #
# Terminal — market data
# --------------------------------------------------------------------------- #


class Quote(_Camel):
    """Latest tick for a symbol (``GET /getQuote``).

    ``last`` / ``volume`` are ~0 on forex (and always 0 on MT4).
    """

    symbol: str
    bid: float
    ask: float
    time: str
    last: float
    volume: int


class SymbolInfo(_Camel):
    """Contract specification for a symbol (``GET /SymbolInfo``)."""

    symbol: str
    description: str
    digits: int
    point: float
    tick_size: float
    tick_value: float
    contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level: int
    freeze_level: int
    spread: int
    trade_mode: str
    swap_long: float
    swap_short: float
    bid: float
    ask: float
    currency_base: str
    currency_profit: str
    currency_margin: str


class Candle(_Camel):
    """One OHLC bar (``GET /PriceHistory``). ``real_volume`` is 0 on MT4."""

    time: str
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    real_volume: int
    spread: int


# --------------------------------------------------------------------------- #
# Terminal — trading
# --------------------------------------------------------------------------- #


class OrderResult(_Camel):
    """Result of an order send / modify / close.

    ``success`` is true when ``retcode`` is DONE (10009) or PLACED (10010).
    ``deal`` is the executed deal ticket (0 for pending placement, and always
    0 on MT4); ``order`` is the resulting position / pending-order ticket.
    """

    success: bool
    retcode: int
    retcode_description: str
    deal: int
    order: int
    volume: float
    price: float
    bid: float
    ask: float
    comment: str


class MarginCalc(_Camel):
    """Required margin for a hypothetical order (``GET /OrderCalcMargin``)."""

    symbol: str
    operation: str
    volume: float
    price: float
    margin: float
    currency: str


class ProfitCalc(_Camel):
    """Projected P/L for a hypothetical trade (``GET /OrderCalcProfit``)."""

    symbol: str
    operation: str
    volume: float
    price_open: float
    price_close: float
    profit: float
    currency: str


# --------------------------------------------------------------------------- #
# Terminal — health
# --------------------------------------------------------------------------- #


class TerminalHealth(_Camel):
    alive: bool
    build: int = 0
    ping_ms: int = 0


class BrokerHealth(_Camel):
    connected: bool
    server: str = ""


class AccountHealth(_Camel):
    """Account section of ``/status``. ``currency`` / ``type`` are blank when
    not logged in; ``login`` is always the configured account."""

    logged_in: bool
    login: int = 0
    currency: str = ""
    type: str = ""
    trade_allowed: bool = False


class BridgeHealth(_Camel):
    version: str = ""
    trade_ea_ready: bool = False
    symbols_synced: bool = False


class Health(_Camel):
    """Full health snapshot (``GET /status``) — always HTTP 200.

    ``status`` is one of ``ready`` / ``starting`` / ``degraded`` / ``down``
    (compare against :class:`fxsocket.HealthStatus`).
    """

    status: str
    terminal: TerminalHealth
    broker: BrokerHealth
    account: AccountHealth
    bridge: BridgeHealth
    server_time: str = ""

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"


class HealthChecks(_Camel):
    """PII-free probe body from ``/healthz`` and ``/livez``."""

    status: str
    terminal: bool
    broker: bool
    account: bool
