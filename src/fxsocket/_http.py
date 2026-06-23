"""Thin transport helpers shared by the sync and async clients.

The response handling is identical for both, so it lives in one place; the
sync/async split is only in who awaits the network call.
"""

from __future__ import annotations

from typing import Any

import httpx

from ._version import __version__
from .errors import error_from_response


def auth_headers(api_key: str) -> dict[str, str]:
    """Default headers carrying the API key for every request."""
    return {
        "X-API-Key": api_key,
        "User-Agent": f"fxsocket-python/{__version__}",
        "Accept": "application/json",
    }


def process_response(resp: httpx.Response) -> Any:
    """Return the decoded JSON body, or raise a typed error on failure."""
    if resp.is_success:
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()
    raise error_from_response(resp)


class SyncHTTP:
    """Synchronous request wrapper over an :class:`httpx.Client`."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        resp = self._client.request(method, path, params=params, json=json)
        return process_response(resp)


class AsyncHTTP:
    """Asynchronous request wrapper over an :class:`httpx.AsyncClient`."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        resp = await self._client.request(method, path, params=params, json=json)
        return process_response(resp)
