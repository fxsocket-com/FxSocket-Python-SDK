"""Pydantic models for FxSocket API payloads.

Two families:

* Management models (:class:`Account`, the multi-account trading batch
  models, :class:`Wallet`) — the v1 API, which already speaks ``snake_case``
  and returns genuine UTC ``datetime`` values.
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

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from .enums import (
    ClosedTicketStatus,
    CloseKind,
    CloseLegStatus,
    CloseSide,
    KeyScope,
    OrderLegStatus,
    OrderOperation,
    OrderOutcome,
    Platform,
    SymbolMatch,
    TradingStatus,
)


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

    ``proxy_address`` / ``proxy_type`` / ``proxy_local_port`` describe the
    outbound proxy the terminal is routed through (empty / ``None`` when
    there is none; the proxy credentials are never returned).
    ``trade_ea_symbol`` is the chart symbol hosting the trade expert —
    empty means the terminal picked one automatically.
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
    proxy_address: str = ""
    proxy_type: str = ""
    proxy_local_port: int | None = None
    trade_ea_symbol: str = ""
    created_at: datetime

    @property
    def has_terminal(self) -> bool:
        """True when this account exposes a reachable terminal API."""
        return bool(self.rest_url)


class PrivateServerAccount(BaseModel):
    """An MT4/MT5 account living on a private server (v1 API).

    ``status`` is the private-hosting lifecycle — compare against
    :class:`fxsocket.PrivateAccountStatus` (provisioning / ready / error /
    expired). ``rest_url`` / ``ws_url`` are the account's terminal API on
    the server's dedicated IP. Private servers use a self-signed TLS
    certificate, so pass ``verify=False`` (or construct the client with
    ``verify_terminal_tls=False``) when calling ``client.terminal(...)``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    nickname: str = ""
    platform: Platform
    server: str
    login: int
    status: str
    rest_url: str = ""
    ws_url: str = ""
    trade_ea_symbol: str = ""
    created_at: datetime

    @property
    def has_terminal(self) -> bool:
        """True when this account exposes a reachable terminal API."""
        return bool(self.rest_url)


class PrivateServer(BaseModel):
    """A dedicated private hosting server (v1 API).

    ``status`` is the server lifecycle — compare against
    :class:`fxsocket.PrivateServerStatus`. ``purchased_slots`` is the paid
    limit; ``used_slots`` how many accounts currently live on the server.
    ``cancel_at_period_end`` is true once the server has been told to stop
    instead of renewing — it then runs until ``period_end`` and expires.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    name: str = ""
    status: str
    region: str = ""
    ip: str = ""
    purchased_slots: int = 0
    used_slots: int = 0
    cancel_at_period_end: bool = False
    period_end: datetime | None = None
    accounts: list[PrivateServerAccount] = []

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"

    @property
    def free_slots(self) -> int:
        return max(self.purchased_slots - self.used_slots, 0)


class Region(BaseModel):
    """One place a private server can run in."""

    model_config = ConfigDict(extra="ignore")

    code: str
    label: str = ""


class PrivateServerOptions(BaseModel):
    """Where private servers may run, how big they may be and what that
    costs (``GET /v1/private-servers/regions``).

    ``enabled`` is false when private hosting is off for the deployment —
    ``regions`` is then empty. Prices are integer EUR cents, and a server
    costs ``first_slot_eur_cents + additional_slot_eur_cents * (slots -
    1)`` per month; :meth:`monthly_price_eur_cents` does that arithmetic.
    Treat the returned list as authoritative rather than hardcoding region
    slugs.
    """

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    regions: list[Region] = []
    max_slots: int = 0
    max_servers: int = 0
    first_slot_eur_cents: int = 0
    additional_slot_eur_cents: int = 0

    @property
    def region_codes(self) -> list[str]:
        """Just the slugs, in the order the API returned them."""
        return [region.code for region in self.regions]

    def monthly_price_eur_cents(self, slots: int) -> int:
        """What a server of ``slots`` accounts costs per month, in cents."""
        if slots < 1:
            raise ValueError("slots must be at least 1")
        return self.first_slot_eur_cents + self.additional_slot_eur_cents * (slots - 1)

    def monthly_price_eur(self, slots: int) -> Decimal:
        """:meth:`monthly_price_eur_cents` as euros."""
        return _eur(self.monthly_price_eur_cents(slots))


# --------------------------------------------------------------------------- #
# Management API (v1) — read-only keys (``/v1/readonly-keys``)
# --------------------------------------------------------------------------- #


class ScopedAccount(BaseModel):
    """Compact account shape attached to a scoped read-only key."""

    model_config = ConfigDict(extra="ignore")

    id: str
    nickname: str = ""
    platform: Platform
    server: str = ""
    login: int = 0


class ReadOnlyKey(BaseModel):
    """A named read-only API key (``fxs_ro_…``), as returned by the v1 API.

    ``key`` is the plaintext secret — it is returned to its owner on every
    read (there is no show-once step), so treat any object holding one as
    sensitive. ``scope`` compares against :class:`fxsocket.KeyScope`:
    ``"all"`` sees every account, ``"selected"`` only ``accounts``.
    ``last_used_at`` is ``None`` until the key has authenticated once.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str = ""
    key: str
    scope: str
    accounts: list[ScopedAccount] = []
    created_at: datetime
    last_used_at: datetime | None = None

    @property
    def is_scoped(self) -> bool:
        """True when the key only sees the accounts attached to it."""
        return self.scope == KeyScope.SELECTED

    @property
    def account_ids(self) -> list[str]:
        return [a.id for a in self.accounts]


