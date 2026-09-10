"""Tests for the read-only wallet accessor (``client.wallet``)."""

from __future__ import annotations

from decimal import Decimal

import httpx
import respx

from fxsocket import AsyncClient, Client

BASE = "https://api.fxsocket.com/v1"

WALLET = {
    "balance_eur_cents": 2400,
    "pending_topups": [
        {
            "id": 17,
            "status": "partial",
            "amount_eur_cents": 5000,
            "credited_eur_cents": 1250,
            "deposit_address": "0xabc",
            "deposit_amount": "50000000",
            "deposit_amount_decimal": "50.0",
            "asset_code": "USDC",
            "blockchain_code": "ETH",
            "expires_at": "2026-09-09T12:30:00Z",
        }
    ],
    "upcoming": [
        {
            "when": "2026-09-15T00:00:00Z",
            "amount_eur_cents": 1200,
            "kind": "seat",
            "label": "demo",
        },
        {
            "when": "2026-09-20T00:00:00Z",
            "amount_eur_cents": 1200,
            "kind": "seat",
            "label": "prop-1",
        },
        {
            "when": "2026-09-28T00:00:00Z",
            "amount_eur_cents": 1200,
            "kind": "server",
            "label": "My Prop Guard",
        },
    ],
    "upcoming_total_eur_cents": 3600,
    "shortfall_eur_cents": 1200,
    "covers_upcoming": False,
}


@respx.mock
def test_get_wallet_parses_models() -> None:
    route = respx.get(f"{BASE}/wallet").mock(
        return_value=httpx.Response(200, json=WALLET)
    )
    with Client(api_key="fxs_ro_test") as fx:
        wallet = fx.wallet.get()

    assert route.calls.last.request.headers["X-API-Key"] == "fxs_ro_test"
    assert wallet.balance_eur_cents == 2400
    assert wallet.balance_eur == Decimal("24")
    assert wallet.covers_upcoming is False
    assert wallet.shortfall_eur == Decimal("12")
    assert wallet.upcoming_total_eur == Decimal("36")

    [topup] = wallet.pending_topups
    assert topup.id == 17
    assert topup.credited_eur == Decimal("12.5")
    assert topup.expires_at is not None and topup.expires_at.year == 2026

    assert [c.kind for c in wallet.upcoming] == ["seat", "seat", "server"]
    assert wallet.upcoming[2].label == "My Prop Guard"
    assert wallet.upcoming[0].amount_eur == Decimal("12")


@respx.mock
def test_get_wallet_tolerates_minimal_body() -> None:
    respx.get(f"{BASE}/wallet").mock(
        return_value=httpx.Response(200, json={"balance_eur_cents": 0})
    )
    with Client(api_key="fxs_live_test") as fx:
        wallet = fx.wallet.get()
    assert wallet.balance_eur == Decimal("0")
    assert wallet.pending_topups == [] and wallet.upcoming == []


@respx.mock
async def test_async_get_wallet() -> None:
    respx.get(f"{BASE}/wallet").mock(return_value=httpx.Response(200, json=WALLET))
    async with AsyncClient(api_key="fxs_live_test") as fx:
        wallet = await fx.wallet.get()
    assert wallet.balance_eur_cents == 2400
