"""Per-account terminal REST client (MT4 + MT5).

One :class:`TerminalClient` (sync) / :class:`AsyncTerminalClient` (async) is
bound to a single account's terminal endpoint (``Account.rest_url``). The two
are method-for-method mirrors; only the awaiting differs.

Platform awareness is enforced **client-side** before the request goes out:
MT5-only timeframes (``M2``/``M3``/``H2``/``H6``/``H8``/``H12``) don't exist on
MT4, so the SDK raises :class:`~fxsocket.UnsupportedOnPlatformError` rather than
letting the terminal answer 400. (Order *operations*, including stop-limit, are
accepted by both platforms' terminal APIs, so they are not gated.)

Order inputs are validated client-side to fail fast and, above all, safely:
the same constraints the terminal enforces (volume > 0, an entry price for
pending orders, a stop-limit price for ``*StopLimit``) plus a guard against the
silent ``order_modify`` footgun where a literal ``0.0`` stop-loss / take-profit
*removes* the protection — pass ``clear_stop_loss`` / ``clear_take_profit`` to
do that explicitly, ``None`` (the default) keeps the current value.

No request is auto-retried — in particular order send/modify/close must never
replay, to avoid duplicate fills.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx

from .._http import AsyncHTTP, SyncHTTP, auth_headers
from ..enums import (
    MT5_ONLY_TIMEFRAMES,
    PENDING_OPERATIONS,
    STOP_LIMIT_OPERATIONS,
    OrderOperation,
    Platform,
    Timeframe,
)
from ..errors import (
    UnsupportedOnPlatformError,
    ValidationError,
    error_from_response,
)
from ..models import (
    AccountInfo,
    AccountSummary,
    Candle,
    Health,
    HealthChecks,
    HistoryTrade,
    MarginCalc,
    OpenedOrder,
    OrderResult,
    PositionTrade,
    ProfitCalc,
    Quote,
    ServerTimezone,
    SymbolInfo,
)

# --------------------------------------------------------------------------- #
# Coercion / validation helpers (shared by sync + async)
# --------------------------------------------------------------------------- #

_OP_BY_NORM = {op.value.lower(): op for op in OrderOperation}

_TF_HUMAN = {
    "1min": Timeframe.M1,
    "2min": Timeframe.M2,
    "3min": Timeframe.M3,
    "5min": Timeframe.M5,
    "15min": Timeframe.M15,
    "30min": Timeframe.M30,
    "1h": Timeframe.H1,
    "2h": Timeframe.H2,
    "4h": Timeframe.H4,
    "6h": Timeframe.H6,
    "8h": Timeframe.H8,
    "12h": Timeframe.H12,
    "1d": Timeframe.D1,
    "1w": Timeframe.W1,
    "1month": Timeframe.MN1,
    "1mn": Timeframe.MN1,
}


def coerce_operation(value: OrderOperation | str) -> OrderOperation:
    """Normalize an order operation (case/separator-insensitive)."""
    if isinstance(value, OrderOperation):
        return value
    key = "".join(ch for ch in str(value).lower() if ch.isalnum())
    op = _OP_BY_NORM.get(key)
    if op is None:
        raise ValidationError(f"Unknown order operation: {value!r}")
    return op


def coerce_timeframe(value: Timeframe | str) -> Timeframe:
    """Normalize a timeframe label (``M5``, ``5min``, ``1h`` … )."""
    if isinstance(value, Timeframe):
        return value
    raw = str(value).strip()
    try:
        return Timeframe(raw.upper())
    except ValueError:
        pass
    tf = _TF_HUMAN.get(raw.lower())
    if tf is None:
        raise ValidationError(f"Unknown timeframe: {value!r}")
    return tf


def _require_positive_volume(volume: float) -> None:
    if volume <= 0:
        raise ValidationError(f"volume must be > 0, got {volume}")


def _validate_order_send(
    op: OrderOperation,
    *,
    volume: float,
    price: float | None,
    stop_limit_price: float | None,
    stop_loss: float | None,
    take_profit: float | None,
) -> None:
    """Mirror the terminal's own ``/OrderSend`` validation, fail-fast."""
    _require_positive_volume(volume)
    if op in PENDING_OPERATIONS and (price is None or price <= 0):
        raise ValidationError(f"price is required for pending orders ({op.value})")
    if op in STOP_LIMIT_OPERATIONS and (
        stop_limit_price is None or stop_limit_price <= 0
    ):
        raise ValidationError(f"stop_limit_price is required for {op.value} orders")
    # On send, 0.0 / absent legitimately means "no SL/TP"; only negatives are wrong.
    if stop_loss is not None and stop_loss < 0:
        raise ValidationError(f"stop_loss must be >= 0, got {stop_loss}")
    if take_profit is not None and take_profit < 0:
        raise ValidationError(f"take_profit must be >= 0, got {take_profit}")


