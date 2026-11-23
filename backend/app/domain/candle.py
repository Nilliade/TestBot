"""Candle contract (SPEC §3)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.domain.enums import ALLOWED_TIMEFRAMES, PriceBasis, VolumeType
from backend.app.domain.instrument import SCHEMA_VERSION, _parse_decimal_strict
from backend.app.domain.quote import _require_utc

ALLOWED_CANDLE_FLAGS = {
    "CONFIRMED_GAP",
    "POSSIBLE_GAP",
    "UNKNOWN_GAP",
    "REVISED",
    "PREVIEW",
    "UNCONFIRMED",
    "STALE",
    "INVALID",
}


class Candle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    source_id: str
    instrument_id: str
    timeframe: str
    open_time_utc: datetime
    close_time_utc: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None
    volume_type: VolumeType
    price_basis: PriceBasis
    is_closed: bool
    quality_flags: set[str] = Field(default_factory=set)
    revision: int = 1
    received_at_utc: datetime
    updated_at_utc: datetime

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: str) -> str:
        if v != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION!r}, got {v!r}")
        return v

    @field_validator("timeframe")
    @classmethod
    def _check_tf(cls, v: str) -> str:
        if v not in ALLOWED_TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {sorted(ALLOWED_TIMEFRAMES)}, got {v!r}")
        return v

    @field_validator("open", "high", "low", "close", "volume", mode="before")
    @classmethod
    def _check_decimal(cls, v: object, info) -> object:
        if v is None:
            return None
        return _parse_decimal_strict(v, info.field_name)

    @field_validator("open_time_utc", "close_time_utc", "received_at_utc", "updated_at_utc")
    @classmethod
    def _check_utc(cls, v: datetime, info) -> datetime:
        return _require_utc(v, info.field_name)

    @field_validator("quality_flags")
    @classmethod
    def _check_flags(cls, v: set[str]) -> set[str]:
        bad = set(v) - ALLOWED_CANDLE_FLAGS
        if bad:
            raise ValueError(f"quality_flags contains unsupported values: {sorted(bad)}")
        return v

    @field_validator("revision")
    @classmethod
    def _check_revision(cls, v: int) -> int:
        if v < 1:
            raise ValueError("revision must be >= 1")
        return v

    @model_validator(mode="after")
    def _check_ohlc(self) -> "Candle":
        if self.close_time_utc <= self.open_time_utc:
            raise ValueError("close_time_utc must be > open_time_utc ([open, close) semantics)")
        for name in ("open", "high", "low", "close"):
            val = getattr(self, name)
            if val <= 0:
                raise ValueError(f"{name} must be > 0")
        if self.volume is not None and self.volume < 0:
            raise ValueError("volume must be >= 0")
        if self.high < self.low:
            raise ValueError("high must be >= low")
        if self.high < max(self.open, self.close):
            raise ValueError("high must be >= max(open, close)")
        if self.low > min(self.open, self.close):
            raise ValueError("low must be <= min(open, close)")
        return self

    @property
    def primary_key(self) -> tuple[str, str, str, datetime]:
        return (self.source_id, self.instrument_id, self.timeframe, self.open_time_utc)
