# FxSocket Python SDK

Python client for the [FxSocket](https://fxsocket.com) API — manage MT4/MT5
trading accounts, and (coming next) trade and stream market data through each
account's terminal over REST and WebSocket.

> **Status:** early. Account management (the v1 API) is implemented. The
> per-account terminal REST client and WebSocket streaming are in progress —
> see the [roadmap](#roadmap).

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

## Private hosting

Privately-hosted accounts (dedicated droplet) are listed, traded, and streamed
just like shared-cluster accounts — their `rest_url` / `ws_url` simply point at
the droplet. Their droplet uses a self-signed certificate, so reaching the
terminal API will require `verify_terminal_tls=False` (or a pinned CA).
*Creating* a private-hosted account stays in the dashboard for now.

## Roadmap

- [x] **M1** — Management client (v1 accounts CRUD), sync + async, typed models.
- [ ] **M2** — Terminal REST client (account, market data, trading) for MT4 + MT5.
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
