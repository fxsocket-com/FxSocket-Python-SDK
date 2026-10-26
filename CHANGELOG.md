# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Releases
up to 0.7.0 are described in their tagged commits.

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

[0.8.0]: https://github.com/fxsocket-com/FxSocket-Python-SDK/releases/tag/v0.8.0
