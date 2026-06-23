# FxSocket Python SDK

Python client for the [FxSocket](https://fxsocket.com) API — manage MT4/MT5
trading accounts, and (coming next) trade and stream market data through each
account's terminal over REST and WebSocket.

> **Status:** early. Account management (the v1 API) and the per-account
> terminal REST client (trading, market data, account — MT4 + MT5) are
> implemented. WebSocket streaming is next — see the [roadmap](#roadmap).

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

## Private hosting

Privately-hosted accounts (dedicated droplet) are listed, traded, and streamed
just like shared-cluster accounts — their `rest_url` / `ws_url` simply point at
the droplet. Their droplet uses a self-signed certificate, so reaching the
terminal API will require `verify_terminal_tls=False` (or a pinned CA).
*Creating* a private-hosted account stays in the dashboard for now.

## Roadmap

- [x] **M1** — Management client (v1 accounts CRUD), sync + async, typed models.
- [x] **M2** — Terminal REST client (account, market data, trading) for MT4 + MT5.
- [ ] **M3** — WebSocket streaming (ticks, bars, account, positions, trades).
- [ ] **M4** — Private-hosting polish (self-signed TLS), examples.
- [ ] **M5** — Publish to PyPI.

## Development

```bash
pip install -e ".[dev]"
ruff check . && mypy && pytest
```

## License

MIT
