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
- **Private servers** — list your dedicated hosting servers and manage the
  accounts on them.
- **Read-only keys** — mint, scope, rotate and revoke `fxs_ro_…` keys for
  dashboards and monitors.
- **Trading** — market & pending orders, modify, close, close-all, plus margin/profit calculators.
  Safe retries with a client order id, pending-order expiry in UTC.
- **Multi-account trading** — one order, or one close, fanned out to several
  accounts in a single request, with idempotency keys for safe retries.
- **Wallet** — read your prepaid balance, pending top-ups and upcoming charges.
- **Market data** — quotes, symbol specifications (incl. commission rules & trading
  sessions), OHLC history, account state & info.
- **Live streaming** — ticks, bars, account, positions, trades, pending orders and
  terminal status over WebSocket, with automatic reconnect + subscription replay,
  and missed deals replayed after a reconnect.
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

    # Move the trade expert to a specific chart symbol (the terminal
    # restarts on it — poll until connected again). "" reverts to automatic.
    account = fx.accounts.update(account, trade_ea_symbol="EURUSDm")

    fx.accounts.delete(account.id)         # unlink
```

An account's terminal can be routed through an outbound proxy at link time.
The proxy is verified first — an unreachable one raises `ConnectFailedError`
with `code == "proxy_unreachable"` and nothing is created. The address, type
and local port are readable on the `Account`; the credentials never come
back.

```python
account = fx.accounts.create(
    server="ICMarkets-Demo", login=1150125, password="…",
    trade_ea_symbol="EURUSDm",                 # optional, broker-exact
    proxy_address="10.0.0.5:1080", proxy_type="socks5",
    proxy_auth="user:secret", proxy_local_port=1080,
)
```

Everything is also available on `AsyncClient`:

```python
from fxsocket import AsyncClient

async with AsyncClient(api_key="fxs_live_…") as fx:
    accounts = await fx.accounts.list()
```

## Read-only keys

A read-only key (`fxs_ro_…`) can call every GET endpoint but nothing that
mutates state. `fx.readonly_keys` manages named ones; each has a `scope` —
`all` sees every account, `selected` only the accounts attached to it
(everything else is absent from lists, 404 by id and 401 at the terminal).
Passing `accounts` implies `selected`.

```python
from fxsocket import Client, KeyScope

with Client(api_key="fxs_live_…") as fx:
    key = fx.readonly_keys.create(name="dashboard", accounts=[account])
    print(key.key)                          # plaintext, returned on every read

    key = fx.readonly_keys.update(key, name="ops-dashboard")
    key = fx.readonly_keys.rotate(key)      # new secret, same name & scope
    fx.readonly_keys.delete(key)            # revoke — immediate, irreversible

    for k in fx.readonly_keys.list():
        print(k.name, k.scope == KeyScope.ALL, k.last_used_at)
```

Terminals are started with the exact set of read-only keys they accept, so
creating, re-scoping, rotating or revoking a key **restarts the terminals of
every account in its scope** — each goes briefly offline, typically a few
minutes. Renaming is free. Key management itself always needs the full
`fxs_live_…` key, even for reads, because the replies contain plaintext
key values.

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

Every order call returns an `OrderResult`. A `200` only means the terminal
answered — check the body: `success` is true for `retcode` `10009` (done) or
`10008` (placed), and `outcome` classifies the result as `applied` /
`no_change` / `partial` / `rejected` (compare against `OrderOutcome`).

`no_change` (retcode `10025`) is a benign, idempotent no-op — the requested
SL/TP/price already match — so it's safe to treat as applied even though
`success` is `False`. For idempotent SL/TP management (e.g. re-sending after a
lost confirmation), send absolute values and gate on `result.is_effective`
(true for both `applied` and `no_change`):

```python
res = term.order_modify(ticket, stop_loss=1.0850)
if res.is_effective:        # applied now, or already in effect
    ...
