"""Top-level entry points: :class:`Client` (sync) and :class:`AsyncClient`.

Both expose ``.accounts`` (the management API). The per-account terminal REST
client (``.terminal(account)``) and WebSocket stream (``.stream(account)``)
arrive in later milestones; their signatures are fixed here so the public
surface is stable.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from ._http import AsyncHTTP, SyncHTTP, auth_headers
from .config import DEFAULT_BASE_URL, DEFAULT_TIMEOUT, ENV_API_KEY
from .errors import AuthError, TerminalNotReadyError
from .management import Accounts, AsyncAccounts
from .models import Account
from .terminal.client import AsyncTerminalClient, TerminalClient


def _resolve_api_key(api_key: str | None) -> str:
    key = api_key or os.environ.get(ENV_API_KEY)
    if not key:
        raise AuthError(
            "No API key. Pass api_key=... or set the "
            f"{ENV_API_KEY} environment variable."
        )
    return key


class Client:
    """Synchronous FxSocket client.

    Usage::

        from fxsocket import Client

        with Client(api_key="fxs_live_...") as fx:
            for acct in fx.accounts.list():
                print(acct.nickname, acct.status)
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        verify_terminal_tls: bool = True,
    ) -> None:
        self._api_key = _resolve_api_key(api_key)
        self._timeout = timeout
        #: Verify TLS for terminal calls. Private-hosting droplets use a
        #: self-signed cert; set False (or pass a CA) to reach them.
        self.verify_terminal_tls = verify_terminal_tls
        self._http_client = httpx.Client(
            base_url=base_url,
            headers=auth_headers(self._api_key),
            timeout=timeout,
        )
        #: Account management (the v1 API).
        self.accounts = Accounts(SyncHTTP(self._http_client))
        self._terminals: dict[tuple[str, str], TerminalClient] = {}

    def terminal(
        self,
        account: Account,
        *,
        verify: bool | None = None,
        timeout: float | None = None,
    ) -> TerminalClient:
        """Return a REST client bound to ``account``'s terminal.

        Resolves the endpoint from ``account.rest_url`` (shared pod or private
        droplet). Raises :class:`TerminalNotReadyError` when the account has no
        terminal yet (still provisioning, or bridge-only). Clients are cached
        per endpoint and closed by :meth:`close`. Pass ``verify=False`` for a
        private droplet's self-signed certificate.
        """
        if not account.rest_url:
            raise TerminalNotReadyError(
                f"Account {account.id} has no terminal endpoint yet "
                "(still provisioning, or bridge-only)."
            )
        key = (account.rest_url, account.platform.value)
        cached = self._terminals.get(key)
        if cached is None:
            cached = TerminalClient(
                base_url=account.rest_url,
                api_key=self._api_key,
                platform=account.platform,
                verify=self.verify_terminal_tls if verify is None else verify,
                timeout=self._timeout if timeout is None else timeout,
            )
            self._terminals[key] = cached
        return cached

    def close(self) -> None:
        try:
            for term in self._terminals.values():
                try:
                    term.close()
                except Exception:
                    pass
            self._terminals.clear()
        finally:
            self._http_client.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class AsyncClient:
    """Asynchronous FxSocket client.

    Usage::

        from fxsocket import AsyncClient

        async with AsyncClient(api_key="fxs_live_...") as fx:
            accounts = await fx.accounts.list()
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        verify_terminal_tls: bool = True,
    ) -> None:
        self._api_key = _resolve_api_key(api_key)
        self._timeout = timeout
        self.verify_terminal_tls = verify_terminal_tls
        self._http_client = httpx.AsyncClient(
            base_url=base_url,
            headers=auth_headers(self._api_key),
            timeout=timeout,
        )
        self.accounts = AsyncAccounts(AsyncHTTP(self._http_client))
        self._terminals: dict[tuple[str, str], AsyncTerminalClient] = {}

    def terminal(
        self,
        account: Account,
        *,
        verify: bool | None = None,
        timeout: float | None = None,
    ) -> AsyncTerminalClient:
        """Return an async REST client bound to ``account``'s terminal.

        See :meth:`Client.terminal`. Clients are cached per endpoint and closed
        by :meth:`aclose`.
        """
        if not account.rest_url:
            raise TerminalNotReadyError(
                f"Account {account.id} has no terminal endpoint yet "
                "(still provisioning, or bridge-only)."
            )
        key = (account.rest_url, account.platform.value)
        cached = self._terminals.get(key)
        if cached is None:
            cached = AsyncTerminalClient(
                base_url=account.rest_url,
                api_key=self._api_key,
                platform=account.platform,
                verify=self.verify_terminal_tls if verify is None else verify,
                timeout=self._timeout if timeout is None else timeout,
            )
            self._terminals[key] = cached
        return cached

    def stream(self, account: Account, **kwargs: Any) -> Any:
        """Return a WebSocket stream for ``account``. (Milestone 3.)"""
        raise NotImplementedError("WebSocket streaming lands in milestone M3.")

    async def aclose(self) -> None:
        try:
            for term in self._terminals.values():
                try:
                    await term.aclose()
                except Exception:
                    pass
            self._terminals.clear()
        finally:
            await self._http_client.aclose()

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