# --------------------------------------------------------------------------- #
# Management API (v1) — multi-account trading (``/v1/orders``)
# --------------------------------------------------------------------------- #


def _id_of_account(value: Any) -> Any:
    """Let ``account_id`` fields take an :class:`Account` /
    :class:`PrivateServerAccount` as well as a bare id string."""
    if isinstance(value, (Account, PrivateServerAccount)):
        return value.id
    return value


class _OrderFields(BaseModel):
    """Order parameters shared by :class:`OrderDefaults` and :class:`OrderLeg`.

    Every field is optional here: a leg only needs what its batch
    ``defaults`` don't already say. Unknown fields are rejected, mirroring
    the API (an unknown field fails the whole batch with ``400``).
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    #: Exactly as the account's broker names it (``EURUSD``, ``EURUSD.sd``…).
    symbol: str | None = None
    #: ``Buy``, ``SellLimit``, … — case-insensitive, or an :class:`OrderOperation`.
    operation: OrderOperation | str | None = None
    #: Lots; must be > 0.
    volume: float | None = None
    #: Entry price — required for pending orders, ignored for market orders.
    price: float | None = None
    #: Points; the terminal defaults to 10 when omitted.
    slippage: int | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    #: Required for ``*StopLimit`` operations.
    stop_limit_price: float | None = None
    #: Omit for good-till-cancelled.
    expiration: str | datetime | date | None = None
    comment: str | None = None
    #: Magic number written onto the order (``expert_id`` on the wire).
    magic: int | None = Field(
        default=None,
        validation_alias=AliasChoices("magic", "expert_id"),
        serialization_alias="expert_id",
    )


class OrderDefaults(_OrderFields):
    """Values every leg of a ``client.orders.send()`` batch inherits unless
    the leg overrides them — "same trade, three accounts, three lot sizes"
    stays short while nothing is locked down."""


class OrderLeg(_OrderFields):
    """One order aimed at one account, for ``client.orders.send()``.

    Only ``account_id`` is mandatory (an :class:`Account` /
    :class:`PrivateServerAccount` is accepted in its place); everything
    else may come from the batch :class:`OrderDefaults`. A value set here
    wins, *including a falsy one*: ``slippage=0`` really means zero, not
    "fall back to the default".
    """

    account_id: str

    @field_validator("account_id", mode="before")
    @classmethod
    def _coerce_account(cls, value: Any) -> Any:
        return _id_of_account(value)


class OrderLegResult(BaseModel):
    """What happened to one leg of a ``client.orders.send()`` batch.

    ``status`` is the honest answer (compare against
    :class:`fxsocket.OrderLegStatus`) and only ``"filled"`` means the broker
    took it. ``"timeout"`` is **unknown** — the order may well have reached
    the broker; never blind-retry it (see :attr:`is_unknown`).

    ``order`` / ``deal`` are the resulting tickets (0 if none), ``retcode``
    the platform's own return code (0 when it never got that far) and
    ``message`` the broker's order comment on a reply, otherwise why this
    leg did not get one. ``volume`` is what the broker reported filling
    where it reported one, otherwise the volume requested.
    """

    model_config = ConfigDict(extra="ignore")

    account_id: str
    platform: str
    symbol: str
    operation: str
    volume: float
    status: str
    order: int = 0
    deal: int = 0
    price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    retcode: int = 0
    retcode_description: str = ""
    message: str = ""
    latency_ms: int = 0

    @property
    def is_filled(self) -> bool:
        """True when the broker accepted the order."""
        return self.status == OrderLegStatus.FILLED

    @property
    def is_unknown(self) -> bool:
        """True when the leg timed out — it may or may not have executed.
        Replay with the same ``idempotency_key`` or reconcile against the
        account's ``opened_orders()`` rather than re-sending."""
        return self.status == OrderLegStatus.TIMEOUT