def _resolve_modify_sl_tp(
    *,
    stop_loss: float | None,
    take_profit: float | None,
    clear_stop_loss: bool,
    clear_take_profit: bool,
    price: float | None,
    stop_limit_price: float | None,
) -> tuple[float | None, float | None]:
    """Validate an ``order_modify`` and resolve SL/TP to wire values.

    Returns ``(sl, tp)`` where ``None`` means "omit → keep current" and ``0.0``
    means "clear". A bare ``stop_loss=0.0`` is rejected because, sent verbatim,
    the terminal would silently *remove* the protection — removal must be
    explicit via ``clear_stop_loss``.
    """
    if clear_stop_loss and stop_loss is not None:
        raise ValidationError(
            "pass either stop_loss=<price> or clear_stop_loss=True, not both"
        )
    if clear_take_profit and take_profit is not None:
        raise ValidationError(
            "pass either take_profit=<price> or clear_take_profit=True, not both"
        )
    if stop_loss is not None and stop_loss <= 0:
        raise ValidationError(
            "stop_loss must be > 0 in order_modify; pass clear_stop_loss=True to "
            "remove it, or leave it None to keep the current value"
        )
    if take_profit is not None and take_profit <= 0:
        raise ValidationError(
            "take_profit must be > 0 in order_modify; pass clear_take_profit=True "
            "to remove it, or leave it None to keep the current value"
        )
    if price is not None and price <= 0:
        raise ValidationError(f"price must be > 0, got {price}")
    if stop_limit_price is not None and stop_limit_price <= 0:
        raise ValidationError(f"stop_limit_price must be > 0, got {stop_limit_price}")
    sl = 0.0 if clear_stop_loss else stop_loss
    tp = 0.0 if clear_take_profit else take_profit
    return sl, tp


def _check_timeframe(tf: Timeframe, platform: Platform) -> None:
    if platform is Platform.MT4 and tf in MT5_ONLY_TIMEFRAMES:
        raise UnsupportedOnPlatformError(
            f"{tf.value} is an MT5-only timeframe; not available on MT4."
        )


