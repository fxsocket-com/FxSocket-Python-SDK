"""Account management — the public v1 API (``/v1/accounts``).

Accessible as ``client.accounts`` on both :class:`~fxsocket.Client` and
:class:`~fxsocket.AsyncClient`.
"""

from __future__ import annotations

from ._http import AsyncHTTP, SyncHTTP
from .enums import Platform
from .models import Account


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
