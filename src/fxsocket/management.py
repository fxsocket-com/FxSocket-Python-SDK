"""Account management — the public v1 API.

``client.accounts`` (``/v1/accounts``), ``client.private_servers``
(``/v1/private-servers``), ``client.readonly_keys`` (``/v1/readonly-keys``)
and the read-only ``client.wallet`` (``/v1/wallet``), on both
:class:`~fxsocket.Client` and :class:`~fxsocket.AsyncClient`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ._http import AsyncHTTP, SyncHTTP
from .enums import KeyScope, Platform
from .errors import ValidationError
from .models import (
    Account,
    PrivateServer,
    PrivateServerAccount,
    PrivateServerOptions,
    ReadOnlyKey,
    Wallet,
)


def account_id_of(account: Account | str) -> str:
    """Accept either an :class:`Account` or a bare id string."""
    return account.id if isinstance(account, Account) else str(account)


def _create_payload(
    *,
    server: str,
    login: int,
    password: str,
    platform: Platform | str,
    nickname: str,
    trade_ea_symbol: str,
    proxy_address: str,
    proxy_type: str,
    proxy_auth: str,
    proxy_local_port: int | None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "platform": Platform(platform).value,
        "server": server,
        "login": login,
        "password": password,
        "nickname": nickname,
        "trade_ea_symbol": trade_ea_symbol,
    }
    if proxy_address or proxy_type or proxy_auth or proxy_local_port is not None:
        if not proxy_address:
            raise ValidationError("proxy_address is required when using a proxy")
        if proxy_local_port is not None and not 1 <= proxy_local_port <= 65535:
            raise ValidationError(
                f"proxy_local_port must be 1–65535, got {proxy_local_port}"
            )
        body["proxy_address"] = proxy_address
        body["proxy_type"] = proxy_type
        body["proxy_auth"] = proxy_auth
        if proxy_local_port is not None:
            body["proxy_local_port"] = proxy_local_port
    return body


def _update_payload(*, trade_ea_symbol: str | None) -> dict[str, object]:
    body: dict[str, object] = {}
    if trade_ea_symbol is not None:
        body["trade_ea_symbol"] = trade_ea_symbol
    if not body:
        raise ValidationError("nothing to update — pass trade_ea_symbol")
    return body


class Accounts:
    """Synchronous account operations."""

    def __init__(self, http: SyncHTTP) -> None:
        self._http = http

    def list(self) -> list[Account]:
        """List every account owned by the authenticated user."""
        data = self._http.request("GET", "/accounts")
        return [Account.model_validate(row) for row in data]

    def get(self, account: Account | str) -> Account:
        """Fetch one account by id (use this to poll connection status)."""
        data = self._http.request("GET", f"/accounts/{account_id_of(account)}")
        return Account.model_validate(data)

    def create(
        self,
        *,
        server: str,
        login: int,
        password: str,
        platform: Platform | str = Platform.MT5,
        nickname: str = "",
        trade_ea_symbol: str = "",
        proxy_address: str = "",
        proxy_type: str = "",
        proxy_auth: str = "",
        proxy_local_port: int | None = None,
    ) -> Account:
        """Link (connect) a new MT4/MT5 account. Returns it in ``connecting``
        state — poll :meth:`get` until it reaches ``connected``.

        ``trade_ea_symbol`` is the chart symbol the terminal hosts its trade
        expert on, exactly as the broker names it (e.g. ``EURUSDm`` on a
        suffixed broker); leave empty to let the terminal pick one.

        The optional ``proxy_*`` arguments route this account's terminal
        through an outbound proxy: ``proxy_address`` (``host:port``),
        ``proxy_type`` (as the terminal names it, e.g. ``socks5`` /
        ``http``), ``proxy_auth`` (``user:password``, write-only) and
        ``proxy_local_port``. The proxy is verified before the account is
        created — an unreachable one raises
        :class:`~fxsocket.ConnectFailedError` with ``code ==
        "proxy_unreachable"``.
        """
        data = self._http.request(
            "POST",
            "/accounts",
            json=_create_payload(
                server=server,
                login=login,
                password=password,
                platform=platform,
                nickname=nickname,
                trade_ea_symbol=trade_ea_symbol,
                proxy_address=proxy_address,
                proxy_type=proxy_type,
                proxy_auth=proxy_auth,
                proxy_local_port=proxy_local_port,
            ),
        )
        return Account.model_validate(data)

    def update(
        self, account: Account | str, *, trade_ea_symbol: str | None = None
    ) -> Account:
        """Change the trade-EA host symbol (``PATCH /accounts/{id}``).

        The terminal restarts on the new symbol right away — poll
        :meth:`get` until ``status`` returns to ``connected``. Pass an empty
        string to revert to automatic selection. Nothing else is mutable:
        different credentials or server mean a different broker account
        (unlink and relink instead).
        """
        data = self._http.request(
            "PATCH",
            f"/accounts/{account_id_of(account)}",
            json=_update_payload(trade_ea_symbol=trade_ea_symbol),
        )
        return Account.model_validate(data)

    def delete(self, account: Account | str) -> None:
        """Unlink (disconnect) an account and tear down its terminal."""
        self._http.request("DELETE", f"/accounts/{account_id_of(account)}")


class AsyncAccounts:
    """Asynchronous mirror of :class:`Accounts`."""

    def __init__(self, http: AsyncHTTP) -> None:
        self._http = http

    async def list(self) -> list[Account]:
        data = await self._http.request("GET", "/accounts")
        return [Account.model_validate(row) for row in data]

    async def get(self, account: Account | str) -> Account:
        data = await self._http.request("GET", f"/accounts/{account_id_of(account)}")
        return Account.model_validate(data)

    async def create(
        self,
        *,
        server: str,
        login: int,
        password: str,
        platform: Platform | str = Platform.MT5,
        nickname: str = "",
        trade_ea_symbol: str = "",
        proxy_address: str = "",
        proxy_type: str = "",
        proxy_auth: str = "",
        proxy_local_port: int | None = None,
    ) -> Account:
        data = await self._http.request(
            "POST",
            "/accounts",
            json=_create_payload(
                server=server,
                login=login,
                password=password,
                platform=platform,
                nickname=nickname,
                trade_ea_symbol=trade_ea_symbol,
                proxy_address=proxy_address,
                proxy_type=proxy_type,
                proxy_auth=proxy_auth,
                proxy_local_port=proxy_local_port,
            ),
        )
        return Account.model_validate(data)

    async def update(
        self, account: Account | str, *, trade_ea_symbol: str | None = None
    ) -> Account:
        data = await self._http.request(
            "PATCH",
            f"/accounts/{account_id_of(account)}",
            json=_update_payload(trade_ea_symbol=trade_ea_symbol),
        )
        return Account.model_validate(data)

    async def delete(self, account: Account | str) -> None:
        await self._http.request("DELETE", f"/accounts/{account_id_of(account)}")


# --------------------------------------------------------------------------- #
# Private servers (``/v1/private-servers``)
# --------------------------------------------------------------------------- #


def private_server_id_of(server: PrivateServer | str) -> str:
    """Accept either a :class:`PrivateServer` or a bare id string."""
    return server.id if isinstance(server, PrivateServer) else str(server)


def _private_account_payload(
    *,
    server: str,
    login: int,
    password: str,
    platform: Platform | str,
    nickname: str,
    trade_ea_symbol: str,
) -> dict[str, object]:
    return {
        "platform": Platform(platform).value,
        "server": server,
        "login": login,
        "password": password,
        "nickname": nickname,
        "trade_ea_symbol": trade_ea_symbol,
    }


def _private_server_payload(*, slots: int, region: str, name: str) -> dict[str, object]:
    body: dict[str, object] = {"slots": slots, "region": region}
    if name:
        body["name"] = name
    return body


class PrivateServers:
    """Synchronous private-server operations.

    Covers the whole lifecycle: :meth:`regions` to see what can be bought,
    :meth:`create` to buy one from the prepaid balance, :meth:`resize`,
    :meth:`cancel` / :meth:`resume`, :meth:`delete`, and
    :meth:`add_account` / :meth:`remove_account` for the accounts on it.
    Balance is the only payment method here — card and crypto purchases
    need a checkout redirect, so those stay in the dashboard.
    """

    def __init__(self, http: SyncHTTP) -> None:
        self._http = http

    def list(self) -> list[PrivateServer]:
        """List every private server owned by the authenticated user."""
        data = self._http.request("GET", "/private-servers")
        return [PrivateServer.model_validate(row) for row in data]

    def regions(self) -> PrivateServerOptions:
        """Where private servers may run, how big they may be, what it costs.

        Returns the whole options payload, not just the region list — see
        :class:`~fxsocket.PrivateServerOptions`.
        """
        data = self._http.request("GET", "/private-servers/regions")
        return PrivateServerOptions.model_validate(data)

    def get(self, server: PrivateServer | str) -> PrivateServer:
        """Fetch one server by id (use this to poll account readiness)."""
        data = self._http.request(
            "GET", f"/private-servers/{private_server_id_of(server)}"
        )
        return PrivateServer.model_validate(data)

    def create(self, *, slots: int, region: str, name: str = "") -> PrivateServer:
        """Buy a dedicated server, charged to the prepaid balance now.

        ``region`` must be one of the ``code`` values from :meth:`regions`,
        ``slots`` how many accounts it should hold (priced
        ``first_slot + additional_slot * (slots - 1)`` per month, billed
        again every month until you :meth:`cancel` it) and ``name`` an
        optional label for your own reference.

        Comes back already ``provisioning`` — poll :meth:`get` until
        ``status`` is ``ready``, usually a couple of minutes. Raises
        :class:`~fxsocket.InsufficientBalanceError` when the balance does
        not cover it, :class:`~fxsocket.ServerLimitError` when you already
        own the maximum, and :class:`~fxsocket.ForbiddenError` when private
        hosting is off for the deployment or the key is read-only.
        """
        data = self._http.request(
            "POST",
            "/private-servers",
            json=_private_server_payload(slots=slots, region=region, name=name),
        )
        return PrivateServer.model_validate(data)

    def resize(self, server: PrivateServer | str, *, slots: int) -> PrivateServer:
        """Change how many accounts the server may hold.

        Increases are prorated over what is left of the current period and
        charged to the balance immediately; the renewal date does not move.
        Decreases are free and take effect at the next renewal, so capacity
        already paid for is never destroyed mid-period.

        Raises :class:`~fxsocket.AccountsExceedTargetError` when more
        accounts are on the server than the new limit allows (remove some
        first), :class:`~fxsocket.InsufficientBalanceError` when the
        balance does not cover a prorated increase, and
        :class:`~fxsocket.NotBalanceFundedError` for card- or
        crypto-funded servers.
        """
        data = self._http.request(
            "PATCH",
            f"/private-servers/{private_server_id_of(server)}",
            json={"slots": slots},
        )
        return PrivateServer.model_validate(data)

    def delete(self, server: PrivateServer | str) -> None:
        """Destroy the machine and everything on it — irreversible.

        The droplet is torn down, its IP released and every account hosted
        on it removed. There is **no refund**: whatever is left of the
        prepaid month is forfeited. To stop paying without losing the rest
        of the period, use :meth:`cancel` instead.
        """
        self._http.request("DELETE", f"/private-servers/{private_server_id_of(server)}")

    def cancel(self, server: PrivateServer | str) -> PrivateServer:
        """Stop the server renewing, letting the paid period run out.

        It keeps running until ``period_end``, then expires; nothing is
        refunded and nothing is charged again. Prefer this to
        :meth:`delete`, which forfeits the rest of the month. Reversible
        with :meth:`resume` while the period lasts. Raises
        :class:`~fxsocket.NotBalanceFundedError` for card- or
        crypto-funded servers.
        """
        data = self._http.request(
            "POST", f"/private-servers/{private_server_id_of(server)}/cancel"
        )
        return PrivateServer.model_validate(data)

    def resume(self, server: PrivateServer | str) -> PrivateServer:
        """Undo a :meth:`cancel`, so the server renews from the balance
        again at the end of the current period.

        Only works while it is still running: raises
        :class:`~fxsocket.AlreadyLapsedError` once the paid period has
        lapsed and the machine is gone — buy a new one with :meth:`create`.
        """
        data = self._http.request(
            "DELETE", f"/private-servers/{private_server_id_of(server)}/cancel"
        )
        return PrivateServer.model_validate(data)

    def add_account(
        self,
        private_server: PrivateServer | str,
        *,
        server: str,
        login: int,
        password: str,
        platform: Platform | str = Platform.MT5,
        nickname: str = "",
        trade_ea_symbol: str = "",
    ) -> PrivateServerAccount:
        """Connect an MT4/MT5 account onto the server.

        The on-server agent brings the terminal up asynchronously — poll
        :meth:`get` until the account's ``status`` reaches ``ready``.
        Raises :class:`~fxsocket.SlotsFullError` when every purchased slot
        is taken and :class:`~fxsocket.DuplicateAccountError` when the
        account is already linked.
        """
        data = self._http.request(
            "POST",
            f"/private-servers/{private_server_id_of(private_server)}/accounts",
            json=_private_account_payload(
                server=server,
                login=login,
                password=password,
                platform=platform,
                nickname=nickname,
                trade_ea_symbol=trade_ea_symbol,
            ),
        )
        return PrivateServerAccount.model_validate(data)

    def remove_account(
        self,
        private_server: PrivateServer | str,
        account: PrivateServerAccount | str,
    ) -> None:
        """Detach an account from the server, freeing its slot."""
        sid = private_server_id_of(private_server)
        aid = account.id if isinstance(account, PrivateServerAccount) else str(account)
        self._http.request("DELETE", f"/private-servers/{sid}/accounts/{aid}")


class AsyncPrivateServers:
    """Asynchronous mirror of :class:`PrivateServers`."""

    def __init__(self, http: AsyncHTTP) -> None:
        self._http = http

    async def list(self) -> list[PrivateServer]:
        data = await self._http.request("GET", "/private-servers")
        return [PrivateServer.model_validate(row) for row in data]

    async def regions(self) -> PrivateServerOptions:
        data = await self._http.request("GET", "/private-servers/regions")
        return PrivateServerOptions.model_validate(data)

    async def get(self, server: PrivateServer | str) -> PrivateServer:
        data = await self._http.request(
            "GET", f"/private-servers/{private_server_id_of(server)}"
        )
        return PrivateServer.model_validate(data)

    async def create(self, *, slots: int, region: str, name: str = "") -> PrivateServer:
        data = await self._http.request(
            "POST",
            "/private-servers",
            json=_private_server_payload(slots=slots, region=region, name=name),
        )
        return PrivateServer.model_validate(data)

    async def resize(self, server: PrivateServer | str, *, slots: int) -> PrivateServer:
        data = await self._http.request(
            "PATCH",
            f"/private-servers/{private_server_id_of(server)}",
            json={"slots": slots},
        )
        return PrivateServer.model_validate(data)

    async def delete(self, server: PrivateServer | str) -> None:
        await self._http.request(
            "DELETE", f"/private-servers/{private_server_id_of(server)}"
        )

    async def cancel(self, server: PrivateServer | str) -> PrivateServer:
        data = await self._http.request(
            "POST", f"/private-servers/{private_server_id_of(server)}/cancel"
        )
        return PrivateServer.model_validate(data)

    async def resume(self, server: PrivateServer | str) -> PrivateServer:
        data = await self._http.request(
            "DELETE", f"/private-servers/{private_server_id_of(server)}/cancel"
        )
        return PrivateServer.model_validate(data)

    async def add_account(
        self,
        private_server: PrivateServer | str,
        *,
        server: str,
        login: int,
        password: str,
        platform: Platform | str = Platform.MT5,
        nickname: str = "",
        trade_ea_symbol: str = "",
    ) -> PrivateServerAccount:
        data = await self._http.request(
            "POST",
            f"/private-servers/{private_server_id_of(private_server)}/accounts",
            json=_private_account_payload(
                server=server,
                login=login,
                password=password,
                platform=platform,
                nickname=nickname,
                trade_ea_symbol=trade_ea_symbol,
            ),
        )
        return PrivateServerAccount.model_validate(data)

    async def remove_account(
        self,
        private_server: PrivateServer | str,
        account: PrivateServerAccount | str,
    ) -> None:
        sid = private_server_id_of(private_server)
        aid = account.id if isinstance(account, PrivateServerAccount) else str(account)
        await self._http.request("DELETE", f"/private-servers/{sid}/accounts/{aid}")


# --------------------------------------------------------------------------- #
# Read-only keys (``/v1/readonly-keys``)
# --------------------------------------------------------------------------- #


def readonly_key_id_of(key: ReadOnlyKey | str) -> str:
    """Accept either a :class:`ReadOnlyKey` or a bare id string."""
    return key.id if isinstance(key, ReadOnlyKey) else str(key)


def _readonly_key_payload(
    *,
    name: str | None,
    scope: KeyScope | str | None,
    accounts: Iterable[Account | PrivateServerAccount | str] | None,
    partial: bool,
) -> dict[str, Any]:
    """Body for create (``partial=False``) / update (``partial=True``).

    ``scope`` is inferred as ``selected`` when ``accounts`` are given and
    no scope was named; on create it otherwise defaults to ``all``.
    """
    account_ids = (
        None
        if accounts is None
        else [
            a.id if isinstance(a, (Account, PrivateServerAccount)) else str(a)
            for a in accounts
        ]
    )
    if scope is None:
        if account_ids is not None:
            scope = KeyScope.SELECTED
        elif not partial:
            scope = KeyScope.ALL
    resolved = None if scope is None else KeyScope(scope)
    if resolved is KeyScope.SELECTED and not account_ids:
        raise ValidationError(
            "scope='selected' needs at least one account (pass accounts=[...])"
        )
    body: dict[str, Any] = {}
    if name is not None:
        if not name.strip():
            raise ValidationError("name must not be blank")
        body["name"] = name
    if resolved is not None:
        body["scope"] = resolved.value
    if account_ids is not None:
        body["account_ids"] = account_ids
    if not partial and "name" not in body:
        raise ValidationError("name is required")
    return body


class ReadOnlyKeys:
    """Synchronous management of named read-only keys (``client.readonly_keys``).

    Every call needs the full ``fxs_live_…`` key — a read-only key gets
    :class:`~fxsocket.ForbiddenError` even on reads, since replies carry
    plaintext key values.

    Terminals are started with the exact set of read-only keys they accept,
    so creating, re-scoping, rotating or revoking a key **restarts the
    terminals of every account in its scope** (each goes briefly offline,
    typically a few minutes). A key scoped to ``all`` covers every connected
    account. Renaming is free.
    """

    def __init__(self, http: SyncHTTP) -> None:
        self._http = http

    def list(self) -> list[ReadOnlyKey]:
        """List your named read-only keys, plaintext values included."""
        data = self._http.request("GET", "/readonly-keys")
        return [ReadOnlyKey.model_validate(row) for row in data]

    def get(self, key: ReadOnlyKey | str) -> ReadOnlyKey:
        """Fetch one read-only key by id."""
        data = self._http.request("GET", f"/readonly-keys/{readonly_key_id_of(key)}")
        return ReadOnlyKey.model_validate(data)

    def create(
        self,
        *,
        name: str,
        scope: KeyScope | str | None = None,
        accounts: Iterable[Account | PrivateServerAccount | str] | None = None,
    ) -> ReadOnlyKey:
        """Mint a new ``fxs_ro_…`` key.

        ``scope`` is ``"all"`` (the default) or ``"selected"``; passing
        ``accounts`` implies ``"selected"`` unless you say otherwise. A
        selected key only sees those accounts — everything else is absent
        from lists, 404 by id and 401 at the terminal. Raises
        :class:`~fxsocket.ValidationError` on a blank name, unknown account
        ids, ``selected`` without accounts, or when the per-user key limit
        is reached. Restarts the terminals in scope.
        """
        data = self._http.request(
            "POST",
            "/readonly-keys",
            json=_readonly_key_payload(
                name=name, scope=scope, accounts=accounts, partial=False
            ),
        )
        return ReadOnlyKey.model_validate(data)

    def update(
        self,
        key: ReadOnlyKey | str,
        *,
        name: str | None = None,
        scope: KeyScope | str | None = None,
        accounts: Iterable[Account | PrivateServerAccount | str] | None = None,
    ) -> ReadOnlyKey:
        """Rename and/or re-scope a key (partial update; ``None`` keeps a
        field). Renaming is free; changing the scope restarts the terminals
        of the accounts that enter or leave it. Widening to ``"all"`` drops
        the account attachments; passing ``accounts`` implies
        ``"selected"``."""
        body = _readonly_key_payload(
            name=name, scope=scope, accounts=accounts, partial=True
        )
        data = self._http.request(
            "PATCH", f"/readonly-keys/{readonly_key_id_of(key)}", json=body
        )
        return ReadOnlyKey.model_validate(data)

    def rotate(self, key: ReadOnlyKey | str) -> ReadOnlyKey:
        """Issue a new secret for the key, keeping its name and scope.

        The old value stops authenticating this API immediately; terminals
        keep honouring it until they restart onto the new list (which this
        triggers). The returned object carries the new ``key``.
        """
        data = self._http.request(
            "POST", f"/readonly-keys/{readonly_key_id_of(key)}/rotate"
        )
        return ReadOnlyKey.model_validate(data)

    def delete(self, key: ReadOnlyKey | str) -> None:
        """Revoke the key. It stops authenticating immediately and cannot be
        restored; the terminals in its scope restart."""
        self._http.request("DELETE", f"/readonly-keys/{readonly_key_id_of(key)}")


class AsyncReadOnlyKeys:
    """Asynchronous mirror of :class:`ReadOnlyKeys`."""

    def __init__(self, http: AsyncHTTP) -> None:
        self._http = http

    async def list(self) -> list[ReadOnlyKey]:
        data = await self._http.request("GET", "/readonly-keys")
        return [ReadOnlyKey.model_validate(row) for row in data]

    async def get(self, key: ReadOnlyKey | str) -> ReadOnlyKey:
        data = await self._http.request(
            "GET", f"/readonly-keys/{readonly_key_id_of(key)}"
        )
        return ReadOnlyKey.model_validate(data)

    async def create(
        self,
        *,
        name: str,
        scope: KeyScope | str | None = None,
        accounts: Iterable[Account | PrivateServerAccount | str] | None = None,
    ) -> ReadOnlyKey:
        data = await self._http.request(
            "POST",
            "/readonly-keys",
            json=_readonly_key_payload(
                name=name, scope=scope, accounts=accounts, partial=False
            ),
        )
        return ReadOnlyKey.model_validate(data)

    async def update(
        self,
        key: ReadOnlyKey | str,
        *,
        name: str | None = None,
        scope: KeyScope | str | None = None,
        accounts: Iterable[Account | PrivateServerAccount | str] | None = None,
    ) -> ReadOnlyKey:
        body = _readonly_key_payload(
            name=name, scope=scope, accounts=accounts, partial=True
        )
        data = await self._http.request(
            "PATCH", f"/readonly-keys/{readonly_key_id_of(key)}", json=body
        )
        return ReadOnlyKey.model_validate(data)

    async def rotate(self, key: ReadOnlyKey | str) -> ReadOnlyKey:
        data = await self._http.request(
            "POST", f"/readonly-keys/{readonly_key_id_of(key)}/rotate"
        )
        return ReadOnlyKey.model_validate(data)

    async def delete(self, key: ReadOnlyKey | str) -> None:
        await self._http.request("DELETE", f"/readonly-keys/{readonly_key_id_of(key)}")


# --------------------------------------------------------------------------- #
# Wallet (``/v1/wallet``) — informational
# --------------------------------------------------------------------------- #


class WalletResource:
    """Synchronous, read-only view of the prepaid balance (``client.wallet``).

    Informational only: topping up (``/wallet/assets``, ``/wallet/topup``)
    happens in the dashboard and is deliberately not exposed here.
    """

    def __init__(self, http: SyncHTTP) -> None:
        self._http = http

    def get(self) -> Wallet:
        """Balance, pending top-ups and the charges it must cover over the
        next 30 days — one call (``GET /v1/wallet``). Works with a
        read-only key."""
        data = self._http.request("GET", "/wallet")
        return Wallet.model_validate(data)


class AsyncWalletResource:
    """Asynchronous mirror of :class:`WalletResource`."""

    def __init__(self, http: AsyncHTTP) -> None:
        self._http = http

    async def get(self) -> Wallet:
        data = await self._http.request("GET", "/wallet")
        return Wallet.model_validate(data)