```

There's also a panic button. `close_all()` closes every open position in one
trade-EA pass — optionally filtered by `symbol` and/or `magic` (`magic=0`
matches manually-opened orders), and `delete_pending=True` also deletes
matching pending orders. It returns a `CloseAllSummary` with per-ticket
results. On a `TradeOutcomeUnknownError` (any `TerminalTimeoutError` on older
bridges) the pass *continues inside the terminal*, so check `opened_orders()`
before acting again rather than re-sending:

```python
summary = term.close_all(symbol="EURUSD", delete_pending=True)
if summary.failed:
    for r in summary.results:
        if not r.success:
            print(r.ticket, r.retcode, r.retcode_description)
```

### Safe retries

No order call is retried by the SDK. To retry one yourself, give `order_send`
a `client_order_id` (1 to 64 characters, remembered by the terminal for 10
minutes). The terminal trades an id once; any repeat trades nothing and gets
the first answer back, with `result.idempotent_replayed` set:

```python
from fxsocket import TradeAbandonedError, TradeOutcomeUnknownError

order = dict(symbol="EURUSD", operation="Buy", volume=0.10,
             client_order_id="copy-7f3a-0001")
try:
    result = term.order_send(**order)
except (TradeOutcomeUnknownError, TradeAbandonedError):
    result = term.order_send(**order)   # safe: same id, traded at most once
```

A 504 now says what happened:

| Exception | Meaning |
|---|---|
| `TradeOutcomeUnknownError` | the terminal took the trade, then timed out; it **may have executed**. Resend only with the same `client_order_id`, or reconcile via `opened_orders()` / `order_history()` |
| `TradeAbandonedError` | the trade never reached the terminal; nothing executed, resending is safe |
| `TerminalTimeoutError` | base class of both; raised directly for a read that timed out |

On bridges older than MT5 0.17.0 / MT4 0.15.0 a trade timeout raises a plain
`TerminalTimeoutError` that may have executed, so treat it like
`TradeOutcomeUnknownError` there. Reusing a `client_order_id` for a different
order raises `ClientOrderIdReusedError`.

Trade replies also carry `position`, the fill `time` (and `time_utc`),
`remainder_ticket` (MT4: where the rest of a partial close went) and
`server_timing` (`queue_ms` waiting for the trade EA, `exec_ms` executing).

### Pending orders and expiry

`pending_orders()` lists the resting orders with what a copy needs: `type`,
`price`, `stop_limit_price`, `sl`/`tp`, `volume_initial`, `volume`,
`expiration_mode`, `expiration` (server time) and `expiration_utc`.

To set an expiry, pass `expiration_utc` (a UTC moment) instead of
`expiration` (broker server time), not both. An aware `datetime` is converted
to UTC, a naive one is taken as UTC already:

```python
from datetime import datetime, timedelta, timezone

term.order_send(
    symbol="EURUSD", operation="BuyLimit", volume=0.10, price=1.0800,
    expiration_utc=datetime.now(timezone.utc) + timedelta(hours=4),
)
```

Brokers enforce a minimum distance for expiries (often several minutes). What a
symbol allows is in `symbol_info(...).expiration_modes` (compare against
`ExpirationMode`), next to `order_modes` and `filling_modes`.

### Warming symbols

A symbol outside Market Watch gets no quotes, so the first order on it waits
for a first tick. `symbol_select([...])` puts up to 100 symbols into Market
Watch in one pass and waits briefly until they quote; `symbol_infos([...])`
fetches up to 100 specs at once, listing failures in `errors` instead of
raising:

```python
for row in term.symbol_select(["EURUSD", "GBPUSD", "XAUUSD"]):
    if not row.selected:
        print(row.symbol, "is unknown to this broker")

specs = term.symbol_infos(["EURUSD", "GBPUSD"])
for err in specs.errors:
    print(err.symbol, err.error)          # e.g. MRPC_UNKNOWN_SYMBOL
