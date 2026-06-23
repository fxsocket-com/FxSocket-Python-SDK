"""Library-wide defaults."""

from __future__ import annotations

#: Base URL of the public management API (account CRUD + status).
DEFAULT_BASE_URL = "https://api.fxsocket.com/v1"

#: Default request timeout, in seconds, for REST calls.
DEFAULT_TIMEOUT = 30.0

#: Environment variable read when no ``api_key`` is passed explicitly.
ENV_API_KEY = "FXSOCKET_API_KEY"
