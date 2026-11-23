"""Quote contract (SPEC §2)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from backend.app.domain.instrument import SCHEMA_VERSION, _parse_decimal_strict

ALLOWED_QUOTE_FLAGS = {
    "CROSSED",
    "MISSING_BID",
    "MISSING_ASK",
    "MISSING_LAST",
    "STALE",
    "TIME_INCONSISTENT",
    "INVALID",
}


def _require_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    if value.utcoffset() is None or value.utcoffset().total_seconds() != 0:
        # Normalize to UTC for comparison; require UTC offset zero.
        raise ValueError(f"{field_name} must be UTC (offset zero)")
    return value


class Quote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    source_id: str
    instrument_id: str
    bid: Decimal | None = None
    ask: Decimal | None = None
    last: Decimal | None = None
    exchange_ts_utc: datetime | None = None
    received_ts_utc: datetime
    bid_age_ms: int | None = None
    ask_age_ms: int | None = None
    quality_flags: set[str] = Field(default_factory=set)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: str) -> str:
        if v != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION!r}, got {v!r}")
        return v

    @field_validator("bid", "ask", "last", mode="before")
    @classmethod
    def _check_decimal(cls, v: object, info) -> object:
        if v is None:
            return None
        return _parse_decimal_strict(v, info.field_name)

    @field_validator("received_ts_utc")
    @classmethod
    def _check_received(cls, v: datetime) -> datetime:
        return _require_utc(v, "received_ts_utc")

    @field_validator("exchange_ts_utc")
    @classmethod
    def _check_exchange(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        return _require_utc(v, "exchange_ts_utc")

    @field_validator("quality_flags")
    @classmethod
    def _check_flags(cls, v: set[str]) -> set[str]:
        bad = set(v) - ALLOWED_QUOTE_FLAGS
        if bad:
            raise ValueError(f"quality_flags contains unsupported values: {sorted(bad)}")
        return v

    @field_validator("bid_age_ms", "ask_age_ms")
    @classmethod
    def _check_age(cls, v: int | None, info) -> int | None:
        if v is not None and v < 0:
            raise ValueError(f"{info.field_name} must be >= 0")
        return v

    @model_validator(mode="after")
    def _check_prices(self) -> "Quote":
        for name in ("bid", "ask", "last"):
            val = getattr(self, name)
            if val is not None and val <= 0:
                raise ValueError(f"{name} must be > 0")
        return self

    @computed_field
    @property
    def spread_bps(self) -> Decimal | None:
        """(ask-bid)/mid*10000 only for fresh valid consistent sides; else None."""
        if self.bid is None or self.ask is None:
            return None
        if "CROSSED" in self.quality_flags or "INVALID" in self.quality_flags:
            return None
        if self.ask < self.bid:
            return None
        mid = (self.ask + self.bid) / Decimal(2)
        if mid == 0:
            return None
        return (self.ask - self.bid) / mid * Decimal(10000)

    @computed_field
    @property
    def mid(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        if self.ask < self.bid:
            return None
        return (self.ask + self.bid) / Decimal(2)
