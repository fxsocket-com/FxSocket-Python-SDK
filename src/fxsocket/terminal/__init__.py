"""Per-account terminal API — REST trading/market-data (M2) and streaming (M3).

The endpoint for an account comes from ``Account.rest_url`` / ``Account.ws_url``
(populated by the management API), which already resolves shared-pod vs
private-droplet hosting.
"""

from .client import AsyncTerminalClient, TerminalClient

__all__ = ["TerminalClient", "AsyncTerminalClient"]