```

### Validation and platforms

Inputs are validated client-side before they're sent. One guard worth knowing:
in `order_modify`, a literal `stop_loss=0.0` would *remove* your stop-loss, so
it's rejected — pass `clear_stop_loss=True` to remove one deliberately, while
`None` (the default) keeps the current value.

MT4 and MT5 share one interface, timeframes included. Both accept the same nine:
`M1`, `M5`, `M15`, `M30`, `H1`, `H4`, `D1`, `W1`, `MN1` (or `1min`, `5min`,
`15min`, `30min`, `1h`, `4h`, `1d`, `1w`, `1month`). MT5's other periods, such
as `H2` or `H12`, aren't served by the terminal API, so anything else raises
`ValidationError` before any request.

The one platform gate is stop-limit: MT4 has no stop-limit order type, so
`BuyStopLimit` / `SellStopLimit` (and `stop_limit_price` on `order_modify`)
raise `UnsupportedOnPlatformError` on MT4 before anything is sent.

> **MT4 history note:** on MT4, `price_history` with `from_`/`to` bounds (or the
> `D1` timeframe) can fail server-side with `CopyRates failed` when the terminal
> hasn't loaded that history. Calling `price_history(symbol, timeframe)` without
> date bounds returns the most recent bars reliably.

## Multi-account trading

`fx.orders` sends one order — or one close — to several accounts in a single
request, through the management API rather than each terminal. Every leg
inherits `defaults` and may override any of it, so "same trade, three
accounts, three lot sizes" stays short. A leg can be an `OrderLeg`, a plain
dict, or just the account (object or id) when `defaults` say everything else.

```python
from fxsocket import Client, OrderDefaults, OrderLeg

with Client(api_key="fxs_live_…") as fx:
    accounts = [a for a in fx.accounts.list() if a.has_terminal]

    result = fx.orders.send(
        [OrderLeg(account_id=a, volume=0.1 * (i + 1)) for i, a in enumerate(accounts)],
        defaults=OrderDefaults(symbol="EURUSD", operation="buy", stop_loss=1.07),
        idempotency_key="signal-4711",
    )
    for account, leg in zip(accounts, result.results):
        print(account.nickname, leg.status, leg.order, leg.message)
```

**Validation is all-or-nothing, execution is not.** A malformed leg raises
`ValidationError` before anything is sent (the SDK checks what the API
checks — symbol / operation / volume present, a price for pending orders,
…). Once dispatched the legs are independent: you get a `BatchOrderResult`
with a positional `results` list — `zip` it with what you sent rather than
matching on `account_id`, which repeats when several legs target one
account. Each `OrderLegResult.status` is only trustworthy as `filled`; a
`timeout` leg *may* have executed (`is_unknown`), so never blind-retry it.
`result.all_filled`, `filled_legs`, `failed_legs` and `unknown_legs` roll
this up.

Symbols are per broker (`EURUSD`, `EURUSD.sd`, `EURUSDm`), so a single
`defaults` symbol across mixed brokers will partly fail by design — that is
what a leg-level `symbol` is for.

**Idempotency.** Pass an `idempotency_key` (any opaque string, ≤ 128 chars)
whenever a retry is possible: replaying the identical batch with the same
key returns the stored reply (`idempotent_replay=True`) and sends nothing.
Keys are remembered for 15 minutes. A key that is still in flight, was
already used for a different body, or can't currently be guaranteed raises
`IdempotencyError` — nothing is sent in any of those cases.

Closing works by **selector**, not by ticket: each account is matched
against `symbol` (plus optional `side`, `kind`, `magic`), the backend
resolves the tickets from that account's open orders and closes them.
`symbol` is required — type `"*"` to mean every symbol; it is never implied.
`symbol_match="base"` lets one selector reach `EURUSD`, `EURUSD.sd` and
`EURUSDm` across brokers. `kind` defaults to `position`, so a routine close
does not also delete resting pending orders.

```python
from fxsocket import CloseDefaults, CloseLeg

closed = fx.orders.close(
    [
        CloseLeg(account_id=accounts[0], side="long", volume=0.05),   # partial
        CloseLeg(account_id=accounts[1], tickets=[123456, 123457]),  # explicit
        accounts[2],                                                 # defaults only
    ],
    defaults=CloseDefaults(symbol="EURUSD", symbol_match="base"),
    idempotency_key="flatten-4711",
)
for leg in closed.results:
    print(leg.account_id, leg.status, f"{leg.closed}/{leg.matched}")
    for ticket in leg.results:
        print("   ", ticket.ticket, ticket.status, ticket.retcode_description)