class BatchOrderSummary(BaseModel):
    """Counts for one ``client.orders.send()`` batch.

    ``failed`` legs provably never reached the broker; ``unknown`` legs
    timed out and may or may not have executed — they are kept apart so a
    retry is a decision, not a reflex.
    """

    model_config = ConfigDict(extra="ignore")

    requested: int
    filled: int
    failed: int
    unknown: int


class BatchOrderResult(BaseModel):
    """Reply of ``client.orders.send()`` (``POST /v1/orders``).

    ``results`` is *positional* — it mirrors the ``orders`` you sent one for
    one, so ``zip(orders, result.results)``. Don't match on ``account_id``:
    it repeats when several legs target the same account.

    ``idempotent_replay`` is true when this is the stored reply of an
    earlier batch with the same ``idempotency_key`` — nothing was sent.
    """

    model_config = ConfigDict(extra="ignore")

    batch_id: str
    idempotent_replay: bool = False
    summary: BatchOrderSummary
    results: list[OrderLegResult] = []

    @property
    def all_filled(self) -> bool:
        """True when every leg was accepted by its broker."""
        return self.summary.filled == self.summary.requested

    @property
    def filled_legs(self) -> list[OrderLegResult]:
        return [r for r in self.results if r.is_filled]

    @property
    def failed_legs(self) -> list[OrderLegResult]:
        """Legs that provably did not execute (not filled, not a timeout)."""
        return [r for r in self.results if not r.is_filled and not r.is_unknown]

    @property
    def unknown_legs(self) -> list[OrderLegResult]:
        """Legs that timed out — they may or may not have executed."""
        return [r for r in self.results if r.is_unknown]


