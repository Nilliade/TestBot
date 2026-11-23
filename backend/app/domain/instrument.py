"""Instrument contract (SPEC §1)."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.domain.enums import ALLOWED_TIMEFRAMES, MarketType, PriceBasis, VolumeType

SCHEMA_VERSION = "1.0"
_STORAGE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _parse_decimal_strict(value: object, field_name: str) -> Decimal:
    """Parse Decimal without float loss; reject float inputs."""
    if isinstance(value, float):
        raise ValueError(f"{field_name}: float input is forbidden (use str/Decimal to avoid precision loss)")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"{field_name}: invalid decimal string {value!r}") from exc
    raise ValueError(f"{field_name}: unsupported type {type(value).__name__}")


class InstrumentSpecs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=False)

    min_qty: Decimal | None = None
    qty_step: Decimal | None = None
    price_step: Decimal | None = None
    min_notional: Decimal | None = None
    contract_size: Decimal | None = None

    @field_validator("min_qty", "qty_step", "price_step", "min_notional", "contract_size", mode="before")
    @classmethod
    def _check_decimal(cls, v: object, info) -> object:
        if v is None:
            return None
        return _parse_decimal_strict(v, info.field_name)


class Instrument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    source_id: str
    instrument_id: str
    instrument_storage_key: str
    alias: str
    venue_id: str
    account_or_feed_id: str
    provider_symbol: str
    market_type: MarketType
    base_ccy: str
    quote_ccy: str
    settlement_ccy: str
    expiry: str | None = None  # ISO-8601 UTC string or null (kept as str for Decimal-safety; parsed by consumers)
    contract_variant: str | None = None
    price_basis: PriceBasis
    volume_type: VolumeType
    supported_timeframes: list[str]
    specs: InstrumentSpecs = Field(default_factory=InstrumentSpecs)
    supports_short: bool = False
    session_calendar_id: str

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: str) -> str:
        if v != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION!r}, got {v!r}")
        return v

    @field_validator("instrument_storage_key")
    @classmethod
    def _check_storage_key(cls, v: str) -> str:
        if v in (".", ".."):
            raise ValueError("instrument_storage_key must not be '.' or '..'")
        if not _STORAGE_KEY_RE.match(v):
            raise ValueError(
                "instrument_storage_key must match [A-Za-z0-9_-]+ "
                "(no '/', ':', spaces, path separators or control chars)"
            )
        return v

    @field_validator("supported_timeframes")
    @classmethod
    def _check_timeframes(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("supported_timeframes must be non-empty")
        if len(set(v)) != len(v):
            raise ValueError("supported_timeframes must not contain duplicates")
        bad = set(v) - ALLOWED_TIMEFRAMES
        if bad:
            raise ValueError(f"supported_timeframes contains unsupported values: {sorted(bad)}")
        return v

    @field_validator("source_id", "instrument_id", "alias", "venue_id", "account_or_feed_id", "provider_symbol", "base_ccy", "quote_ccy", "settlement_ccy", "session_calendar_id")
    @classmethod
    def _check_non_empty(cls, v: str, info) -> str:
        if not v or not v.strip():
            raise ValueError(f"{info.field_name} must be non-empty")
        return v
