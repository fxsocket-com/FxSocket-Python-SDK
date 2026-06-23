"""Per-account terminal API — REST trading/market-data and WebSocket streaming.

Lands in milestones M2 (REST) and M3 (WebSocket). The endpoint for an account
is taken from ``Account.rest_url`` / ``Account.ws_url`` (populated by the
management API), which already resolves shared-pod vs private-droplet hosting.
"""