class _CloseFields(BaseModel):
    """Selector shared by :class:`CloseDefaults` and :class:`CloseLeg`."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    #: Symbol to close, as the broker names it. ``"*"`` closes every symbol
    #: and must be typed literally — an omitted symbol is an error, never a
    #: silent close-everything.
    symbol: str | None = None
    #: ``exact`` (default) or ``base`` — see :class:`fxsocket.SymbolMatch`.
    symbol_match: SymbolMatch | str | None = None
    #: ``long``, ``short`` or ``any`` (default).
    side: CloseSide | str | None = None
    #: ``position`` (default), ``pending`` or ``any``.
    kind: CloseKind | str | None = None
    #: Only touch orders carrying this magic number.
    magic: int | None = None
    #: Partial-close volume per matched position; omit for a full close.
    volume: float | None = None
    #: Points; the terminal defaults to 10 when omitted.
    slippage: int | None = None


class CloseDefaults(_CloseFields):
    """Selector values every account of a ``client.orders.close()`` batch
    inherits unless it overrides them."""


class CloseLeg(_CloseFields):
    """What to close on one account, for ``client.orders.close()``.

    Either a selector (``symbol`` and friends — possibly inherited from the
    batch :class:`CloseDefaults`) or an explicit ``tickets`` list, never
    both. Tickets go stale the moment a stop fires, so prefer a selector
    unless you read them from ``opened_orders()`` moments ago.
    """

    account_id: str
    tickets: list[int] | None = None

    @field_validator("account_id", mode="before")
    @classmethod
    def _coerce_account(cls, value: Any) -> Any:
        return _id_of_account(value)


class ClosedTicket(BaseModel):
    """What happened to one ticket in a ``client.orders.close()`` batch.

    ``status`` compares against :class:`fxsocket.ClosedTicketStatus`.
    ``"skipped"`` means it was never sent (the per-account cap or the batch
    deadline), so it definitely did not close; ``"timeout"`` means it was
    sent and never answered, so it may well have. ``kind`` is
    ``"position"`` or ``"pending"``; ``type`` is ``Buy``, ``SellLimit``, ….
    """

    model_config = ConfigDict(extra="ignore")

    ticket: int
    symbol: str = ""
    type: str = ""
    kind: str = ""
    volume: float = 0.0
    status: str
    retcode: int = 0
    retcode_description: str = ""
    price: float = 0.0
    message: str = ""
    latency_ms: int = 0

    @property
    def is_closed(self) -> bool:
        return self.status == ClosedTicketStatus.CLOSED

    @property
    def is_unknown(self) -> bool:
        """True when the close timed out — it may or may not have happened."""
        return self.status == ClosedTicketStatus.TIMEOUT

    @property
    def is_pending(self) -> bool:
        """True when this row is a pending order (vs. a position)."""
        return self.kind.lower() == "pending"


class CloseLegResult(BaseModel):
    """What happened on one account of a ``client.orders.close()`` batch.

    ``status`` compares against :class:`fxsocket.CloseLegStatus`:

    - ``closed`` — every matched order closed.
    - ``partial`` — some closed, some did not; read ``results``.
    - ``failed`` — orders matched and none of them closed.
    - ``nothing_matched`` — the selector found nothing. Not an error.
    - ``unavailable`` / ``unreachable`` / ``invalid`` — the lookup itself
      failed, so nothing is known about what is open there (``matched`` is
      0 and says nothing).
    - ``timeout`` — the lookup or the account's whole job ran out of time.
      Orders may have closed.
    """

    model_config = ConfigDict(extra="ignore")

    account_id: str
    platform: str = ""
    status: str
    matched: int = 0
    closed: int = 0
    message: str = ""
    latency_ms: int = 0
    results: list[ClosedTicket] = []

    @property
    def is_closed(self) -> bool:
        """True when every matched order closed."""
        return self.status == CloseLegStatus.CLOSED

    @property
    def nothing_matched(self) -> bool:
        return self.status == CloseLegStatus.NOTHING_MATCHED

    @property
    def is_unknown(self) -> bool:
        """True when this account's job timed out — orders may have closed."""
        return self.status == CloseLegStatus.TIMEOUT


class BatchCloseSummary(BaseModel):
    """Counts for one ``client.orders.close()`` batch.

    ``failed`` tickets provably did not close; ``unknown`` tickets timed out
    and may or may not have. ``accounts_unknown`` are accounts whose ticket
    list was never established — their ``matched`` is 0 and says nothing
    about what is actually open there.
    """

    model_config = ConfigDict(extra="ignore")

    accounts: int
    matched: int
    closed: int
    failed: int
    unknown: int
    accounts_unknown: int = 0


