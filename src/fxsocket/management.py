"""Account management — the public v1 API (``/v1/accounts``).

Accessible as ``client.accounts`` on both :class:`~fxsocket.Client` and
:class:`~fxsocket.AsyncClient`.
"""

from __future__ import annotations

from ._http import AsyncHTTP, SyncHTTP
from .enums import Platform
from .models import Account, PrivateServer, PrivateServerAccount


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
) -> dict[str, object]:
    return {
        "platform": Platform(platform).value,
        "server": server,
        "login": login,
        "password": password,
        "nickname": nickname,
    }


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
    ) -> Account:
        """Link (connect) a new MT4/MT5 account. Returns it in ``connecting``
        state when terminal pods are enabled, else ``connected``."""
        data = self._http.request(
            "POST",
            "/accounts",
            json=_create_payload(
                server=server,
                login=login,
                password=password,
                platform=platform,
                nickname=nickname,
            ),
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
            ),
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


class PrivateServers:
    """Synchronous private-server operations.

    Read + on-server account management only: purchasing a server,
    canceling, and slot changes happen in the dashboard.
    """

    def __init__(self, http: SyncHTTP) -> None:
        self._http = http

    def list(self) -> list[PrivateServer]:
        """List every private server owned by the authenticated user."""
        data = self._http.request("GET", "/private-servers")
        return [PrivateServer.model_validate(row) for row in data]

    def get(self, server: PrivateServer | str) -> PrivateServer:
        """Fetch one server by id (use this to poll account readiness)."""
        data = self._http.request(
            "GET", f"/private-servers/{private_server_id_of(server)}"
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

    async def get(self, server: PrivateServer | str) -> PrivateServer:
        data = await self._http.request(
            "GET", f"/private-servers/{private_server_id_of(server)}"
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
