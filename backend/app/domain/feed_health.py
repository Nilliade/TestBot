"""FeedHealth contract (SPEC §4)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

from backend.app.domain.enums import FeedStatus
from backend.app.domain.instrument import SCHEMA_VERSION


class FeedHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    source_id: str
    instrument_id: str | None = None  # null = venue-level
    status: FeedStatus
    last_quote_age_ms: int | None = None
    last_heartbeat_age_ms: int | None = None
    subscription_state: str | None = None
    reconnect_count: int = 0
    gaps_detected: int = 0
    clock_skew_ms: int | None = None
    rate_limit_state: str | None = None

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: str) -> str:
        if v != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION!r}, got {v!r}")
        return v

    @field_validator("reconnect_count", "gaps_detected")
    @classmethod
    def _check_non_negative(cls, v: int, info) -> int:
        if v < 0:
            raise ValueError(f"{info.field_name} must be >= 0")
        return v

    @field_validator("last_quote_age_ms", "last_heartbeat_age_ms")
    @classmethod
    def _check_age(cls, v: int | None, info) -> int | None:
        if v is not None and v < 0:
            raise ValueError(f"{info.field_name} must be >= 0")
        return v