class BatchCloseResult(BaseModel):
    """Reply of ``client.orders.close()`` (``POST /v1/orders/close``).

    ``results`` mirrors the ``accounts`` you sent one for one, in request
    order. ``idempotent_replay`` is true when this is the stored reply of
    an earlier batch with the same ``idempotency_key`` — nothing was sent.
    """

    model_config = ConfigDict(extra="ignore")

    batch_id: str
    idempotent_replay: bool = False
    summary: BatchCloseSummary
    results: list[CloseLegResult] = []

    @property
    def all_closed(self) -> bool:
        """True when nothing failed, nothing timed out and every account's
        open orders could be established — a selector that matched nothing
        counts as done."""
        s = self.summary
        return s.failed == 0 and s.unknown == 0 and s.accounts_unknown == 0

    @property
    def unknown_accounts(self) -> list[CloseLegResult]:
        """Accounts whose job timed out — orders there may have closed."""
        return [r for r in self.results if r.is_unknown]


# --------------------------------------------------------------------------- #
# Management API (v1) — wallet (``/v1/wallet``, read-only)
# --------------------------------------------------------------------------- #


def _eur(cents: int) -> Decimal:
    return Decimal(cents) / Decimal(100)


class TopUp(BaseModel):
    """A prepaid-balance top-up order.

    ``status`` is ``pending``, ``partial``, ``paid`` or ``failed``.
    ``credited_eur_cents`` is what has actually landed so far — lower than
    ``amount_eur_cents`` while a payment is short. ``deposit_amount`` is the
    provider's raw *unscaled* integer string; ``deposit_amount_decimal`` is
    the human-readable amount. The quote lapses at ``expires_at``.
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    status: str
    amount_eur_cents: int
    credited_eur_cents: int = 0
    deposit_address: str = ""
    deposit_amount: str = ""
    deposit_amount_decimal: str = ""
    asset_code: str = ""
    blockchain_code: str = ""
    expires_at: datetime | None = None

    @property
    def amount_eur(self) -> Decimal:
        return _eur(self.amount_eur_cents)

    @property
    def credited_eur(self) -> Decimal:
        return _eur(self.credited_eur_cents)


class UpcomingCharge(BaseModel):
    """One thing the prepaid balance is going to pay for, and when.

    ``kind`` is ``"seat"`` (an account seat) or ``"server"`` (a
    balance-funded private server); ``label`` names the account or server.
    """

    model_config = ConfigDict(extra="ignore")

    when: datetime
    amount_eur_cents: int
    kind: str
    label: str = ""

    @property
    def amount_eur(self) -> Decimal:
        return _eur(self.amount_eur_cents)


class Wallet(BaseModel):
    """Your prepaid balance: what is in it, what has been asked for but has
    not landed yet, and what it is going to pay for over the next 30 days
    (``GET /v1/wallet``).

    ``upcoming`` is a projection in date order covering account seats and
    balance-funded private servers together, since they share the one
    balance. Affordability is cumulative: with 24 EUR and three 12 EUR
    renewals the first two are covered and the third is not, which is why
    ``shortfall_eur_cents`` is the *total* gap (0 when covered), not the
    size of any single charge. All amounts are integer EUR cents; the
    ``*_eur`` properties give :class:`~decimal.Decimal` euros.
    """

    model_config = ConfigDict(extra="ignore")

    balance_eur_cents: int
    pending_topups: list[TopUp] = []
    upcoming: list[UpcomingCharge] = []
    upcoming_total_eur_cents: int = 0
    shortfall_eur_cents: int = 0
    covers_upcoming: bool = True

    @property
    def balance_eur(self) -> Decimal:
        return _eur(self.balance_eur_cents)

    @property
    def upcoming_total_eur(self) -> Decimal:
        return _eur(self.upcoming_total_eur_cents)

    @property
    def shortfall_eur(self) -> Decimal:
        """How much to top up to cover everything in ``upcoming``."""
        return _eur(self.shortfall_eur_cents)


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

    ``position`` groups the rows of one round-trip: on MT5 it is the deal's
    ``DEAL_POSITION_ID`` — the ``In`` and ``Out`` rows share it, and it
    equals the ``trades``-stream events' ``position`` and
    :attr:`PositionTrade.position_id` — so an exit row alone identifies the
    position it closed even though MT5 exits usually carry ``magic=0`` /
    ``comment=""``. On MT4 it equals the order ticket. 0 on pods older than
    bridge MT5 0.14 / MT4 0.13. (Netting-account caveat: a reversal
    ``InOut`` row reports the position it belongs to *after* processing.)
    """

    ticket: int
    order: int
    position: int = 0
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


