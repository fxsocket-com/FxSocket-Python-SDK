"""List the trading accounts linked to your FxSocket API key.

Run:  FXSOCKET_API_KEY=fxs_live_... python examples/manage_accounts.py
"""

from fxsocket import Client


def main() -> None:
    with Client() as fx:  # reads FXSOCKET_API_KEY from the environment
        accounts = fx.accounts.list()
        print(f"{len(accounts)} account(s):")
        for a in accounts:
            terminal = a.rest_url or "(no terminal — bridge-only/provisioning)"
            print(f"  {a.id}  {a.platform.value:3}  {a.status.value:12}  {terminal}")


if __name__ == "__main__":
    main()
