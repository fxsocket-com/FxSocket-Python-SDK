"""Tests for the private-server management client (sync + async)."""

from __future__ import annotations

import httpx
import pytest
import respx

from fxsocket import (
    AsyncClient,
    Client,
    DuplicateAccountError,
    PrivateAccountStatus,
    PrivateServerStatus,
    SlotsFullError,
)

BASE = "https://api.fxsocket.com/v1"

SERVER_ID = "ecf2fa95-4177-4402-8a00-dea33ae0e79a"

SERVER_ACCOUNT = {
    "id": "22222222-2222-2222-2222-222222222222",
    "nickname": "prop-1",
    "platform": "mt5",
    "server": "ICMarkets-Demo",
    "login": 7001,
    "status": "ready",
    "rest_url": f"https://159.223.244.125/22222222-2222-2222-2222-222222222222",
    "ws_url": f"wss://159.223.244.125/22222222-2222-2222-2222-222222222222/ws",
    "trade_ea_symbol": "",
    "created_at": "2026-07-16T08:00:00Z",
}

SERVER = {
    "id": SERVER_ID,
    "name": "My Prop Guard",
    "status": "ready",
    "region": "lon1",
    "ip": "159.223.244.125",
    "purchased_slots": 2,
    "used_slots": 1,
    "period_end": "2026-08-16T07:01:08Z",
    "accounts": [SERVER_ACCOUNT],
}


def _client() -> Client:
    return Client(api_key="fxs_live_test")


@respx.mock
def test_list_servers_parses_models() -> None:
    respx.get(f"{BASE}/private-servers").mock(
        return_value=httpx.Response(200, json=[SERVER])
    )
    with _client() as fx:
        [server] = fx.private_servers.list()
    assert server.name == "My Prop Guard"
    assert server.status == PrivateServerStatus.READY
    assert server.is_ready
    assert server.ip == "159.223.244.125"
    assert server.free_slots == 1
    [account] = server.accounts
    assert account.status == PrivateAccountStatus.READY
    assert account.has_terminal


@respx.mock
def test_get_accepts_model_or_id() -> None:
    route = respx.get(f"{BASE}/private-servers/{SERVER_ID}").mock(
        return_value=httpx.Response(200, json=SERVER)
    )
    with _client() as fx:
        by_id = fx.private_servers.get(SERVER_ID)
        by_model = fx.private_servers.get(by_id)
    assert route.call_count == 2
    assert by_model.id == SERVER_ID


@respx.mock
def test_add_account_payload_and_model() -> None:
    route = respx.post(f"{BASE}/private-servers/{SERVER_ID}/accounts").mock(
        return_value=httpx.Response(201, json=SERVER_ACCOUNT)
    )
    with _client() as fx:
        account = fx.private_servers.add_account(
            SERVER_ID,
            server="ICMarkets-Demo",
            login=7001,
            password="pw",
            nickname="prop-1",
        )
    import json

    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "platform": "mt5",
        "server": "ICMarkets-Demo",
        "login": 7001,
        "password": "pw",
        "nickname": "prop-1",
        "trade_ea_symbol": "",
    }
    assert account.login == 7001


@respx.mock
def test_slots_full_maps_to_typed_error() -> None:
    respx.post(f"{BASE}/private-servers/{SERVER_ID}/accounts").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": "slots_full",
                "detail": "Server is full (2/2).",
                "used": 2,
                "cap": 2,
            },
        )
    )
    with _client() as fx:
        with pytest.raises(SlotsFullError) as err:
            fx.private_servers.add_account(
                SERVER_ID, server="Demo", login=1, password="pw"
            )
    assert err.value.used == 2
    assert err.value.cap == 2


@respx.mock
def test_duplicate_still_maps_to_duplicate_error() -> None:
    respx.post(f"{BASE}/private-servers/{SERVER_ID}/accounts").mock(
        return_value=httpx.Response(
            409, json={"error": "duplicate", "detail": "Already linked."}
        )
    )
    with _client() as fx:
        with pytest.raises(DuplicateAccountError):
            fx.private_servers.add_account(
                SERVER_ID, server="Demo", login=1, password="pw"
            )


@respx.mock
def test_remove_account() -> None:
    account_id = SERVER_ACCOUNT["id"]
    route = respx.delete(
        f"{BASE}/private-servers/{SERVER_ID}/accounts/{account_id}"
    ).mock(return_value=httpx.Response(204))
    with _client() as fx:
        fx.private_servers.remove_account(SERVER_ID, account_id)
    assert route.called


@respx.mock
def test_terminal_client_from_private_account() -> None:
    respx.get(f"{BASE}/private-servers").mock(
        return_value=httpx.Response(200, json=[SERVER])
    )
    with _client() as fx:
        [server] = fx.private_servers.list()
        term = fx.terminal(server.accounts[0], verify=False)
    assert term is not None


@pytest.mark.asyncio
@respx.mock
async def test_async_mirror() -> None:
    respx.get(f"{BASE}/private-servers").mock(
        return_value=httpx.Response(200, json=[SERVER])
    )
    respx.post(f"{BASE}/private-servers/{SERVER_ID}/accounts").mock(
        return_value=httpx.Response(201, json=SERVER_ACCOUNT)
    )
    async with AsyncClient(api_key="fxs_live_test") as fx:
        [server] = await fx.private_servers.list()
        account = await fx.private_servers.add_account(
            server, server="ICMarkets-Demo", login=7001, password="pw"
        )
    assert server.is_ready
    assert account.login == 7001
