"""Send one trade to every connected account, then flatten it everywhere.

Run:  FXSOCKET_API_KEY=fxs_live_... python examples/multi_account_trade.py

Requires the full ``fxs_live_…`` key — read-only keys cannot trade.
"""

import uuid

from fxsocket import Client, CloseDefaults, OrderDefaults, OrderLeg


def main() -> None:
    with Client() as fx:  # reads FXSOCKET_API_KEY from the environment
        accounts = [a for a in fx.accounts.list() if a.has_terminal]
        if not accounts:
            print("no connected accounts")
            return

        # Same trade on every account; the first one gets a bigger size.
        legs = [OrderLeg(account_id=a) for a in accounts]
        legs[0].volume = 0.2
        result = fx.orders.send(
            legs,
            defaults=OrderDefaults(symbol="EURUSD", operation="buy", volume=0.1),
            idempotency_key=str(uuid.uuid4()),  # makes a retry safe
        )
        print(f"batch {result.batch_id}: {result.summary}")
        for account, leg in zip(accounts, result.results, strict=True):
            print(f"  {account.nickname or account.id}: {leg.status} {leg.message}")
        if result.unknown_legs:
            print("  some legs timed out — reconcile before re-sending")

        # Flatten: close every EURUSD position on every account, whatever
        # suffix the broker uses (EURUSD, EURUSD.sd, EURUSDm ...).
        closed = fx.orders.close(
            accounts,
            defaults=CloseDefaults(symbol="EURUSD", symbol_match="base"),
            idempotency_key=str(uuid.uuid4()),
        )
        for account, leg in zip(accounts, closed.results, strict=True):
            print(
                f"  {account.nickname or account.id}: {leg.status} "
                f"({leg.closed}/{leg.matched} closed)"
            )


if __name__ == "__main__":
    main()
