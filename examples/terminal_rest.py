"""Read account state and market data from an account's terminal (REST).

Run:  FXSOCKET_API_KEY=fxs_live_... python examples/terminal_rest.py <account-id>
"""

import sys

from fxsocket import Client


def main(account_id: str) -> None:
    with Client() as fx:
        account = fx.accounts.get(account_id)
        if not account.has_terminal:
            print("account has no terminal endpoint yet")
            return
        t = fx.terminal(account)

        health = t.status()
        summary = t.account_summary()
        print(f"platform : {account.platform.value}")
        print(f"status   : {health.status} (broker={health.broker.server})")
        print(f"balance  : {summary.balance} {summary.currency}")
        print(f"equity   : {summary.equity}")

        symbol = "EURUSD" if "EURUSD" in set(t.symbols()) else t.symbols()[0]
        q = t.quote(symbol)
        print(f"{symbol}   : bid={q.bid} ask={q.ask}")

        # Latest 5 M5 candles (omit from_/to to get the most recent bars).
        bars = t.price_history(symbol, "M5")[-5:]
        for b in bars:
            print(f"  {b.time}  O={b.open} H={b.high} L={b.low} C={b.close}")

    # To place a trade (this executes a REAL order on the account):
    #   res = t.order_send(symbol="EURUSD", operation="Buy", volume=0.10,
    #                      stop_loss=1.07, take_profit=1.10)
    #   if res.success: print("opened ticket", res.order)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python examples/terminal_rest.py <account-id>")
    main(sys.argv[1])
