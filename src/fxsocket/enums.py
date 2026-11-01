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

    MT5 accepts all eight. MT4 has no stop-limit order type: its terminal
    API rejects ``BuyStopLimit``/``SellStopLimit`` (HTTP 400
    ``MRPC_UNSUPPORTED``), so the SDK raises
    :class:`~fxsocket.UnsupportedOnPlatformError` for them on MT4 before
    anything is sent.
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


class OrderOutcome(str, Enum):
    """Semantic classification of a trade result (``OrderResult.outcome``).

    ``success`` only tells you applied-or-not; ``outcome`` additionally
    separates a benign no-op and a partial fill from a genuine rejection, so
    clients don't have to hardcode retcode tables. Empty on bridges older than
    MT5 0.6.1 / MT4 0.5.1.
    """

    APPLIED = "applied"       #: retcode 10009 (done) / 10008 (placed)
    NO_CHANGE = "no_change"   #: retcode 10025 — requested state already in effect
    PARTIAL = "partial"       #: retcode 10010 (done partially)
    REJECTED = "rejected"     #: anything else — inspect ``retcode`` / ``comment``


class PrivateServerStatus(str, Enum):
    """Lifecycle of a private hosting server (v1 ``status``)."""

    PENDING_PAYMENT = "pending_payment"
    PROVISIONING = "provisioning"
    READY = "ready"
    RESIZING = "resizing"
    EXPIRED = "expired"


class PrivateAccountStatus(str, Enum):
    """Lifecycle of an account living on a private server (v1 ``status``)."""

    PROVISIONING = "provisioning"
    READY = "ready"
    ERROR = "error"
    EXPIRED = "expired"


class KeyScope(str, Enum):
    """What a named read-only key may see (v1 ``scope``).

    ``ALL`` covers every account; ``SELECTED`` only the accounts attached to
    the key — everything else is invisible to it (absent from lists, 404 by
    id, 401 at the terminal).
    """

    ALL = "all"
    SELECTED = "selected"


class SymbolMatch(str, Enum):
    """How a multi-account close selector matches symbols.

    ``EXACT`` (the default) matches the symbol exactly as typed. ``BASE``
    also accepts a broker suffix, so ``EURUSD`` reaches ``EURUSD.sd`` and
    ``EURUSDm`` — useful when one selector spans brokers that spell an
    instrument differently. A suffix is separator-led (``.sd``) or at most
    two alphanumerics (``m``), so ``EUR`` never matches ``EURUSD``.
    """

    EXACT = "exact"
    BASE = "base"


class CloseSide(str, Enum):
    """Which direction a multi-account close selector touches."""

    LONG = "long"
    SHORT = "short"
    ANY = "any"


class CloseKind(str, Enum):
    """What a multi-account close selector touches.

    ``POSITION`` (the default) closes open positions only, ``PENDING``
    deletes pending orders only, ``ANY`` does both.
    """

    POSITION = "position"
    PENDING = "pending"
    ANY = "any"


class OrderLegStatus(str, Enum):
    """Outcome of one leg of a multi-account order batch
    (``OrderLegResult.status``). Only ``FILLED`` means the broker took it.

    ``TIMEOUT`` is *unknown*: the order may well have reached the broker.
    Never blind-retry one — replay with the same ``idempotency_key`` or
    reconcile against the account's ``opened_orders()``.
    """

    FILLED = "filled"          #: the terminal replied and the broker accepted
    REJECTED = "rejected"      #: the broker refused — ``retcode`` says why
    INVALID = "invalid"        #: the terminal refused the request itself
    UNAVAILABLE = "unavailable"  #: terminal up but not trading yet
    UNREACHABLE = "unreachable"  #: no terminal to talk to
    TIMEOUT = "timeout"        #: unknown — may or may not have executed


class CloseLegStatus(str, Enum):
    """Outcome for one account of a multi-account close batch
    (``CloseLegResult.status``).

    ``NOTHING_MATCHED`` is a normal answer, not an error. ``UNAVAILABLE`` /
    ``UNREACHABLE`` / ``INVALID`` mean the lookup itself failed, so nothing
    is known about what is open there. ``TIMEOUT`` means orders *may* have
    closed.
    """

    CLOSED = "closed"                  #: every matched order closed
    PARTIAL = "partial"                #: some closed, some did not
    FAILED = "failed"                  #: orders matched, none closed
    NOTHING_MATCHED = "nothing_matched"  #: the selector found nothing
    UNAVAILABLE = "unavailable"
    UNREACHABLE = "unreachable"
    TIMEOUT = "timeout"
    INVALID = "invalid"


class ClosedTicketStatus(str, Enum):
    """Outcome for one ticket of a multi-account close batch
    (``ClosedTicket.status``).

    ``SKIPPED`` means it was never sent (per-account cap or batch deadline),
    so it definitely did not close. ``TIMEOUT`` means it was sent and never
    answered, so it may well have.
    """

    CLOSED = "closed"
    REJECTED = "rejected"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"
    UNREACHABLE = "unreachable"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class OrderKind(str, Enum):
    """Whether an opened row is a live position or a resting pending order."""

    POSITION = "Position"
    PENDING = "Pending"