```

`nothing_matched` is a normal answer, not an error. Per-account `status`
compares against `CloseLegStatus` and per-ticket against
`ClosedTicketStatus`; `skipped` tickets were never sent, `timeout` ones may
well have closed. An `idempotency_key` matters most for partial closes,
where a blind retry genuinely over-closes.

Both calls need the full `fxs_live_…` key — a read-only key raises
`ForbiddenError`.

## Streaming (WebSocket)

Subscribe to live ticks, bars, account, positions, trades, pending orders and
terminal status. Streaming is async-first; a synchronous wrapper is provided
too. A dropped connection auto-reconnects and replays active subscriptions
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

Data events carry the frame envelope (bridges MT5 0.17.0 / MT4 0.15.0 on):
`seq` numbers a topic's live frames (a gap means frames were dropped for you),
`ts` is when the bridge sent it (Unix ms, UTC), and `snapshot=True` marks the
current-state frame sent right after a subscribe. A `StreamWarning` names the
`topic` that lost frames. The server pings every 5 s; the WebSocket library
answers by itself, so keep iterating the stream and nothing else is needed.

### Trade events

A `TradeUpdate` carries the full deal: `commission`, `swap`, `magic` and a
real `comment` alongside `profit` (bridges MT5 0.12+ / MT4 0.11+; zero on
older pods). Event-only P&L accounting is `data.net_profit`
(`profit + commission + swap`).

Correlate the `In` and `Out` events of one round-trip through
`data.position` — on MT5, `Out` deals carry `magic=0` / `comment=""` unless
the closing request set them (platform behavior, not a bridge gap), so
position id is the reliable join key. On MT4, `deal` is always 0 and
`position` equals the order ticket. The same id appears as `position` in
`order_history()` rows (bridges MT5 0.14+ / MT4 0.13+) and as
`position_id` in `position_history()`.

If the bridge can't fully enrich an event in time it sets
`data.degraded=True`: identifiers, `symbol`, `type`, `volume` and `price`
are still trustworthy, but `entry` is `"Unknown"` and the cost fields are
zeroed — reconcile that deal via `order_history()`.

```python
async for event in s:
    match event:
        case TradeUpdate() as t if t.data.degraded:
            reconcile_later(t.data.position)     # costs/entry unreliable
        case TradeUpdate() as t if t.data.entry == DealEntry.OUT:
            print(t.data.position, "closed, net", t.data.net_profit)
```

From bridges MT5 0.17.0 / MT4 0.15.0 on, a deal also carries the position's
`sl` / `tp`, `position_volume` (volume still open after it; `-1` = unknown)
and `time_utc`. On MT4 a partial close moves the rest to a new ticket: the
`Out` of the closed part names it in `remainder_ticket`, and the remainder's
`In` points back with `remainder_of`. A `ModifyUpdate` on the same topic
reports an open position's moved SL/TP.

### Trades after a reconnect

With `resume_trades=True` (the default) the stream remembers the newest deal
`time` it delivered. After an automatic reconnect it resubscribes with that moment
as `since`, so the terminal replays the deals you missed (`replay=True`, then a
`Replayed` event) before the live frames continue. Deals that arrive twice
around the switch-over are dropped (by `deal` on MT5, by `order` + `entry` on
MT4). You can also start from a known point yourself:

```python
await s.subscribe_trades(since=last_seen_trade.data.time)   # broker server time
```

`since` is **broker server time** as the frames print it, not UTC, and the
replay reaches at most 24 hours back (the newest 1000 deals). A `Replayed`
with `truncated=True`, or an error frame for a longer gap, means you should
reconcile via `order_history()`. Pass `resume_trades=False` to
`fx.stream(...)` to get exactly the frames the server sends. Bridges older
than MT5 0.17.0 / MT4 0.15.0 ignore `since` (no replay).

### Pending orders stream

`subscribe_orders()` first sends an `OrdersUpdate` snapshot of the resting
orders, then an `OrderUpdate` per step of an order's life. `data.event` is
`placed`, `modified`, `deleted`, `expired` or `triggered` (compare against
`OrderEventKind`); on `triggered`, `data.position` is the position the fill
opened or added to.

```python
from fxsocket import OrderEventKind, OrdersUpdate, OrderUpdate

