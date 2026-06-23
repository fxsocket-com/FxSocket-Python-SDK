"""Pydantic models for FxSocket API payloads.

Terminal-API payloads use ``camelCase`` on the wire; models accept those via
aliases while exposing ``snake_case`` attributes, so the same model serves
MT4 and MT5 (MT4 simply leaves some fields at their zero value).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from .enums import Platform, TradingStatus


class _Base(BaseModel):
    """Shared config: populate by field name or camelCase alias, ignore extras."""

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
        extra="ignore",
    )


class Account(_Base):
    """A linked trading account, as returned by the management API (v1).

    ``rest_url`` / ``ws_url`` are where this account's terminal REST and
    WebSocket APIs live. Both are empty until the account has a reachable
    terminal (shared pod or private droplet); a bridge-only account exposes
    none.
    """

    # v1 returns snake_case already, so no aliasing is needed here.
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    nickname: str = ""
    platform: Platform
    server: str
    login: int
    status: TradingStatus
    error: str = ""
    rest_url: str = ""
    ws_url: str = ""
    created_at: datetime

    @property
    def has_terminal(self) -> bool:
        """True when this account exposes a reachable terminal API."""
        return bool(self.rest_url)
