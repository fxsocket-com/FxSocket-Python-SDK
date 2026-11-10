# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Releases
up to 0.7.0 are described in their tagged commits.

## [Unreleased]

### Added

- `Platform.CTRADER`: cTrader accounts (linked from the dashboard) are
  listed, fetched, traded and streamed like MT4 and MT5 accounts. Before
  this, `accounts.list()` failed to parse an account with
  `platform: "ctrader"`.

### Changed

- `BuyStopLimit` / `SellStopLimit` and `stop_limit_price` raise
  `UnsupportedOnPlatformError` on cTrader too, before sending.
- `accounts.create()` and `private_servers.add_account()` raise
  `ValidationError` for `platform="ctrader"`: cTrader accounts are linked
  from the dashboard.

## [0.9.0] - 2026-11-01

Support for terminal bridges MT5 0.17.0 / MT4 0.15.0. Everything new is
additive: on older bridges the new fields keep their defaults, the new
endpoints are not served and `since` is ignored.

### Added

- `pending_orders()` with the new `PendingOrder` model (initial volume,
  stop-limit price, `expiration_mode`, `expiration`, `expiration_utc`).
- `symbol_select(symbols)` puts up to 100 symbols into Market Watch in one
  pass (`SymbolSelectResult`); `symbol_infos(symbols)` fetches up to 100
  specs at once (`SymbolInfoBatch` with per-symbol `errors`).
- `SymbolInfo.order_modes`, `expiration_modes` and `filling_modes`.
- `order_send(client_order_id=...)`: the terminal trades an id once and
  answers repeats with the first outcome, so a retry is safe.
  `OrderResult.idempotent_replayed` reports a replayed answer.
- `order_send(expiration_utc=...)` and `order_modify(expiration_utc=...)`
  set a pending order's expiry in UTC instead of broker server time.
- `OrderResult.position`, `time`, `time_utc`, `remainder_ticket`,
  `client_order_id` and `server_timing` (`ServerTiming`, from the
  `Server-Timing` header).
- Errors: `TradeOutcomeUnknownError` and `TradeAbandonedError` (both
  subclasses of `TerminalTimeoutError`) and `ClientOrderIdReusedError`.
  `TerminalErrorCode` lists the terminal's fixed error codes.
- Streaming: the `orders` topic (`subscribe_orders()`, `OrdersUpdate`
  snapshot, `OrderUpdate` per step with `OrderEventKind`), `modify` frames on
  `trades` (`ModifyUpdate`), `subscribe_trades(since=...)` with the
  `Replayed` event, and the frame envelope (`seq`, `ts`, `snapshot`, plus
  `replay` on `TradeUpdate`) on every data event.
- `TradeEventData.sl`, `tp`, `position_volume`, `time_utc` and, for MT4
  partial closes, `remainder_ticket` / `remainder_of`.
- `StreamWarning.topic`, `symbol` and `timeframe` name the stream that lost
  frames.
- `BridgeHealth` trade-pipeline fields from `/status` (`async_trades`,
  `trade_in_flight`, `oldest_trade_in_flight_age_ms`, `dealer_rtt_ms_ewma`,
  `trade_requests_sent`, `late_trade_verdicts`, `trade_queue_depth`,
  `non_trade_queue_depth`, `trade_deferred`).
- `ExpirationMode`, `OrderEventKind` and `DealEntry.OUT_BY`.

### Changed

- Trades resume after a reconnect. With `resume_trades=True` (the new
  default on `fx.stream(...)` and `AsyncStream`) the stream resubscribes
  `trades` from the newest deal `time` it delivered, so deals executed while the
  connection was down are replayed, and deals seen twice are dropped. Pass
  `resume_trades=False` for the previous behavior.
- A 504 is now split by code: `TradeOutcomeUnknownError` (the trade may have
  executed), `TradeAbandonedError` (it did not) or a plain
  `TerminalTimeoutError` (a read timed out). Existing
  `except TerminalTimeoutError` handlers still catch all three.
- `MRPC_TRADE_EA_STOPPED` raises `TerminalNotReadyError` (nothing executed).
- A 409 `MRPC_CLIENT_ORDER_ID_REUSED` raises `ClientOrderIdReusedError`
  instead of falling through to `DuplicateAccountError`.
- On these bridges an unknown symbol is HTTP 400 `MRPC_UNKNOWN_SYMBOL` and so
  raises `ValidationError` (it used to be a 500 and a plain `FxSocketError`).

### Fixed

- Stop-limit orders on MT4 now raise `UnsupportedOnPlatformError` before
  anything is sent. The SDK used to claim MT4 accepts them, but the MT4
  terminal API rejects `BuyStopLimit` / `SellStopLimit` with HTTP 400 and
  silently ignores `stop_limit_price` on `order_modify`.

## [0.8.0] - 2026-10-26

### Removed

- `Timeframe.M2`, `M3`, `H2`, `H6`, `H8` and `H12`, and the `2min`, `3min`,
  `2h`, `6h`, `8h` and `12h` aliases. The SDK treated them as MT5-only, but
  neither terminal API serves them: MT4 and MT5 both accept exactly `M1`, `M5`,
  `M15`, `M30`, `H1`, `H4`, `D1`, `W1` and `MN1`, in REST `price_history` and on
  the `bars` stream. On an MT5 account they passed the SDK and the terminal
  refused them (HTTP 400 from `price_history`, an `error` frame and no bars
  from the stream).

  They are removed rather than kept and rejected on both platforms, so that
  `Timeframe` lists exactly what the server accepts: iterating it gives the
  usable set, and type checkers flag the removed names. The only code this
  breaks is code that could never succeed.

### Changed

- A timeframe outside those nine now raises `ValidationError` on both
  platforms before anything is sent, and the message lists the accepted
  labels. On MT4 the six removed ones used to raise
  `UnsupportedOnPlatformError`, so code that caught that for timeframes should
  catch `ValidationError` instead. Nothing raises `UnsupportedOnPlatformError`
  now; it stays exported.

### Deprecated

- `fxsocket.enums.MT5_ONLY_TIMEFRAMES` is always empty and will be removed in
  1.0.

[0.9.0]: https://github.com/fxsocket-com/FxSocket-Python-SDK/releases/tag/v0.9.0
[0.8.0]: https://github.com/fxsocket-com/FxSocket-Python-SDK/releases/tag/v0.8.0
