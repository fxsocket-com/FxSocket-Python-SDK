"""Tests for read-only key management (``client.readonly_keys``)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from fxsocket import (
    Account,
    AsyncClient,
    Client,
    ForbiddenError,
    KeyScope,
    NotFoundError,
    Platform,
    ValidationError,
)

BASE = "https://api.fxsocket.com/v1"
KEY_ID = "9c2a1c1e-4d8f-4b7e-9a1f-0c4a7b1e2d33"
A1 = "d04096e8-79cd-4078-bc8c-0fd245198938"
A2 = "11111111-1111-1111-1111-111111111111"

ACCOUNT = Account.model_validate(
    {
        "id": A1,
        "platform": "mt5",
        "server": "ICMarkets-Demo",
        "login": 1150125,
        "status": "connected",
        "created_at": "2026-06-22T16:53:56Z",
    }
)

SCOPED_KEY = {
    "id": KEY_ID,
    "name": "dashboard",
    "key": "fxs_ro_abc123",
    "scope": "selected",
    "accounts": [
        {
            "id": A1,
            "nickname": "demo",
            "platform": "mt5",
            "server": "ICMarkets-Demo",
            "login": 1150125,
        }
    ],
    "created_at": "2026-09-01T10:00:00Z",
    "last_used_at": None,
}

ALL_KEY = {
    "id": "22222222-2222-2222-2222-222222222222",
    "name": "monitor",
    "key": "fxs_ro_def456",
    "scope": "all",
    "accounts": [],
    "created_at": "2026-08-01T10:00:00Z",
    "last_used_at": "2026-09-09T08:00:00Z",
}


def _client() -> Client:
    return Client(api_key="fxs_live_test")


def _sent(route: respx.Route) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(route.calls.last.request.content)
    return body


@respx.mock
def test_list_parses_models() -> None:
    respx.get(f"{BASE}/readonly-keys").mock(
        return_value=httpx.Response(200, json=[SCOPED_KEY, ALL_KEY])
    )
    with _client() as fx:
        scoped, everything = fx.readonly_keys.list()

    assert scoped.key == "fxs_ro_abc123"
    assert scoped.scope == KeyScope.SELECTED
    assert scoped.is_scoped
    assert scoped.account_ids == [A1]
    assert scoped.accounts[0].platform is Platform.MT5
    assert scoped.last_used_at is None
    assert everything.scope == "all" and not everything.is_scoped
    assert everything.last_used_at is not None
    assert everything.created_at.year == 2026


@respx.mock
def test_get_accepts_model_or_id() -> None:
    route = respx.get(f"{BASE}/readonly-keys/{KEY_ID}").mock(
        return_value=httpx.Response(200, json=SCOPED_KEY)
    )
    with _client() as fx:
        by_id = fx.readonly_keys.get(KEY_ID)
        by_model = fx.readonly_keys.get(by_id)
    assert route.call_count == 2
    assert by_model.name == "dashboard"


@respx.mock
def test_create_defaults_to_all_scope() -> None:
    route = respx.post(f"{BASE}/readonly-keys").mock(
        return_value=httpx.Response(201, json=ALL_KEY)
    )
    with _client() as fx:
        key = fx.readonly_keys.create(name="monitor")
    assert _sent(route) == {"name": "monitor", "scope": "all"}
    assert key.key == "fxs_ro_def456"


@respx.mock
def test_create_with_accounts_implies_selected() -> None:
    route = respx.post(f"{BASE}/readonly-keys").mock(
        return_value=httpx.Response(201, json=SCOPED_KEY)
    )
    with _client() as fx:
        fx.readonly_keys.create(name="dashboard", accounts=[ACCOUNT, A2])
    assert _sent(route) == {
        "name": "dashboard",
        "scope": "selected",
        "account_ids": [A1, A2],
    }


def test_create_validates_before_sending() -> None:
    with _client() as fx:
        with pytest.raises(ValidationError, match="at least one account"):
            fx.readonly_keys.create(name="x", scope=KeyScope.SELECTED)
        with pytest.raises(ValidationError, match="blank"):
            fx.readonly_keys.create(name="   ")
        with pytest.raises(ValueError):
            fx.readonly_keys.create(name="x", scope="everything")


@respx.mock
def test_update_sends_only_given_fields() -> None:
    route = respx.patch(f"{BASE}/readonly-keys/{KEY_ID}").mock(
        return_value=httpx.Response(200, json=SCOPED_KEY)
    )
    with _client() as fx:
        fx.readonly_keys.update(KEY_ID, name="renamed")
        assert _sent(route) == {"name": "renamed"}

        fx.readonly_keys.update(KEY_ID, accounts=[A2])
        assert _sent(route) == {"scope": "selected", "account_ids": [A2]}

        fx.readonly_keys.update(KEY_ID, scope="all")
        assert _sent(route) == {"scope": "all"}


@respx.mock
def test_rotate_and_delete() -> None:
    rotated = {**SCOPED_KEY, "key": "fxs_ro_new789"}
    rotate = respx.post(f"{BASE}/readonly-keys/{KEY_ID}/rotate").mock(
        return_value=httpx.Response(200, json=rotated)
    )
    delete = respx.delete(f"{BASE}/readonly-keys/{KEY_ID}").mock(
        return_value=httpx.Response(204)
    )
    with _client() as fx:
        key = fx.readonly_keys.rotate(KEY_ID)
        assert fx.readonly_keys.delete(key) is None
    assert key.key == "fxs_ro_new789"
    assert rotate.called and delete.called


@respx.mock
def test_readonly_key_is_forbidden_even_on_reads() -> None:
    respx.get(f"{BASE}/readonly-keys").mock(
        return_value=httpx.Response(
            403, json={"detail": "key management requires your full key"}
        )
    )
    respx.get(f"{BASE}/readonly-keys/{KEY_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "Not found."})
    )
    with Client(api_key="fxs_ro_test") as fx:
        with pytest.raises(ForbiddenError):
            fx.readonly_keys.list()
        with pytest.raises(NotFoundError):
            fx.readonly_keys.get(KEY_ID)


@respx.mock
async def test_async_mirror() -> None:
    respx.get(f"{BASE}/readonly-keys").mock(
        return_value=httpx.Response(200, json=[ALL_KEY])
    )
    create = respx.post(f"{BASE}/readonly-keys").mock(
        return_value=httpx.Response(201, json=SCOPED_KEY)
    )
    respx.patch(f"{BASE}/readonly-keys/{KEY_ID}").mock(
        return_value=httpx.Response(200, json=SCOPED_KEY)
    )
    respx.post(f"{BASE}/readonly-keys/{KEY_ID}/rotate").mock(
        return_value=httpx.Response(200, json=SCOPED_KEY)
    )
    respx.delete(f"{BASE}/readonly-keys/{KEY_ID}").mock(
        return_value=httpx.Response(204)
    )
    async with AsyncClient(api_key="fxs_live_test") as fx:
        [existing] = await fx.readonly_keys.list()
        created = await fx.readonly_keys.create(name="dashboard", accounts=[A1])
        updated = await fx.readonly_keys.update(created, name="dash")
        rotated = await fx.readonly_keys.rotate(created)
        await fx.readonly_keys.delete(created)
    assert existing.scope == KeyScope.ALL
    assert _sent(create)["scope"] == "selected"
    assert updated.id == rotated.id == KEY_ID
