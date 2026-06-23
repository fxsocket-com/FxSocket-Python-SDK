"""Stream live ticks and account updates over WebSocket (async).

Run:  FXSOCKET_API_KEY=fxs_live_... python examples/stream_quotes.py <account-id>
"""

import asyncio
import sys

from fxsocket import AccountUpdate, AsyncClient, Tick


async def main(account_id: str) -> None:
    async with AsyncClient() as fx:
        account = await fx.accounts.get(account_id)
        async with fx.stream(account) as s:
            await s.subscribe_prices("EURUSD")
            await s.subscribe_account()
            print("streaming — Ctrl-C to stop")
            async for event in s:
                if isinstance(event, Tick):
                    d = event.data
                    print(f"tick {event.symbol}  bid={d.bid}  ask={d.ask}")
                elif isinstance(event, AccountUpdate):
                    print(f"account equity={event.data.equity} {event.data.currency}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python examples/stream_quotes.py <account-id>")
    try:
        asyncio.run(main(sys.argv[1]))
    except KeyboardInterrupt:
        pass