class DealEntry(str, Enum):
    """Direction of a deal in trade history / the ``trades`` stream.

    ``UNKNOWN`` appears only on degraded ``trades``-stream frames, where the
    bridge could not resolve the deal direction in time — see
    ``TradeEventData.degraded``.
    """

    IN = "In"
    OUT = "Out"
    IN_OUT = "InOut"
    OUT_BY = "OutBy"
    UNKNOWN = "Unknown"


class OrderEventKind(str, Enum):
    """Step in a pending order's life, on the ``orders`` stream
    (``OrderEventData.event``)."""

    PLACED = "placed"
    MODIFIED = "modified"  #: price, stops, expiry, volume or type changed
    DELETED = "deleted"
    EXPIRED = "expired"
    TRIGGERED = "triggered"  #: it filled; ``position`` is the resulting position


class ExpirationMode(str, Enum):
    """How a pending order expires (``PendingOrder.expiration_mode``, and the
    modes a symbol allows in ``SymbolInfo.expiration_modes``).

    MT4 only knows ``GTC`` and ``SPECIFIED``.
    """

    GTC = "gtc"  #: until cancelled
    DAY = "day"  #: end of the trading day
    SPECIFIED = "specified"  #: a given date and time
    SPECIFIED_DAY = "specifiedDay"  #: end of a given day


class TerminalErrorCode(str, Enum):
    """The fixed ``error`` codes of the terminal API (``FxSocketError.code``).

    Branch on these, never on the message. Bridges before MT5 0.17.0 /
    MT4 0.15.0 answered most failures with the generic ``MRPC`` instead.
    """

    UNAUTHORIZED = "UNAUTHORIZED"  #: 401, missing or wrong key
    READ_ONLY_KEY = "READ_ONLY_KEY"  #: 403, a read-only key on a trade route
    MRPC_VALIDATION = "MRPC_VALIDATION"  #: 400, bad request parameters
    MRPC_UNKNOWN_TICKET = "MRPC_UNKNOWN_TICKET"  #: 400, no such order/position
    MRPC_UNKNOWN_SYMBOL = "MRPC_UNKNOWN_SYMBOL"  #: 400, no such symbol
    MRPC_BAD_TIMEFRAME = "MRPC_BAD_TIMEFRAME"  #: 400, timeframe not recognized
    MRPC_UNSUPPORTED = "MRPC_UNSUPPORTED"  #: 400, MT4 cannot do this
    #: 500, the symbol has no quote yet (warm it with ``symbol_select``)
    MRPC_SYMBOL_NOT_QUOTING = "MRPC_SYMBOL_NOT_QUOTING"
    #: 500, another request for this ticket is still at the dealer
    MRPC_TICKET_BUSY = "MRPC_TICKET_BUSY"
    #: 409, ``client_order_id`` already used for a different order
    MRPC_CLIENT_ORDER_ID_REUSED = "MRPC_CLIENT_ORDER_ID_REUSED"
    MRPC_TRADE_EA_NOT_READY = "MRPC_TRADE_EA_NOT_READY"  #: 503, nothing executed
    MRPC_TRADE_EA_STOPPED = "MRPC_TRADE_EA_STOPPED"  #: 500, nothing executed
    #: 504, gave up before the terminal took the command (not executed)
    MRPC_ABANDONED = "MRPC_ABANDONED"
    MRPC_TIMEOUT = "MRPC_TIMEOUT"  #: 504, a read timed out
    #: 504, a trade timed out after the terminal took it (may have executed)
    MRPC_TRADE_OUTCOME_UNKNOWN = "MRPC_TRADE_OUTCOME_UNKNOWN"
    MRPC_HISTORY_UNAVAILABLE = "MRPC_HISTORY_UNAVAILABLE"  #: 500
    MRPC_PRICE_HISTORY_UNAVAILABLE = "MRPC_PRICE_HISTORY_UNAVAILABLE"  #: 500
    MRPC_CALC_FAILED = "MRPC_CALC_FAILED"  #: 500
    MRPC_INTERNAL = "MRPC_INTERNAL"  #: 500, bridge fault
    MRPC_UNKNOWN_COMMAND = "MRPC_UNKNOWN_COMMAND"  #: 500, bridge fault
    MRPC = "MRPC"  #: generic code of bridges before MT5 0.17.0 / MT4 0.15.0


class HealthStatus(str, Enum):
    """Roll-up status reported by the terminal ``/status`` endpoint."""

    READY = "ready"
    STARTING = "starting"
    DEGRADED = "degraded"
    DOWN = "down"


class Timeframe(str, Enum):
    """Candle timeframes.

    Exactly the nine the terminal API accepts, on MT4 and MT5 alike (REST
    ``price_history`` and the ``bars`` stream). MT5's other native periods
    (``M2``, ``H2``, ``H12`` …) are not exposed by the terminal API, so they
    are not members.
    """

    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"
    MN1 = "MN1"


#: Deprecated, always empty: both platforms accept every :class:`Timeframe`.
#: Kept so existing imports don't break; will be removed in 1.0.
MT5_ONLY_TIMEFRAMES: frozenset[Timeframe] = frozenset()
