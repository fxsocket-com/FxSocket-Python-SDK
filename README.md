# FxSocket Python SDK

[![PyPI](https://img.shields.io/pypi/v/fxsocket.svg)](https://pypi.org/project/fxsocket/)
[![Python](https://img.shields.io/pypi/pyversions/fxsocket.svg)](https://pypi.org/project/fxsocket/)
[![CI](https://github.com/fxsocket-com/FxSocket-Python-SDK/actions/workflows/ci.yml/badge.svg)](https://github.com/fxsocket-com/FxSocket-Python-SDK/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/fxsocket-com/FxSocket-Python-SDK/blob/main/LICENSE)

Typed Python client for the [FxSocket](https://fxsocket.com) API. Connect your
MetaTrader 4 / 5 accounts, then place trades, read market data, and stream live
updates over REST and WebSocket — with mirrored **synchronous** and **async**
interfaces.

## Features

- **Account management** — link, list, fetch, and disconnect MT4/MT5 accounts.
- **Trading** — market & pending orders, modify, close, plus margin/profit calculators.
- **Market data** — quotes, symbol specifications, OHLC history, account state & info.
- **Live streaming** — ticks, bars, account, positions, trades, and terminal status
  over WebSocket, with automatic reconnect + subscription replay.
- **Sync *and* async** — `Client` / `AsyncClient`, method-for-method mirrors.
- **Typed** — Pydantic v2 models throughout; ships `py.typed`.
- **One interface for MT4 and MT5** — platform differences handled for you.

## Install

```bash
pip install fxsocket
```

Requires Python 3.10+.

## Quickstart

```python
from fxsocket import Client

with Client(api_key="fxs_live_…") as fx:        # or set FXSOCKET_API_KEY
    account = fx.accounts.list()[0]
    term = fx.terminal(account)

    print("equity:", term.account_summary().equity)
    print("EURUSD:", term.quote("EURUSD").ask)
```

## Authentication

Every call uses your FxSocket API key (`fxs_live_…`), from the dashboard.
Pass it explicitly, or set the `FXSOCKET_API_KEY` environment variable and call
`Client()` with no arguments.

```python
from fxsocket import Client

with Client(api_key="fxs_live_…") as fx:
    for account in fx.accounts.list():
        print(account.platform, account.nickname, account.status)
```

## Managing accounts

```python
from fxsocket import Client

with Client(api_key="fxs_live_…") as fx:
    # Link a new account (platform defaults to MT5).
    account = fx.accounts.create(
        platform="mt5", server="ICMarkets-Demo", login=1150125, password="…",
    )

    # Poll until it's connected.
    account = fx.accounts.get(account.id)
    print(account.status)                  # connecting → connected

    # Where this account's terminal API lives (empty until provisioned).
    print(account.rest_url, account.ws_url)

    fx.accounts.delete(account.id)         # unlink
```

Everything is also available on `AsyncClient`:

```python
from fxsocket import AsyncClient

async with AsyncClient(api_key="fxs_live_…") as fx:
    accounts = await fx.accounts.list()
```

## Trading & market data

`fx.terminal(account)` returns a REST client bound to that account's terminal
(resolved from `account.rest_url`, whether it's a shared pod or a private droplet):

```python
from fxsocket import Client

with Client(api_key="fxs_live_…") as fx:
    account = fx.accounts.get("…")         # must be connected (has a terminal)
    term = fx.terminal(account)

    summary = term.account_summary()       # balance, equity, margin, …
    quote = term.quote("EURUSD")           # latest tick
    bars = term.price_history("EURUSD", "M5")   # recent OHLC bars

    result = term.order_send(              # market buy
        symbol="EURUSD", operation="Buy", volume=0.10,
        stop_loss=1.07, take_profit=1.10,
    )
    if result.success:
        term.order_modify(result.order, take_profit=1.12)   # None keeps the SL
        term.order_close(result.order)
```

Inputs are validated client-side before they're sent. One guard worth knowing:
in `order_modify`, a literal `stop_loss=0.0` would *remove* your stop-loss, so
it's rejected — pass `clear_stop_loss=True` to remove one deliberately, while
`None` (the default) keeps the current value.

MT4 and MT5 share one interface. MT5-only timeframes (`M2`, `M3`, `H2`, `H6`,
`H8`, `H12`) raise `UnsupportedOnPlatformError` on MT4 before any request.

> **MT4 history note:** on MT4, `price_history` with `from_`/`to` bounds (or the
> `D1` timeframe) can fail server-side with `CopyRates failed` when the terminal
> hasn't loaded that history. Calling `price_history(symbol, timeframe)` without
> date bounds returns the most recent bars reliably.

## Streaming (WebSocket)

Subscribe to live ticks, bars, account, positions, trades, and terminal status.
Streaming is async-first; a synchronous wrapper is provided too. A dropped
connection auto-reconnects and replays active subscriptions
(`auto_reconnect=True` by default).

```python
import asyncio
from fxsocket import AsyncClient, Tick, Bar, AccountUpdate

async def main():
    async with AsyncClient(api_key="fxs_live_…") as fx:
        account = await fx.accounts.get("…")
        async with fx.stream(account) as s:
            await s.subscribe_prices("EURUSD")
            await s.subscribe_bars("EURUSD", "M5")
            await s.subscribe_account()
            async for event in s:
                match event:
                    case Tick():
                        print(event.symbol, event.data.bid, event.data.ask)
                    case Bar():
                        print(event.symbol, event.timeframe, event.data.close)
                    case AccountUpdate():
                        print("equity", event.data.equity)

asyncio.run(main())
```

Synchronous equivalent:

```python
from fxsocket import Client, Tick

with Client(api_key="fxs_live_…") as fx:
    with fx.stream(fx.accounts.get("…")) as s:
        s.subscribe_prices("EURUSD")
        for event in s:
            if isinstance(event, Tick):
                print(event.data.bid, event.data.ask)
```

## Errors

Every failure raises a subclass of `fxsocket.FxSocketError`:

| Exception | When |
|---|---|
| `AuthError` | missing/invalid API key |
| `RateLimitError` | rate limited (`.retry_after`) |
| `ValidationError` | malformed request |
| `NotFoundError` | account/resource not found |
| `AccountCapError` | plan account limit reached (`.cap`, `.current`) |
| `DuplicateAccountError` | account already linked |
| `ConnectFailedError` | broker rejected the login |
| `TerminalNotReadyError` | terminal not provisioned / not ready |
| `UnsupportedOnPlatformError` | feature not available on this platform |

```python
from fxsocket import Client, AccountCapError

try:
    fx.accounts.create(server="Demo", login=1, password="…")
except AccountCapError as e:
    print(f"Plan limit reached: {e.current}/{e.cap}")
```

## Private hosting

Privately-hosted accounts (a dedicated droplet) are listed, traded, and streamed
exactly like shared-cluster accounts — their `rest_url` / `ws_url` simply point
at the droplet. The droplet serves a self-signed certificate, so reach it with
`Client(..., verify_terminal_tls=False)` (or supply a pinned CA). *Creating* a
private-hosted account is done in the dashboard.

## Timestamps

Terminal timestamps (`quote.time`, candle `time`, order times) are returned as
**strings in broker server time** — not Python `datetime`. The trailing `Z` is
stylistic and does **not** mean UTC. Use `terminal.server_timezone()` to get the
broker's UTC offset if you need to convert.

## Requirements

- Python 3.10+
- [`httpx`](https://www.python-httpx.org/), [`pydantic`](https://docs.pydantic.dev/) ≥ 2, [`websockets`](https://websockets.readthedocs.io/) ≥ 13

## Links

- API reference: <https://api.fxsocket.com/v1/docs>
- Examples: [`examples/`](https://github.com/fxsocket-com/FxSocket-Python-SDK/tree/main/examples)

## Development

```bash
pip install -e ".[dev]"
ruff check . && mypy && pytest
```

## License

MIT — see [LICENSE](https://github.com/fxsocket-com/FxSocket-Python-SDK/blob/main/LICENSE).