await s.subscribe_orders()
async for event in s:
    match event:
        case OrdersUpdate():
            book = {o.ticket: o for o in event.data}
        case OrderUpdate() as o if o.data.event == OrderEventKind.TRIGGERED:
            print(o.data.ticket, "filled into position", o.data.position)
```

## Errors

Every failure raises a subclass of `fxsocket.FxSocketError`:

| Exception | When |
|---|---|
| `AuthError` | missing/invalid API key |
| `ForbiddenError` | key not allowed to do this (read-only key on a mutating call) |
| `RateLimitError` | rate limited (`.retry_after`) |
| `ValidationError` | malformed request (`.code`: `invalid_batch`, `unknown_account`, …) |
| `IdempotencyError` | batch refused because of its `Idempotency-Key` (`.code`) |
| `NotFoundError` | account/resource not found |
| `PaymentRequiredError` | base for every 402 below (plan / balance doesn't allow it) |
| `AccountCapError` | plan account limit reached (`.cap`, `.current`) |
| `NoSubscriptionError` | no plan permits linking accounts |
| `InsufficientBalanceError` | prepaid balance too low (`.shortfall_eur_cents`, `.shortfall_eur`) |
| `SeatLapsedError` | seats lapsed, existing accounts unseated — renew first |
| `DuplicateAccountError` | account already linked |
| `ConnectFailedError` | broker rejected the login |
| `ClientOrderIdReusedError` | `client_order_id` already used for a different order |
| `TerminalNotReadyError` | terminal not provisioned / not ready (nothing executed) |
| `TerminalTimeoutError` | the terminal didn't answer in time (base of the two below) |
| `TradeOutcomeUnknownError` | a trade timed out after the terminal took it; may have executed |
| `TradeAbandonedError` | a trade never reached the terminal; safe to resend |
| `UnsupportedOnPlatformError` | feature not available on this platform (stop-limit on MT4) |

Terminal errors carry a fixed code in `.code`; branch on it (compare against
`TerminalErrorCode`, e.g. `MRPC_UNKNOWN_SYMBOL`, `MRPC_SYMBOL_NOT_QUOTING`),
never on the message.

```python
from fxsocket import Client, AccountCapError, InsufficientBalanceError

try:
    fx.accounts.create(server="Demo", login=1, password="…")
except AccountCapError as e:
    print(f"Plan limit reached: {e.current}/{e.cap}")
except InsufficientBalanceError as e:
    print(f"Top up {e.shortfall_eur} EUR first")   # None if the API gave no figure
```

## Private hosting

Dedicated private servers are managed through `client.private_servers`:

```python
import time

from fxsocket import Client, PrivateAccountStatus, SlotsFullError

with Client(api_key="fxs_live_...", verify_terminal_tls=False) as fx:
    [server] = fx.private_servers.list()
    print(server.name, server.status, f"{server.used_slots}/{server.purchased_slots}")

    try:
        account = fx.private_servers.add_account(
            server, server="ICMarkets-Demo", login=1150125, password="..."
        )
    except SlotsFullError as err:
        print(f"Server full ({err.used}/{err.cap})")
        server = fx.private_servers.resize(server, slots=err.cap + 1)

    # Poll until the on-server agent has the terminal up, then trade as usual.
    while True:
        server = fx.private_servers.get(server)
        account = next(a for a in server.accounts if a.id == account.id)
        if account.status == PrivateAccountStatus.READY:
            break
        time.sleep(5)

    print(fx.terminal(account).account_summary())
