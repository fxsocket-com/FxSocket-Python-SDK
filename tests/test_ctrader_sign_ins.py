"""Tests for cTrader API sign-in (``client.ctrader_sign_ins``), respx-mocked."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from fxsocket import (
    Account,
    AsyncClient,
    Client,
    CtraderAccountNotInSignInError,
    CtraderLinkResult,
    CtraderReauthRequiredError,
    CtraderSignIn,
    CtraderSignInStatus,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
    SignInExpiredError,
    SignInLimitError,
    SignInNotCompletedError,
    ValidationError,
)

BASE = "https://api.fxsocket.com/v1"
SIGN_IN_ID = "6f1c2b9e-4d3a-4f6e-9a51-2c7d8e0b1a34"

CREATED = {
    "id": SIGN_IN_ID,
    "authorize_url": "https://id.ctrader.com/my/settings/openapi/grantingaccess/?x=1",
    "status": "pending",
    "expires_at": "2026-11-22T14:10:00Z",
}

GRANTED = {
    "ctrader_account_id": 49471004,
    "login": 5958067,
    "broker": "IC Markets",
    "live": False,
    "account_type": "hedged",
    "currency": "EUR",
    "active": True,
    "unavailable_reason": None,
    "linked_account_id": None,
}

COMPLETED = {
    "id": SIGN_IN_ID,
    "status": "completed",
    "error": None,
    "created_at": "2026-11-22T14:00:00Z",
    "expires_at": "2026-11-22T15:01:12Z",
    "accounts": [
        GRANTED,
        {
            **GRANTED,
            "ctrader_account_id": 49471011,
            "login": 5958070,
            "account_type": "netted",
            "linked_account_id": "0b8f3c52-7a1e-4c9d-8e2f-5d6a7b8c9d0e",
        },
        {
            **GRANTED,
            "ctrader_account_id": 49471012,
            "live": True,
            "account_type": None,
            "currency": None,
            "unavailable_reason": "ctrader_live_not_allowed",
        },
    ],
}

PENDING = {**COMPLETED, "status": "pending", "accounts": []}

LINKED_ACCOUNT = {
    "id": "3c4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e6f",
    "nickname": "Main",
    "platform": "ctrader",
    "server": "IC Markets (demo)",
    "login": 5958067,
    "status": "connected",
    "error": "",
    "suspended": False,
    "reauth_required": False,
    "rest_url": "https://api.fxsocket.com/ctrader/3c4d5e6f",
    "ws_url": "wss://api.fxsocket.com/ctrader/3c4d5e6f/ws",
    "proxy_address": "",
    "proxy_type": "",
    "proxy_local_port": None,
    "trade_ea_symbol": "",
    "created_at": "2026-11-22T14:02:03Z",
}

LINK_REPLY = {
    "accounts": [
        {"ctrader_account_id": 49471004, "account": LINKED_ACCOUNT},
        {
            "ctrader_account_id": 49471011,
            "error": "account_cap_reached",
            "detail": "You have reached your plan limit of 5 accounts.",
            "cap": 5,
            "current": 5,
        },
    ]
}


def _client() -> Client:
    return Client(api_key="fxs_live_test")


@respx.mock
def test_create_sign_in() -> None:
    route = respx.post(f"{BASE}/ctrader/sign-ins").mock(
        return_value=httpx.Response(201, json=CREATED)
    )
    with _client() as fx:
        started = fx.ctrader_sign_ins.create(return_url="https://app.example.com/done")
    assert started.id == SIGN_IN_ID
    assert started.authorize_url.startswith("https://id.ctrader.com/")
    assert started.status == CtraderSignInStatus.PENDING
    assert started.expires_at.year == 2026
    assert json.loads(route.calls.last.request.content) == {
        "return_url": "https://app.example.com/done"
    }


@respx.mock
def test_create_sign_in_without_return_url_sends_empty_body() -> None:
    route = respx.post(f"{BASE}/ctrader/sign-ins").mock(
        return_value=httpx.Response(201, json=CREATED)
    )
    with _client() as fx:
        fx.ctrader_sign_ins.create()
    assert json.loads(route.calls.last.request.content) == {}


@respx.mock
def test_get_sign_in_parses_accounts() -> None:
    respx.get(f"{BASE}/ctrader/sign-ins/{SIGN_IN_ID}").mock(
        return_value=httpx.Response(200, json=COMPLETED)
    )
    with _client() as fx:
        si = fx.ctrader_sign_ins.get(SIGN_IN_ID)
    assert isinstance(si, CtraderSignIn)
    assert si.is_completed and not si.is_pending and si.error is None
    first, linked, live = si.accounts
    assert first.ctrader_account_id == 49471004 and first.login == 5958067
    assert first.is_available and not first.is_linked
    assert linked.is_linked and linked.account_type == "netted"
    assert not live.is_available and live.currency is None
    assert live.unavailable_reason == "ctrader_live_not_allowed"
    assert [a.ctrader_account_id for a in si.linkable_accounts] == [49471004]


@respx.mock
def test_get_accepts_a_started_sign_in() -> None:
    from fxsocket import CtraderSignInStarted

    route = respx.get(f"{BASE}/ctrader/sign-ins/{SIGN_IN_ID}").mock(
        return_value=httpx.Response(200, json=PENDING)
    )
    with _client() as fx:
        fx.ctrader_sign_ins.get(CtraderSignInStarted.model_validate(CREATED))
    assert route.called


@respx.mock
def test_link_accounts_posts_items_and_parses_results() -> None:
    route = respx.post(f"{BASE}/ctrader/sign-ins/{SIGN_IN_ID}/accounts").mock(
        return_value=httpx.Response(200, json=LINK_REPLY)
    )
    si = CtraderSignIn.model_validate(COMPLETED)
    with _client() as fx:
        results = fx.ctrader_sign_ins.link_accounts(
            si,
            [
                {"ctrader_account_id": 49471004, "nickname": "Main"},
                si.accounts[1],
            ],
        )
    assert json.loads(route.calls.last.request.content) == {
        "accounts": [
            {"ctrader_account_id": 49471004, "nickname": "Main"},
            {"ctrader_account_id": 49471011},
        ]
    }
    ok, refused = results
    assert isinstance(ok, CtraderLinkResult) and ok.is_linked
    assert isinstance(ok.account, Account)
    assert ok.account.platform == "ctrader" and ok.account.has_terminal
    assert ok.error is None
    assert not refused.is_linked and refused.account is None
    assert refused.error == "account_cap_reached"
    assert "plan limit" in refused.detail


@respx.mock
def test_link_accounts_takes_bare_ids() -> None:
    route = respx.post(f"{BASE}/ctrader/sign-ins/{SIGN_IN_ID}/accounts").mock(
        return_value=httpx.Response(200, json={"accounts": []})
    )
    with _client() as fx:
        fx.ctrader_sign_ins.link_accounts(SIGN_IN_ID, [49471004, 49471011])
    assert json.loads(route.calls.last.request.content) == {
        "accounts": [
            {"ctrader_account_id": 49471004},
            {"ctrader_account_id": 49471011},
        ]
    }


@pytest.mark.parametrize(
    "accounts",
    [
        [],
        [1, 1],
        list(range(1, 52)),
        [0],
        [-5],
        ["49471004"],
        [True],
        [1.5],
        [{"ctrader_account_id": 1, "nick": "x"}],
        [{"ctrader_account_id": 1, "nickname": "x" * 65}],
        [{"nickname": "x"}],
        "49471004",
    ],
)
@respx.mock
def test_link_accounts_validated_before_sending(accounts: object) -> None:
    route = respx.post(f"{BASE}/ctrader/sign-ins/{SIGN_IN_ID}/accounts").mock(
        return_value=httpx.Response(200, json={"accounts": []})
    )
    with _client() as fx, pytest.raises(ValidationError):
        fx.ctrader_sign_ins.link_accounts(SIGN_IN_ID, accounts)  # type: ignore[arg-type]
    assert not route.called


@respx.mock
def test_link_accounts_fifty_is_fine() -> None:
    respx.post(f"{BASE}/ctrader/sign-ins/{SIGN_IN_ID}/accounts").mock(
        return_value=httpx.Response(200, json={"accounts": []})
    )
    with _client() as fx:
        assert fx.ctrader_sign_ins.link_accounts(SIGN_IN_ID, range(1, 51)) == []


@respx.mock
def test_wait_polls_until_not_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("fxsocket.management.time.sleep", sleeps.append)
    route = respx.get(f"{BASE}/ctrader/sign-ins/{SIGN_IN_ID}").mock(
        side_effect=[
            httpx.Response(200, json=PENDING),
            httpx.Response(200, json=PENDING),
            httpx.Response(200, json=COMPLETED),
        ]
    )
    with _client() as fx:
        si = fx.ctrader_sign_ins.wait(SIGN_IN_ID, interval=1.5)
    assert si.is_completed
    assert route.call_count == 3
    assert sleeps == [1.5, 1.5]


@respx.mock
def test_wait_returns_pending_when_timeout_runs_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter([0.0, 0.0, 3.0])
    monkeypatch.setattr("fxsocket.management.time.monotonic", lambda: next(clock))
    sleeps: list[float] = []
    monkeypatch.setattr("fxsocket.management.time.sleep", sleeps.append)
    route = respx.get(f"{BASE}/ctrader/sign-ins/{SIGN_IN_ID}").mock(
        return_value=httpx.Response(200, json=PENDING)
    )
    with _client() as fx:
        si = fx.ctrader_sign_ins.wait(SIGN_IN_ID, timeout=2.5, interval=2.0)
    assert si.is_pending
    assert route.call_count == 2
    # The second sleep would overshoot the deadline, so it never happens.
    assert sleeps == [2.0]


@respx.mock
def test_wait_returns_a_denied_sign_in() -> None:
    denied = {**PENDING, "status": "denied", "error": "access_denied"}
    respx.get(f"{BASE}/ctrader/sign-ins/{SIGN_IN_ID}").mock(
        return_value=httpx.Response(200, json=denied)
    )
    with _client() as fx:
        si = fx.ctrader_sign_ins.wait(SIGN_IN_ID)
    assert si.status == CtraderSignInStatus.DENIED and si.error == "access_denied"


@pytest.mark.parametrize("kwargs", [{"timeout": -1}, {"interval": 0}])
def test_wait_checks_its_arguments(kwargs: dict) -> None:
    with _client() as fx, pytest.raises(ValidationError):
        fx.ctrader_sign_ins.wait(SIGN_IN_ID, **kwargs)


# -- errors ---------------------------------------------------------------- #


@respx.mock
@pytest.mark.parametrize("code", ["sign_in_pending_limit", "sign_in_hourly_limit"])
def test_create_429_is_a_sign_in_limit_error(code: str) -> None:
    respx.post(f"{BASE}/ctrader/sign-ins").mock(
        return_value=httpx.Response(
            429,
            json={"error": code, "detail": "Too many sign-ins.", "limit": 20},
            headers={"Retry-After": "60"},
        )
    )
    with _client() as fx, pytest.raises(SignInLimitError) as info:
        fx.ctrader_sign_ins.create()
    err = info.value
    assert isinstance(err, RateLimitError)
    assert err.code == code and err.limit == 20 and err.retry_after == 60.0


@respx.mock
def test_create_return_url_not_allowed_is_a_validation_error() -> None:
    respx.post(f"{BASE}/ctrader/sign-ins").mock(
        return_value=httpx.Response(
            400, json={"error": "return_url_not_allowed", "detail": "not allowed"}
        )
    )
    with _client() as fx, pytest.raises(ValidationError) as info:
        fx.ctrader_sign_ins.create(return_url="http://evil.example")
    assert info.value.code == "return_url_not_allowed"


@respx.mock
@pytest.mark.parametrize("code", ["sign_in_not_found", "ctrader_unavailable"])
def test_404_codes(code: str) -> None:
    respx.get(f"{BASE}/ctrader/sign-ins/{SIGN_IN_ID}").mock(
        return_value=httpx.Response(404, json={"error": code, "detail": "nope"})
    )
    with _client() as fx, pytest.raises(NotFoundError) as info:
        fx.ctrader_sign_ins.get(SIGN_IN_ID)
    assert info.value.code == code


@respx.mock
def test_read_only_key_is_forbidden() -> None:
    respx.get(f"{BASE}/ctrader/sign-ins/{SIGN_IN_ID}").mock(
        return_value=httpx.Response(403, json={"detail": "read-only key"})
    )
    with _client() as fx, pytest.raises(ForbiddenError):
        fx.ctrader_sign_ins.get(SIGN_IN_ID)


@respx.mock
@pytest.mark.parametrize(
    ("status", "body", "exc"),
    [
        (
            409,
            {
                "error": "sign_in_not_completed",
                "detail": "pending",
                "status": "pending",
            },
            SignInNotCompletedError,
        ),
        (410, {"error": "sign_in_expired", "detail": "expired"}, SignInExpiredError),
        (
            409,
            {"error": "ctrader_reauth_required", "detail": "sign in again"},
            CtraderReauthRequiredError,
        ),
        (
            400,
            {
                "error": "ctrader_account_not_in_sign_in",
                "detail": "not granted",
                "ctrader_account_ids": [7, 8],
            },
            CtraderAccountNotInSignInError,
        ),
    ],
)
def test_link_errors(status: int, body: dict, exc: type[Exception]) -> None:
    respx.post(f"{BASE}/ctrader/sign-ins/{SIGN_IN_ID}/accounts").mock(
        return_value=httpx.Response(status, json=body)
    )
    with _client() as fx, pytest.raises(exc) as info:
        fx.ctrader_sign_ins.link_accounts(SIGN_IN_ID, [7])
    err = info.value
    if isinstance(err, SignInNotCompletedError):
        assert err.sign_in_status == "pending"
    if isinstance(err, CtraderAccountNotInSignInError):
        assert isinstance(err, ValidationError)
        assert err.ctrader_account_ids == [7, 8]


@respx.mock
async def test_async_sign_in_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("fxsocket.management.asyncio.sleep", fake_sleep)
    respx.post(f"{BASE}/ctrader/sign-ins").mock(
        return_value=httpx.Response(201, json=CREATED)
    )
    respx.get(f"{BASE}/ctrader/sign-ins/{SIGN_IN_ID}").mock(
        side_effect=[
            httpx.Response(200, json=PENDING),
            httpx.Response(200, json=COMPLETED),
        ]
    )
    link = respx.post(f"{BASE}/ctrader/sign-ins/{SIGN_IN_ID}/accounts").mock(
        return_value=httpx.Response(200, json=LINK_REPLY)
    )
    async with AsyncClient(api_key="fxs_live_test") as fx:
        started = await fx.ctrader_sign_ins.create()
        si = await fx.ctrader_sign_ins.wait(started, interval=0.5)
        results = await fx.ctrader_sign_ins.link_accounts(si, si.linkable_accounts)
    assert si.is_completed and sleeps == [0.5]
    assert json.loads(link.calls.last.request.content) == {
        "accounts": [{"ctrader_account_id": 49471004}]
    }
    assert results[0].is_linked


async def test_async_link_validated_before_sending() -> None:
    async with AsyncClient(api_key="fxs_live_test") as fx:
        with pytest.raises(ValidationError):
            await fx.ctrader_sign_ins.link_accounts(SIGN_IN_ID, [])


def test_account_reauth_required_and_suspended() -> None:
    acct = Account.model_validate({**LINKED_ACCOUNT, "reauth_required": True})
    assert acct.reauth_required is True and acct.suspended is False
    bare = {
        k: v
        for k, v in LINKED_ACCOUNT.items()
        if k not in ("suspended", "reauth_required")
    }
    old = Account.model_validate(bare)
    assert old.reauth_required is False and old.suspended is False
