"""Tests for the management client (sync + async) using respx HTTP mocking."""

from __future__ import annotations

import httpx
import pytest
import respx

from fxsocket import (
    AccountCapError,
    AsyncClient,
    AuthError,
    Client,
    DuplicateAccountError,
    Platform,
    TradingStatus,
)

BASE = "https://api.fxsocket.com/v1"

POD_ACCOUNT = {
    "id": "d04096e8-79cd-4078-bc8c-0fd245198938",
    "nickname": "demo",
    "platform": "mt5",
    "server": "ICMarkets-Demo",
    "login": 1150125,
    "status": "connected",
    "error": "",
    "rest_url": "https://api.fxsocket.com/mt5/d04096e8-79cd-4078-bc8c-0fd245198938",
    "ws_url": "wss://api.fxsocket.com/mt5/d04096e8-79cd-4078-bc8c-0fd245198938/ws",
    "created_at": "2026-06-22T16:53:56Z",
}

BRIDGE_ACCOUNT = {
    "id": "11111111-1111-1111-1111-111111111111",
    "nickname": "",
    "platform": "mt4",
    "server": "Demo",
    "login": 42,
    "status": "connecting",
    "error": "",
    "rest_url": "",
    "ws_url": "",
    "created_at": "2026-06-22T16:53:56Z",
}


def _client() -> Client:
    return Client(api_key="fxs_live_test")


def test_missing_api_key_raises_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FXSOCKET_API_KEY", raising=False)
    with pytest.raises(AuthError):
        Client()


def test_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FXSOCKET_API_KEY", "fxs_live_env")
    with Client() as fx:
        assert fx._api_key == "fxs_live_env"


@respx.mock
def test_list_accounts_parses_models() -> None:
    respx.get(f"{BASE}/accounts").mock(
        return_value=httpx.Response(200, json=[POD_ACCOUNT, BRIDGE_ACCOUNT])
    )
    with _client() as fx:
        accounts = fx.accounts.list()

    assert [a.platform for a in accounts] == [Platform.MT5, Platform.MT4]
    pod, bridge = accounts
    assert pod.status is TradingStatus.CONNECTED
    assert pod.has_terminal is True
    assert pod.rest_url.endswith("/mt5/d04096e8-79cd-4078-bc8c-0fd245198938")
    assert bridge.has_terminal is False
    assert bridge.rest_url == ""


@respx.mock
def test_get_account_sends_api_key_header() -> None:
    route = respx.get(f"{BASE}/accounts/{POD_ACCOUNT['id']}").mock(
        return_value=httpx.Response(200, json=POD_ACCOUNT)
    )
    with _client() as fx:
        acct = fx.accounts.get(POD_ACCOUNT["id"])

    assert acct.login == 1150125
    assert route.calls.last.request.headers["X-API-Key"] == "fxs_live_test"


@respx.mock
def test_create_account_posts_payload() -> None:
    route = respx.post(f"{BASE}/accounts").mock(
        return_value=httpx.Response(201, json=POD_ACCOUNT)
    )
    with _client() as fx:
        acct = fx.accounts.create(
            server="ICMarkets-Demo", login=1150125, password="pw", platform="mt5"
        )

    assert acct.id == POD_ACCOUNT["id"]
    import json

    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "platform": "mt5",
        "server": "ICMarkets-Demo",
        "login": 1150125,
        "password": "pw",
        "nickname": "",
    }


@respx.mock
def test_create_defaults_to_mt5() -> None:
    route = respx.post(f"{BASE}/accounts").mock(
        return_value=httpx.Response(201, json=POD_ACCOUNT)
    )
    with _client() as fx:
        fx.accounts.create(server="Demo", login=1, password="pw")
    import json

    assert json.loads(route.calls.last.request.content)["platform"] == "mt5"


@respx.mock
def test_delete_account_returns_none() -> None:
    respx.delete(f"{BASE}/accounts/{POD_ACCOUNT['id']}").mock(
        return_value=httpx.Response(204)
    )
    with _client() as fx:
        assert fx.accounts.delete(POD_ACCOUNT["id"]) is None


@respx.mock
def test_duplicate_maps_to_typed_error() -> None:
    respx.post(f"{BASE}/accounts").mock(
        return_value=httpx.Response(
            409, json={"error": "duplicate", "detail": "already linked"}
        )
    )
    with _client() as fx, pytest.raises(DuplicateAccountError) as exc:
        fx.accounts.create(server="Demo", login=1, password="pw")
    assert exc.value.status_code == 409
    assert exc.value.code == "duplicate"


@respx.mock
def test_cap_reached_carries_cap_and_current() -> None:
    respx.post(f"{BASE}/accounts").mock(
        return_value=httpx.Response(
            402,
            json={
                "error": "account_cap_reached",
                "detail": "limit reached",
                "cap": 3,
                "current": 3,
            },
        )
    )
    with _client() as fx, pytest.raises(AccountCapError) as exc:
        fx.accounts.create(server="Demo", login=1, password="pw")
    assert (exc.value.cap, exc.value.current) == (3, 3)


@respx.mock
def test_bad_key_maps_to_auth_error() -> None:
    respx.get(f"{BASE}/accounts").mock(
        return_value=httpx.Response(401, json={"detail": "Invalid API key."})
    )
    with _client() as fx, pytest.raises(AuthError):
        fx.accounts.list()


@respx.mock
async def test_async_list_accounts() -> None:
    respx.get(f"{BASE}/accounts").mock(
        return_value=httpx.Response(200, json=[POD_ACCOUNT])
    )
    async with AsyncClient(api_key="fxs_live_test") as fx:
        accounts = await fx.accounts.list()
    assert accounts[0].ws_url.startswith("wss://")