```

Accounts on a private server are traded and streamed exactly like
shared-cluster accounts — their `rest_url` / `ws_url` simply point at the
server's dedicated IP. The server presents a self-signed certificate, so reach
it with `Client(..., verify_terminal_tls=False)` (or supply a pinned CA).

`server.cancel_at_period_end` is `True` once a server has been told to stop
instead of renewing; it then runs until `server.period_end` and expires.

### Buying, resizing and canceling

`fx.private_servers.regions()` returns where servers may run, how big they may
be and what that costs — call it before buying rather than hardcoding slugs:

```python
options = fx.private_servers.regions()
if options.enabled:
    print(options.region_codes)                     # ['fra1', 'lon1', ...]
    print(options.max_slots, options.max_servers)
    print(options.monthly_price_eur(3))             # Decimal('45') — 3 slots/month
```

`create()` buys one, charged to the prepaid balance immediately. It comes back
`provisioning`; poll `get()` until it is `ready`, usually a couple of minutes:

```python
from fxsocket import InsufficientBalanceError, PrivateServerStatus, ServerLimitError

try:
    server = fx.private_servers.create(slots=2, region="fra1", name="prop-guard")
except InsufficientBalanceError as err:
    print(f"Top up {err.shortfall_eur} EUR first")
except ServerLimitError:
    print(f"Already own the maximum ({options.max_servers})")

while server.status != PrivateServerStatus.READY:
    time.sleep(10)
    server = fx.private_servers.get(server)
```

`resize()` changes the slot count. Increases are prorated over the rest of the
period and charged now (the renewal date doesn't move); decreases are free and
apply at the next renewal, so paid-for capacity is never destroyed mid-month.
Shrinking below the accounts already on the server raises
`AccountsExceedTargetError` — remove accounts first:

```python
server = fx.private_servers.resize(server, slots=4)
```

`cancel()` stops the server renewing: it runs until `period_end`, then expires.
`resume()` undoes that while the period lasts (afterwards the machine is gone
and `AlreadyLapsedError` is raised). `delete()` destroys the machine and every
account on it right away, with **no refund** for the rest of the prepaid month —
prefer `cancel()` unless you really want it gone now:

```python
server = fx.private_servers.cancel(server)      # stop at period_end
assert server.cancel_at_period_end
server = fx.private_servers.resume(server)      # changed your mind
fx.private_servers.delete(server)               # irreversible, no refund
```

All of these move the prepaid balance, so they only work on balance-funded
servers — a card- or crypto-funded one raises `NotBalanceFundedError` (a
`ForbiddenError` subclass) and is managed from the dashboard. Read-only
`fxs_ro_…` keys get a plain `ForbiddenError`.

## Wallet

`fx.wallet.get()` is a read-only view of your prepaid balance: what is in
it, top-ups that haven't landed yet, and what the balance will pay for over
the next 30 days (account seats and balance-funded private servers
together). Amounts are integer EUR cents; the `*_eur` properties give
`Decimal` euros.

```python
wallet = fx.wallet.get()
print(f"balance {wallet.balance_eur} EUR, covers next 30 days: {wallet.covers_upcoming}")
for charge in wallet.upcoming:
    print(f"  {charge.when:%Y-%m-%d} {charge.kind:6} {charge.label} {charge.amount_eur} EUR")
if not wallet.covers_upcoming:
    print(f"top up at least {wallet.shortfall_eur} EUR")
```

Affordability is cumulative — with 24 EUR and three 12 EUR renewals the
first two are covered and the third is not — so `shortfall_eur` is the
total gap, not the size of any single charge. Topping up happens in the
dashboard; the balance is only ever *spent* through the SDK (account seats
and `private_servers.create()` / `resize()`), never topped up.

## Timestamps

Terminal timestamps (`quote.time`, candle `time`, order times) are returned as
**strings in broker server time** — not Python `datetime`. The trailing `Z` is
stylistic and does **not** mean UTC. Use `terminal.server_timezone()` to get the
broker's UTC offset if you need to convert.

Fields named `…_utc` (`time_utc`, `expiration_utc`) are real UTC. They are
set on live data (trade replies, stream frames, pending orders) by bridges
MT5 0.17.0 / MT4 0.15.0 on; history endpoints stay in server time.

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
