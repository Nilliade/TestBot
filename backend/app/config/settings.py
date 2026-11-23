"""Settings and instrument catalog loading (SPEC §1, §5)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.app.domain.instrument import Instrument

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INSTRUMENTS_PATH = Path(__file__).resolve().parent / "instruments.yaml"
DEFAULT_SESSIONS_PATH = Path(__file__).resolve().parent / "sessions.yaml"
DEFAULT_DATA_DIR = REPO_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TP_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8765
    data_dir: Path = DEFAULT_DATA_DIR
    sqlite_path: Path = DEFAULT_DATA_DIR / "market.db"
    instruments_path: Path = DEFAULT_INSTRUMENTS_PATH
    sessions_path: Path = DEFAULT_SESSIONS_PATH
    candle_flush_interval_sec: float = 5.0
    ws_client_queue_limit: int = 100


class InstrumentsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    instruments: list[Instrument] = Field(default_factory=list)


def load_instruments(path: Path | None = None) -> list[Instrument]:
    p = Path(path) if path is not None else DEFAULT_INSTRUMENTS_PATH
    if not p.exists():
        raise FileNotFoundError(f"instruments file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    parsed = InstrumentsFile.model_validate(raw)
    # Enforce uniqueness of (source_id, instrument_id) and storage key per venue partition.
    seen_ids: set[tuple[str, str]] = set()
    seen_keys: set[tuple[str, str]] = set()
    for inst in parsed.instruments:
        key_id = (inst.source_id, inst.instrument_id)
        if key_id in seen_ids:
            raise ValueError(f"duplicate (source_id, instrument_id): {key_id}")
        seen_ids.add(key_id)
        key_storage = (inst.venue_id, inst.instrument_storage_key)
        if key_storage in seen_keys:
            raise ValueError(f"duplicate storage key in venue {inst.venue_id}: {inst.instrument_storage_key}")
        seen_keys.add(key_storage)
    return parsed.instruments
