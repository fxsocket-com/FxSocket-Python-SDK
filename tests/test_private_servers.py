"""Tests for the private-server management client (sync + async)."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
import respx

from fxsocket import (
    AccountsExceedTargetError,
    AlreadyLapsedError,
    AsyncClient,
    Client,
    DuplicateAccountError,
    InsufficientBalanceError,
    NotBalanceFundedError,
    PrivateAccountStatus,
    PrivateServerStatus,
    ServerLimitError,
    SlotsFullError,
)
from fxsocket.errors import ForbiddenError

BASE = "https://api.fxsocket.com/v1"

SERVER_ID = "ecf2fa95-4177-4402-8a00-dea33ae0e79a"

SERVER_ACCOUNT = {
    "id": "22222222-2222-2222-2222-222222222222",
    "nickname": "prop-1",
    "platform": "mt5",
    "server": "ICMarkets-Demo",
    "login": 7001,
    "status": "ready",
    "rest_url": "https://159.223.244.125/22222222-2222-2222-2222-222222222222",
    "ws_url": "wss://159.223.244.125/22222222-2222-2222-2222-222222222222/ws",
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
    "cancel_at_period_end": False,
    "period_end": "2026-08-16T07:01:08Z",
    "accounts": [SERVER_ACCOUNT],
}


REGIONS = {
    "enabled": True,
    "regions": [
        {"code": "fra1", "label": "Frankfurt, Germany"},
        {"code": "lon1", "label": "London, United Kingdom"},
    ],
    "max_slots": 10,
    "max_servers": 3,
    "first_slot_eur_cents": 1700,
    "additional_slot_eur_cents": 1400,
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
def test_cancel_at_period_end_is_parsed() -> None:
    canceled = {**SERVER, "cancel_at_period_end": True}
    respx.get(f"{BASE}/private-servers").mock(
        return_value=httpx.Response(200, json=[canceled])
    )
    with _client() as fx:
        [server] = fx.private_servers.list()
    assert server.cancel_at_period_end
    assert server.period_end is not None


@respx.mock
def test_regions_parses_options_and_prices() -> None:
    respx.get(f"{BASE}/private-servers/regions").mock(
        return_value=httpx.Response(200, json=REGIONS)
    )
    with _client() as fx:
        options = fx.private_servers.regions()
    assert options.enabled
    assert options.region_codes == ["fra1", "lon1"]
    assert options.regions[0].label == "Frankfurt, Germany"
    assert options.max_slots == 10
    assert options.monthly_price_eur_cents(1) == 1700
    assert options.monthly_price_eur_cents(3) == 4500
    assert options.monthly_price_eur(3) == Decimal("45.00")
    with pytest.raises(ValueError):
        options.monthly_price_eur_cents(0)


@respx.mock
def test_regions_disabled_deployment() -> None:
    respx.get(f"{BASE}/private-servers/regions").mock(
        return_value=httpx.Response(
            200,
            json={
                "enabled": False,
                "regions": [],
                "max_slots": 0,
                "max_servers": 0,
                "first_slot_eur_cents": 0,
                "additional_slot_eur_cents": 0,
            },
        )
    )
    with _client() as fx:
        options = fx.private_servers.regions()
    assert not options.enabled
    assert options.region_codes == []


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
def test_create_payload_and_model() -> None:
    route = respx.post(f"{BASE}/private-servers").mock(
        return_value=httpx.Response(
            201, json={**SERVER, "status": "provisioning", "accounts": []}
        )
    )
    with _client() as fx:
        server = fx.private_servers.create(slots=2, region="lon1", name="My Prop Guard")
    assert json.loads(route.calls.last.request.content) == {
        "slots": 2,
        "region": "lon1",
        "name": "My Prop Guard",
    }
    assert server.status == PrivateServerStatus.PROVISIONING
    assert not server.is_ready


@respx.mock
def test_create_omits_blank_name() -> None:
    route = respx.post(f"{BASE}/private-servers").mock(
        return_value=httpx.Response(201, json=SERVER)
    )
    with _client() as fx:
        fx.private_servers.create(slots=1, region="fra1")
    assert json.loads(route.calls.last.request.content) == {
        "slots": 1,
        "region": "fra1",
    }


@respx.mock
def test_create_insufficient_balance() -> None:
    respx.post(f"{BASE}/private-servers").mock(
        return_value=httpx.Response(
            402,
            json={
                "error": "insufficient_balance",
                "detail": "Balance does not cover it.",
                "shortfall_eur_cents": 1200,
            },
        )
    )
    with _client() as fx:
        with pytest.raises(InsufficientBalanceError) as err:
            fx.private_servers.create(slots=2, region="lon1")
    assert err.value.shortfall_eur == Decimal("12.00")


@respx.mock
def test_create_server_limit_reached() -> None:
    respx.post(f"{BASE}/private-servers").mock(
        return_value=httpx.Response(
            409, json={"error": "server_limit_reached", "detail": "You own 3 of 3."}
        )
    )
    with _client() as fx:
        with pytest.raises(ServerLimitError) as err:
            fx.private_servers.create(slots=1, region="lon1")
    assert err.value.code == "server_limit_reached"
    assert not isinstance(err.value, DuplicateAccountError)


@respx.mock
def test_resize_sends_slots() -> None:
    route = respx.patch(f"{BASE}/private-servers/{SERVER_ID}").mock(
        return_value=httpx.Response(200, json={**SERVER, "purchased_slots": 4})
    )
    with _client() as fx:
        server = fx.private_servers.resize(SERVER_ID, slots=4)
    assert json.loads(route.calls.last.request.content) == {"slots": 4}
    assert server.purchased_slots == 4
    assert server.free_slots == 3


@respx.mock
def test_resize_below_accounts_is_typed() -> None:
    respx.patch(f"{BASE}/private-servers/{SERVER_ID}").mock(
        return_value=httpx.Response(
            409,
            json={"error": "accounts_exceed_target", "detail": "2 accounts, 1 slot."},
        )
    )
    with _client() as fx:
        with pytest.raises(AccountsExceedTargetError):
            fx.private_servers.resize(SERVER_ID, slots=1)


@respx.mock
def test_resize_not_balance_funded() -> None:
    respx.patch(f"{BASE}/private-servers/{SERVER_ID}").mock(
        return_value=httpx.Response(
            403, json={"error": "not_balance_funded", "detail": "Card-funded server."}
        )
    )
    with _client() as fx:
        with pytest.raises(NotBalanceFundedError) as err:
            fx.private_servers.resize(SERVER_ID, slots=4)
    assert isinstance(err.value, ForbiddenError)


@respx.mock
def test_delete_server() -> None:
    route = respx.delete(f"{BASE}/private-servers/{SERVER_ID}").mock(
        return_value=httpx.Response(204)
    )
    with _client() as fx:
        fx.private_servers.delete(SERVER_ID)
    assert route.called


@respx.mock
def test_cancel_and_resume() -> None:
    canceled = {**SERVER, "cancel_at_period_end": True}
    cancel_route = respx.post(f"{BASE}/private-servers/{SERVER_ID}/cancel").mock(
        return_value=httpx.Response(200, json=canceled)
    )
    resume_route = respx.delete(f"{BASE}/private-servers/{SERVER_ID}/cancel").mock(
        return_value=httpx.Response(200, json=SERVER)
    )
    with _client() as fx:
        stopped = fx.private_servers.cancel(SERVER_ID)
        resumed = fx.private_servers.resume(stopped)
    assert cancel_route.called and resume_route.called
    assert stopped.cancel_at_period_end
    assert not resumed.cancel_at_period_end


@respx.mock
def test_resume_after_lapse() -> None:
    respx.delete(f"{BASE}/private-servers/{SERVER_ID}/cancel").mock(
        return_value=httpx.Response(
            409, json={"error": "already_lapsed", "detail": "Period has lapsed."}
        )
    )
    with _client() as fx:
        with pytest.raises(AlreadyLapsedError):
            fx.private_servers.resume(SERVER_ID)


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
    respx.get(f"{BASE}/private-servers/regions").mock(
        return_value=httpx.Response(200, json=REGIONS)
    )
    respx.post(f"{BASE}/private-servers").mock(
        return_value=httpx.Response(201, json=SERVER)
    )
    respx.patch(f"{BASE}/private-servers/{SERVER_ID}").mock(
        return_value=httpx.Response(200, json={**SERVER, "purchased_slots": 4})
    )
    respx.post(f"{BASE}/private-servers/{SERVER_ID}/cancel").mock(
        return_value=httpx.Response(200, json={**SERVER, "cancel_at_period_end": True})
    )
    respx.delete(f"{BASE}/private-servers/{SERVER_ID}/cancel").mock(
        return_value=httpx.Response(200, json=SERVER)
    )
    respx.delete(f"{BASE}/private-servers/{SERVER_ID}").mock(
        return_value=httpx.Response(204)
    )
    async with AsyncClient(api_key="fxs_live_test") as fx:
        [server] = await fx.private_servers.list()
        account = await fx.private_servers.add_account(
            server, server="ICMarkets-Demo", login=7001, password="pw"
        )
        options = await fx.private_servers.regions()
        bought = await fx.private_servers.create(slots=2, region="lon1")
        bigger = await fx.private_servers.resize(bought, slots=4)
        stopped = await fx.private_servers.cancel(bought)
        resumed = await fx.private_servers.resume(bought)
        await fx.private_servers.delete(bought)
    assert server.is_ready
    assert options.region_codes == ["fra1", "lon1"]
    assert bigger.purchased_slots == 4
    assert stopped.cancel_at_period_end
    assert not resumed.cancel_at_period_end
    assert account.login == 7001
