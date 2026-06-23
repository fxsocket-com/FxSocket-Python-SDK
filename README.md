# FxSocket Python SDK

Python client for the [FxSocket](https://fxsocket.com) API — manage MT4/MT5
trading accounts, and (coming next) trade and stream market data through each
account's terminal over REST and WebSocket.

> **Status:** account management (the v1 API), the per-account terminal REST
> client (trading, market data, account — MT4 + MT5), and WebSocket streaming
> are all implemented and exercised against live accounts. Not yet published to
> PyPI — see the [roadmap](#roadmap).

## Install

```bash
pip install fxsocket   # not yet published — for now: pip install -e .
```

Requires Python 3.10+.

## Authentication

Every call uses your FxSocket API key (`fxs_live_…`), from the dashboard.
Pass it explicitly or set `FXSOCKET_API_KEY`.

```python
from fxsocket import Client

with Client(api_key="fxs_live_…") as fx:      # or Client() to read the env var
    for account in fx.accounts.list():
        print(account.platform, account.nickname, account.status)
```

## Managing accounts

```python
from fxsocket import Client

with Client(api_key="fxs_live_…") as fx:
    # Link a new account (defaults to MT5).
    account = fx.accounts.create(
        server="ICMarkets-Demo", login=1150125, password="…", platform="mt5",
    )

    # Poll until it's connected.
    account = fx.accounts.get(account.id)
    print(account.status)           # connecting → connected

    # Where this account's terminal API lives (empty until provisioned).
    print(account.rest_url, account.ws_url)

    fx.accounts.delete(account.id)  # unlink
```

### Async

```python
from fxsocket import AsyncClient

async with AsyncClient(api_key="fxs_live_…") as fx:
    accounts = await fx.accounts.list()
```

## Errors

All failures raise a subclass of `fxsocket.FxSocketError`, e.g.
`AuthError`, `RateLimitError` (with `.retry_after`), `AccountCapError`
(with `.cap` / `.current`), `DuplicateAccountError`, `ConnectFailedError`,
`NotFoundError`, `ValidationError`.

```python
from fxsocket import Client, AccountCapError

try:
    fx.accounts.create(server="Demo", login=1, password="…")
except AccountCapError as e:
    print(f"Plan limit reached: {e.current}/{e.cap}")
```

## Trading & market data

Once an account is connected, `client.terminal(account)` returns a REST client
bound to that account's terminal (resolved from `account.rest_url`):

```python
from fxsocket import Client

with Client(api_key="fxs_live_…") as fx:
    account = fx.accounts.get("…")        # must have a terminal (status connected)
    term = fx.terminal(account)

    summary = term.account_summary()       # balance, equity, margin, …
    quote = term.quote("EURUSD")           # latest tick
    bars = term.price_history("EURUSD", "M5", from_="2026-06-01")

    result = term.order_send(              # market buy
        symbol="EURUSD", operation="Buy", volume=0.10,
        stop_loss=1.07, take_profit=1.10,
    )
    if result.success:
        term.order_modify(result.order, take_profit=1.12)   # None keeps SL
        term.order_close(result.order)
```

The client validates inputs before sending — and protects against a real
footgun: in `order_modify`, a bare `stop_loss=0.0` would *remove* your
stop-loss, so it's rejected. Pass `clear_stop_loss=True` to remove one on
purpose; `None` (the default) keeps the current value.

MT4 and MT5 share one interface. MT5-only timeframes (`M2`, `M3`, `H2`, `H6`,
`H8`, `H12`) raise `UnsupportedOnPlatformError` on MT4 before any request.

> **MT4 history note:** on MT4, `price_history` with `from_`/`to` bounds (or the
> `D1` timeframe) can fail server-side with `CopyRates failed` when the
> terminal hasn't loaded that history. Calling `price_history(symbol, tf)`
> without date bounds returns the most recent bars reliably.

## Streaming (WebSocket)

Subscribe to live ticks, bars, account, positions, trades, and terminal status.
Streaming is async-first; a synchronous wrapper is also provided.

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

A dropped connection auto-reconnects and replays active subscriptions
(`auto_reconnect=True` by default).

## Private hosting

Privately-hosted accounts (dedicated droplet) are listed, traded, and streamed
just like shared-cluster accounts — their `rest_url` / `ws_url` simply point at
the droplet. Their droplet uses a self-signed certificate, so reaching the
terminal API will require `verify_terminal_tls=False` (or a pinned CA).
*Creating* a private-hosted account stays in the dashboard for now.

## Roadmap

- [x] **M1** — Management client (v1 accounts CRUD), sync + async, typed models.
- [x] **M2** — Terminal REST client (account, market data, trading) for MT4 + MT5.
- [x] **M3** — WebSocket streaming (ticks, bars, account, positions, trades).
- [x] **M4** — Private-hosting (self-signed TLS via `verify_terminal_tls=False`), examples.
- [ ] **M5** — Publish to PyPI.

## Development

```bash
pip install -e ".[dev]"
ruff check . && mypy && pytest
```

## License

MIT