class CommissionTier(_Camel):
    """One tier of a commission rule — a value and the volume/turnover range
    it applies to.

    Enum-like fields (``mode``, ``volume_type``) carry the raw MQL5 constant
    names (e.g. ``SYMBOL_COMMISSION_MODE_MONEY``) so nothing is lost in
    translation. ``range_to == 0`` means unbounded; ``min_value`` /
    ``max_value`` cap the charged amount (0 = no cap).
    """

    mode: str
    volume_type: str
    value: float
    min_value: float
    max_value: float
    range_from: float
    range_to: float
    currency: str


class CommissionRule(_Camel):
    """One broker commission rule for a symbol, as configured server-side
    (MT5 ``SymbolInfoCommissions``).

    A symbol can carry several rules; each has its own tiers. The mode fields
    carry the MQL5 ``ENUM_SYMBOL_COMMISSION_*`` constant names verbatim.
    """

    currency: str
    range_mode: str
    charge_mode: str
    entry_mode: str
    direction_mode: str
    profit_mode: str
    tiers: list[CommissionTier] = []


class TradingSession(_Camel):
    """One trading-session window of a symbol, in *broker server time*.

    ``day`` uses the MQL ``ENUM_DAY_OF_WEEK`` constant names (``SUNDAY`` …
    ``SATURDAY``); times are ``HH:MM`` where ``24:00`` means end of day, so a
    24-hour market shows ``00:00``–``24:00``.
    """

    day: str
    from_: str = Field(alias="from")
    to: str


