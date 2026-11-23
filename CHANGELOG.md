# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Releases
up to 0.7.0 are described in their tagged commits.

## [Unreleased]

## [0.12.0] - 2026-11-23

cTrader sign-in through the API, and the trading extras of cTrader bridge
0.1.6. Everything new is additive: MT4 and MT5 rows keep the defaults.

### Added

- `client.ctrader_sign_ins` links cTrader accounts without the dashboard:
  `create(return_url=...)` (`CtraderSignInStarted` with `authorize_url`),
  `get()` (`CtraderSignIn` with the granted `CtraderSignInAccount` rows and
  `linkable_accounts`), `wait()` (polls until the sign-in is no longer
  pending, with `timeout` and `interval`) and `link_accounts()` (up to 50,
  one `CtraderLinkResult` per account). `CtraderSignInStatus` lists the
  statuses.
- Errors for the sign-in routes: `SignInLimitError` (429, a `RateLimitError`
  with `retry_after` and `limit`), `SignInNotCompletedError` (409, with
  `sign_in_status`), `SignInExpiredError` (410),
  `CtraderReauthRequiredError` (409) and `CtraderAccountNotInSignInError`
  (400, a `ValidationError` with `ctrader_account_ids`).
- `Account.reauth_required` (cTrader: the sign-in was revoked) and
  `Account.suspended` (billing suspended the account).
- `order_send(trailing_stop=..., guaranteed_stop_loss=...)` and the same on
  `order_modify` (`None` keeps them), cTrader only. Both need a
  `stop_loss`; the SDK raises `ValidationError` before sending otherwise.
- `order_modify(client_order_id=...)` and `order_close(client_order_id=...)`
  on cTrader: one set of ids with `order_send`, so a modify or partial close
  that timed out can be sent again safely.
- `journal(limit=, since=, kind=)` on the terminal client, cTrader only, with
  the new `JournalEntry` model and `JournalKind` enum.
- `OpenedOrder.trailing_stop` / `.guaranteed_stop_loss` and the same on
  `PendingOrder` (so on the `positions` and `orders` stream frames too),
  `AccountInfo.limited_risk`, `SymbolInfo.guaranteed_stop_loss` and
  `.gsl_distance`. `False` / `0` on MT4 and MT5.
- `OrderLeg` / `OrderDefaults` take `trailing_stop` and
  `guaranteed_stop_loss` for `client.orders.send()`, checked for a
  `stop_loss` before sending. An MT4 or MT5 leg with either set to `True`
  fails the batch at the API (`invalid_batch`).

### Changed

- On MT4 and MT5, `journal()`, `trailing_stop=True`,
  `guaranteed_stop_loss=True` and `client_order_id` on `order_modify` /
  `order_close` raise `UnsupportedOnPlatformError` before sending, since the
  MT terminals would ignore them. `False` for a stop loss kind is simply not
  sent there.
- `accounts.create()` and `private_servers.add_account()` with
  `platform="ctrader"` now point to `client.ctrader_sign_ins` instead of the
  dashboard.

## [0.11.0] - 2026-11-20

Support for terminal bridges MT5 0.18.3 / MT4 0.16.2. Everything new is
additive: on older bridges the new fields keep their defaults.

### Added

- `HistoryTrade.sl` / `.tp` (`order_history()`) and `PositionTrade.sl` /
  `.tp` (`position_history()`): the stop-loss and take-profit of closed
  trades, `0` when not set. On MT5 an `In` deal carries the opening
  order's levels and an `Out` deal the position's levels at close; a
  position row has the levels it closed with. Needs MT5 0.18.3 / MT4
  0.16.2; `0` on older bridges and on cTrader.
- `AccountHealth.auth_error` (`status().account`): the broker's reason for
  the last refused login, e.g. `"Invalid account"` for a wrong password.
  Needs MT5 0.18.2 / MT4 0.16.1.
- `order_close(ticket, pending_only=True)` only deletes a pending order; a
  ticket that has filled into a position by now raises the new
  `NotPendingError` (409 `MRPC_NOT_PENDING`) and the position stays open.
  Needs MT5 0.18.0 / MT4 0.16.0: older bridges ignore the flag and close
  the position.
- `order_send(..., execution="Manual")` with the new `Execution` enum:
  places a market order through the account's own order dialog, for
  brokers that refuse expert trading (retcode 10026). `Buy` / `Sell` only,
  magic 0; the SDK raises `ValidationError` for anything else before
  sending, and `UnsupportedOnPlatformError` on cTrader. The new
  `ManualNotSentError` (500 `MRPC_MANUAL_NOT_SENT`) means nothing was
  sent. Needs MT5 0.18.0 / MT4 0.16.0.
- `TerminalErrorCode.MRPC_NOT_PENDING` and `MRPC_MANUAL_NOT_SENT`.

### Fixed

- A 409 `MRPC_NOT_PENDING` was raised as `DuplicateAccountError`.

## [0.10.0] - 2026-11-10

cTrader support. cTrader accounts speak the same terminal API as MT5, so
everything else works on them unchanged.

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

[0.12.0]: https://github.com/fxsocket-com/FxSocket-Python-SDK/releases/tag/v0.12.0
[0.11.0]: https://github.com/fxsocket-com/FxSocket-Python-SDK/releases/tag/v0.11.0
[0.10.0]: https://github.com/fxsocket-com/FxSocket-Python-SDK/releases/tag/v0.10.0
[0.9.0]: https://github.com/fxsocket-com/FxSocket-Python-SDK/releases/tag/v0.9.0
[0.8.0]: https://github.com/fxsocket-com/FxSocket-Python-SDK/releases/tag/v0.8.0