def _fmt_time(value: str | datetime | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _clean(params: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None (so omitted fields aren't sent)."""
    return {k: v for k, v in params.items() if v is not None}


def _order_send_body(
    *,
    symbol: str,
    operation: OrderOperation,
    volume: float,
    price: float | None,
    slippage: int | None,
    stop_loss: float | None,
    take_profit: float | None,
    stop_limit_price: float | None,
    expiration: str | datetime | date | None,
    comment: str | None,
    magic: int | None,
) -> dict[str, Any]:
    return _clean(
        {
            "symbol": symbol,
            "operation": operation.value,
            "volume": volume,
            "price": price,
            "slippage": slippage,
            "stopLoss": stop_loss,
            "takeProfit": take_profit,
            "stopLimitPrice": stop_limit_price,
            "expiration": _fmt_time(expiration),
            "comment": comment,
            "expertId": magic,
        }
    )


def _order_modify_body(
    *,
    ticket: int,
    stop_loss: float | None,
    take_profit: float | None,
    price: float | None,
    stop_limit_price: float | None,
    expiration: str | datetime | date | None,
) -> dict[str, Any]:
    return _clean(
        {
            "ticket": ticket,
            "stopLoss": stop_loss,
            "takeProfit": take_profit,
            "price": price,
            "stopLimitPrice": stop_limit_price,
            "expiration": _fmt_time(expiration),
        }
    )


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #


class TerminalClient:
    """Synchronous terminal client bound to one account's REST endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        platform: Platform | str,
        verify: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self.platform = Platform(platform)
        self.base_url = base_url
        self._client = httpx.Client(
            base_url=base_url,
            headers=auth_headers(api_key),
            timeout=timeout,
            verify=verify,
        )
        self._http = SyncHTTP(self._client)

    # -- account state ----------------------------------------------------- #

    def account_summary(self) -> AccountSummary:
        return AccountSummary.model_validate(
            self._http.request("GET", "/AccountSummary")
        )

    def account_info(self) -> AccountInfo:
        return AccountInfo.model_validate(self._http.request("GET", "/AccountInfo"))

    def opened_orders(self) -> list[OpenedOrder]:
        rows = self._http.request("GET", "/OpenedOrders")
        return [OpenedOrder.model_validate(r) for r in rows]

    def order_history(
        self,
        from_: str | datetime | date | None = None,
        to: str | datetime | date | None = None,
    ) -> list[HistoryTrade]:
        params = _clean({"from": _fmt_time(from_), "to": _fmt_time(to)})
        rows = self._http.request("GET", "/OrderHistory", params=params)
        return [HistoryTrade.model_validate(r) for r in rows]

    def position_history(
        self,
        from_: str | datetime | date | None = None,
        to: str | datetime | date | None = None,
    ) -> list[PositionTrade]:
        params = _clean({"from": _fmt_time(from_), "to": _fmt_time(to)})
        rows = self._http.request("GET", "/PositionHistory", params=params)
        return [PositionTrade.model_validate(r) for r in rows]

    def server_timezone(self) -> ServerTimezone:
        return ServerTimezone.model_validate(
            self._http.request("GET", "/ServerTimezone")
        )

    # -- market data ------------------------------------------------------- #

    def symbols(self) -> list[str]:
        return list(self._http.request("GET", "/symbols"))

    def quote(self, symbol: str) -> Quote:
        return Quote.model_validate(
            self._http.request("GET", "/getQuote", params={"symbol": symbol})
        )

    def symbol_info(self, symbol: str) -> SymbolInfo:
        return SymbolInfo.model_validate(
            self._http.request("GET", "/SymbolInfo", params={"symbol": symbol})
        )

    def price_history(
        self,
        symbol: str,
        timeframe: Timeframe | str,
        from_: str | datetime | date | None = None,
        to: str | datetime | date | None = None,
    ) -> list[Candle]:
        tf = coerce_timeframe(timeframe)
        _check_timeframe(tf, self.platform)
        params = _clean(
            {
                "symbol": symbol,
                "timeframe": tf.value,
                "from": _fmt_time(from_),
                "to": _fmt_time(to),
            }
        )
        rows = self._http.request("GET", "/PriceHistory", params=params)
        return [Candle.model_validate(r) for r in rows]

    # -- calculators ------------------------------------------------------- #

    def calc_margin(
        self, symbol: str, operation: OrderOperation | str, volume: float, price: float
    ) -> MarginCalc:
        op = coerce_operation(operation)
        _require_positive_volume(volume)
        params = {
            "symbol": symbol,
            "operation": op.value,
            "volume": volume,
            "price": price,
        }
        return MarginCalc.model_validate(
            self._http.request("GET", "/OrderCalcMargin", params=params)
        )

    def calc_profit(
        self,
        symbol: str,
        operation: OrderOperation | str,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> ProfitCalc:
        op = coerce_operation(operation)
        _require_positive_volume(volume)
        params = {
            "symbol": symbol,
            "operation": op.value,
            "volume": volume,
            "priceOpen": price_open,
            "priceClose": price_close,
        }
        return ProfitCalc.model_validate(
            self._http.request("GET", "/OrderCalcProfit", params=params)
        )

    # -- trading (never retried) ------------------------------------------- #

    def order_send(
        self,
        *,
        symbol: str,
        operation: OrderOperation | str,
        volume: float,
        price: float | None = None,
        slippage: int | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        stop_limit_price: float | None = None,
        expiration: str | datetime | date | None = None,
        comment: str | None = None,
        magic: int | None = None,
    ) -> OrderResult:
        op = coerce_operation(operation)
        _validate_order_send(
            op,
            volume=volume,
            price=price,
            stop_limit_price=stop_limit_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        body = _order_send_body(
            symbol=symbol,
            operation=op,
            volume=volume,
            price=price,
            slippage=slippage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            stop_limit_price=stop_limit_price,
            expiration=expiration,
            comment=comment,
            magic=magic,
        )
        return OrderResult.model_validate(
            self._http.request("POST", "/OrderSend", json=body)
        )

    def order_modify(
        self,
        ticket: int,
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        price: float | None = None,
        stop_limit_price: float | None = None,
        expiration: str | datetime | date | None = None,
        clear_stop_loss: bool = False,
        clear_take_profit: bool = False,
    ) -> OrderResult:
        sl, tp = _resolve_modify_sl_tp(
            stop_loss=stop_loss,
            take_profit=take_profit,
            clear_stop_loss=clear_stop_loss,
            clear_take_profit=clear_take_profit,
            price=price,
            stop_limit_price=stop_limit_price,
        )
        body = _order_modify_body(
            ticket=ticket,
            stop_loss=sl,
            take_profit=tp,
            price=price,
            stop_limit_price=stop_limit_price,
            expiration=expiration,
        )
        return OrderResult.model_validate(
            self._http.request("POST", "/OrderModify", json=body)
        )

    def order_close(
        self,
        ticket: int,
        *,
        volume: float | None = None,
        slippage: int | None = None,
    ) -> OrderResult:
        if volume is not None and volume < 0:
            raise ValidationError(f"volume must be >= 0, got {volume}")
        body = _clean({"ticket": ticket, "volume": volume, "slippage": slippage})
        return OrderResult.model_validate(
            self._http.request("POST", "/OrderClose", json=body)
        )

    # -- health ------------------------------------------------------------ #

    def status(self) -> Health:
        return Health.model_validate(self._http.request("GET", "/status"))

    def healthz(self) -> HealthChecks:
        return self._probe("/healthz")

    def livez(self) -> HealthChecks:
        return self._probe("/livez")

    def _probe(self, path: str) -> HealthChecks:
        # /healthz and /livez answer 503 when not-ready, with a useful body —
        # parse it instead of raising; only other codes are real errors.
        resp = self._client.get(path)
        if resp.status_code not in (200, 503):
            raise error_from_response(resp)
        return HealthChecks.model_validate(resp.json())

    # -- lifecycle --------------------------------------------------------- #

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TerminalClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# --------------------------------------------------------------------------- #
# Async
# --------------------------------------------------------------------------- #


class AsyncTerminalClient:
    """Asynchronous mirror of :class:`TerminalClient`."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        platform: Platform | str,
        verify: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self.platform = Platform(platform)
        self.base_url = base_url
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=auth_headers(api_key),
            timeout=timeout,
            verify=verify,
        )
        self._http = AsyncHTTP(self._client)

    async def account_summary(self) -> AccountSummary:
        return AccountSummary.model_validate(
            await self._http.request("GET", "/AccountSummary")
        )

    async def account_info(self) -> AccountInfo:
        return AccountInfo.model_validate(
            await self._http.request("GET", "/AccountInfo")
        )

    async def opened_orders(self) -> list[OpenedOrder]:
        rows = await self._http.request("GET", "/OpenedOrders")
        return [OpenedOrder.model_validate(r) for r in rows]

    async def order_history(
        self,
        from_: str | datetime | date | None = None,
        to: str | datetime | date | None = None,
    ) -> list[HistoryTrade]:
        params = _clean({"from": _fmt_time(from_), "to": _fmt_time(to)})
        rows = await self._http.request("GET", "/OrderHistory", params=params)
        return [HistoryTrade.model_validate(r) for r in rows]

    async def position_history(
        self,
        from_: str | datetime | date | None = None,
        to: str | datetime | date | None = None,
    ) -> list[PositionTrade]:
        params = _clean({"from": _fmt_time(from_), "to": _fmt_time(to)})
        rows = await self._http.request("GET", "/PositionHistory", params=params)
        return [PositionTrade.model_validate(r) for r in rows]

    async def server_timezone(self) -> ServerTimezone:
        return ServerTimezone.model_validate(
            await self._http.request("GET", "/ServerTimezone")
        )

    async def symbols(self) -> list[str]:
        return list(await self._http.request("GET", "/symbols"))

    async def quote(self, symbol: str) -> Quote:
        return Quote.model_validate(
            await self._http.request("GET", "/getQuote", params={"symbol": symbol})
        )

    async def symbol_info(self, symbol: str) -> SymbolInfo:
        return SymbolInfo.model_validate(
            await self._http.request("GET", "/SymbolInfo", params={"symbol": symbol})
        )

    async def price_history(
        self,
        symbol: str,
        timeframe: Timeframe | str,
        from_: str | datetime | date | None = None,
        to: str | datetime | date | None = None,
    ) -> list[Candle]:
        tf = coerce_timeframe(timeframe)
        _check_timeframe(tf, self.platform)
        params = _clean(
            {
                "symbol": symbol,
                "timeframe": tf.value,
                "from": _fmt_time(from_),
                "to": _fmt_time(to),
            }
        )
        rows = await self._http.request("GET", "/PriceHistory", params=params)
        return [Candle.model_validate(r) for r in rows]

    async def calc_margin(
        self, symbol: str, operation: OrderOperation | str, volume: float, price: float
    ) -> MarginCalc:
        op = coerce_operation(operation)
        _require_positive_volume(volume)
        params = {
            "symbol": symbol,
            "operation": op.value,
            "volume": volume,
            "price": price,
        }
        return MarginCalc.model_validate(
            await self._http.request("GET", "/OrderCalcMargin", params=params)
        )

    async def calc_profit(
        self,
        symbol: str,
        operation: OrderOperation | str,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> ProfitCalc:
        op = coerce_operation(operation)
        _require_positive_volume(volume)
        params = {
            "symbol": symbol,
            "operation": op.value,
            "volume": volume,
            "priceOpen": price_open,
            "priceClose": price_close,
        }
        return ProfitCalc.model_validate(
            await self._http.request("GET", "/OrderCalcProfit", params=params)
        )

    async def order_send(
        self,
        *,
        symbol: str,
        operation: OrderOperation | str,
        volume: float,
        price: float | None = None,
        slippage: int | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        stop_limit_price: float | None = None,
        expiration: str | datetime | date | None = None,
        comment: str | None = None,
        magic: int | None = None,
    ) -> OrderResult:
        op = coerce_operation(operation)
        _validate_order_send(
            op,
            volume=volume,
            price=price,
            stop_limit_price=stop_limit_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        body = _order_send_body(
            symbol=symbol,
            operation=op,
            volume=volume,
            price=price,
            slippage=slippage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            stop_limit_price=stop_limit_price,
            expiration=expiration,
            comment=comment,
            magic=magic,
        )
        return OrderResult.model_validate(
            await self._http.request("POST", "/OrderSend", json=body)
        )

    async def order_modify(
        self,
        ticket: int,
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        price: float | None = None,
        stop_limit_price: float | None = None,
        expiration: str | datetime | date | None = None,
        clear_stop_loss: bool = False,
        clear_take_profit: bool = False,
    ) -> OrderResult:
        sl, tp = _resolve_modify_sl_tp(
            stop_loss=stop_loss,
            take_profit=take_profit,
            clear_stop_loss=clear_stop_loss,
            clear_take_profit=clear_take_profit,
            price=price,
            stop_limit_price=stop_limit_price,
        )
        body = _order_modify_body(
            ticket=ticket,
            stop_loss=sl,
            take_profit=tp,
            price=price,
            stop_limit_price=stop_limit_price,
            expiration=expiration,
        )
        return OrderResult.model_validate(
            await self._http.request("POST", "/OrderModify", json=body)
        )

    async def order_close(
        self,
        ticket: int,
        *,
        volume: float | None = None,
        slippage: int | None = None,
    ) -> OrderResult:
        if volume is not None and volume < 0:
            raise ValidationError(f"volume must be >= 0, got {volume}")
        body = _clean({"ticket": ticket, "volume": volume, "slippage": slippage})
        return OrderResult.model_validate(
            await self._http.request("POST", "/OrderClose", json=body)
        )

    async def status(self) -> Health:
        return Health.model_validate(await self._http.request("GET", "/status"))

    async def healthz(self) -> HealthChecks:
        return await self._probe("/healthz")

    async def livez(self) -> HealthChecks:
        return await self._probe("/livez")

    async def _probe(self, path: str) -> HealthChecks:
        resp = await self._client.get(path)
        if resp.status_code not in (200, 503):
            raise error_from_response(resp)
        return HealthChecks.model_validate(resp.json())

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncTerminalClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