class SymbolInfo(_Camel):
    """Contract specification for a symbol (``GET /SymbolInfo``).

    ``commissions`` are the broker's commission rules straight from the
    server's symbol specification; ``sessions`` are the per-weekday trading
    windows in broker server time. Both default to empty on pods older than
    bridge 0.10 (and ``commissions`` also when the broker publishes none or
    the terminal predates the API — build 6060+).
    """

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
    commissions: list[CommissionRule] = []
    sessions: list[TradingSession] = []


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

    ``success`` is true when ``retcode`` is DONE (10009) or PLACED (10008).

    ``outcome`` classifies the result further — ``"applied"`` /
    ``"no_change"`` / ``"partial"`` / ``"rejected"`` (compare against
    :class:`fxsocket.OrderOutcome`). ``"no_change"`` (retcode 10025) is a
    benign idempotent no-op — the requested SL/TP/price already match the
    current values — so it is safe to treat as applied even though ``success``
    is ``False``. Use :attr:`is_effective` when you only care that the
    requested state is in effect (the idempotent-retry case). ``outcome`` is
    empty on bridges older than MT5 0.6.1 / MT4 0.5.1.

    ``deal`` is the executed deal ticket (0 for pending placement, and always
    0 on MT4); ``order`` is the resulting position / pending-order ticket.
    """

    success: bool
    outcome: str = ""
    retcode: int
    retcode_description: str
    deal: int
    order: int
    volume: float
    price: float
    bid: float
    ask: float
    comment: str

    @property
    def is_no_change(self) -> bool:
        """True for a benign no-op (retcode 10025 / ``outcome == "no_change"``):
        the requested SL/TP/price already match the current values."""
        return self.retcode == 10025 or self.outcome == OrderOutcome.NO_CHANGE

    @property
    def is_effective(self) -> bool:
        """True when the requested state is in effect — either ``success``
        (applied) or a no-op (:attr:`is_no_change`). Use this for idempotent
        SL/TP management, where re-sending an identical modify returns 10025
        with ``success=False``."""
        return self.success or self.is_no_change


class CloseAllResult(_Camel):
    """One per-ticket outcome of a ``/CloseAll`` pass.

    ``kind`` is ``"position"`` (closed) or ``"pending"`` (deleted).
    """

    ticket: int
    kind: str
    success: bool
    retcode: int
    retcode_description: str

    @property
    def is_pending(self) -> bool:
        """True when this row is a deleted pending order (vs. a closed position)."""
        return self.kind.lower() == "pending"


class CloseAllSummary(_Camel):
    """Reply of ``POST /CloseAll`` — every matched position (and pending
    order, when ``delete_pending`` was set) with its close/delete outcome.

    ``requested`` is how many orders the filters matched and were attempted;
    ``closed`` how many attempts the broker accepted; ``failed`` how many it
    rejected — inspect ``results`` for the per-ticket retcodes.
    """

    requested: int
    closed: int
    failed: int
    results: list[CloseAllResult] = []


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
    """Bridge section of ``/status``.

    ``trade_ea_heartbeat_age_ms`` is how long ago the trade EA last made
    dispatcher progress (``-1`` = never registered, or a pod older than
    bridge 0.10). A large age while ``trade_ea_ready`` is still ``True``
    means the EA is blocked in a long dealer call or dead — worth alerting on.
    """

    version: str = ""
    trade_ea_ready: bool = False
    trade_ea_heartbeat_age_ms: int = -1
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


# --------------------------------------------------------------------------- #
# Terminal — streaming payloads (the inner ``data`` of some WS events)
# --------------------------------------------------------------------------- #


class TradeEventData(_Camel):
    """A trade transaction pushed on the ``trades`` stream.

    ``entry`` is the deal direction (compare against
    :class:`fxsocket.DealEntry`; ``"Unknown"`` appears only on degraded
    frames). ``commission`` / ``swap`` / ``magic`` (and a real ``comment`` on
    MT5) arrive on bridges MT5 0.12+ / MT4 0.11+ and default to 0 before
    that; a deal's net P&L is ``profit + commission + swap``
    (:attr:`net_profit`).

    Platform semantics:

    * **MT5** — ``Out`` deals carry ``magic=0`` / ``comment=""`` unless the
      closing request set them (a platform property, not a bridge gap).
      Correlate ``In``/``Out`` through ``position``, which is present on
      every event.
    * **MT4** — orders keep their magic/comment for the whole lifecycle, so
      both ``In`` and ``Out`` events carry them; ``deal`` is always 0 and
      ``position`` equals the order ticket.

    ``degraded=True`` (bridges MT5 0.13+ / MT4 0.12+; structurally always
    ``False`` on MT4) means the bridge could not fully enrich the event in
    time: the identifiers, ``symbol``, ``type``, ``volume`` and ``price`` are
    trustworthy, but ``entry`` is ``"Unknown"`` and ``profit`` /
    ``commission`` / ``swap`` / ``magic`` / ``comment`` are zeroed —
    reconcile the deal via ``GET /OrderHistory``.
    """

    deal: int
    order: int
    position: int
    symbol: str
    type: str
    entry: str
    volume: float
    price: float
    profit: float
    commission: float = 0.0
    swap: float = 0.0
    magic: int = 0
    comment: str
    time: str
    degraded: bool = False

    @property
    def net_profit(self) -> float:
        """Deal P&L including costs: ``profit + commission + swap``."""
        return self.profit + self.commission + self.swap


class TerminalStatusData(_Camel):
    """Terminal status pushed (~1/s) on the ``terminal`` stream."""

    connected: bool
    trade_allowed: bool
    server_time: str
