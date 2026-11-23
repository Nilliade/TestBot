"""Backup/restore tests (ACCEPTANCE A5): WAL-aware backup via backup API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app.domain.candle import Candle
from backend.app.storage.sqlite_store import SqliteStore

UTC = timezone.utc


def _candle() -> Candle:
    open_t = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    return Candle(
        source_id="okx-spot",
        instrument_id="BTC-USDT",
        timeframe="1h",
        open_time_utc=open_t,
        close_time_utc=open_t + timedelta(hours=1),
        open="100",
        high="110",
        low="90",
        close="105",
        volume="10",
        volume_type="exchange",
        price_basis="last",
        is_closed=True,
        quality_flags=set(),
        revision=1,
        received_at_utc=open_t + timedelta(hours=1, minutes=1),
        updated_at_utc=open_t + timedelta(hours=1, minutes=1),
    )


def test_backup_and_restore_preserves_data(tmp_path: Path):
    db = tmp_path / "market.db"
    store = SqliteStore(db)
    candle = _candle()
    store.upsert_candle_meta(candle, parquet_path="/tmp/x.parquet", file_hash="abc")
    store.upsert_manifest("okx/spot/BTC-USDT/1h/2026-01", "/tmp/x.parquet", "abc", 1, 1)
    backup_path = tmp_path / "backup.db"
    store.backup(backup_path)
    assert backup_path.exists()
    store.close()

    restored = SqliteStore(tmp_path / "restored.db")
    try:
        restored.restore(backup_path)
        assert restored.integrity_check() == "ok"
        assert restored.journal_mode().lower() == "wal"
        assert restored.count_candle_meta() == 1
        assert restored.count_manifest() == 1
        meta = restored.get_candle_meta("okx-spot", "BTC-USDT", "1h", candle.open_time_utc)
        assert meta is not None
        assert meta["revision"] == 1
    finally:
        restored.close()


def test_backup_handles_wal_without_checkpoint(tmp_path: Path):
    # Backup API must capture WAL data without manual checkpoint.
    db = tmp_path / "market.db"
    store = SqliteStore(db)
    store.upsert_candle_meta(_candle(), parquet_path="/tmp/x.parquet", file_hash="h")
    # Deliberately do NOT call wal_checkpoint before backup.
    backup_path = tmp_path / "b.db"
    store.backup(backup_path)
    store.close()
    check = SqliteStore(tmp_path / "c.db")
    try:
        check.restore(backup_path)
        assert check.count_candle_meta() == 1
    finally:
        check.close()


def test_restore_missing_file_raises(tmp_path: Path):
    store = SqliteStore(tmp_path / "m.db")
    try:
        with pytest.raises(FileNotFoundError):
            store.restore(tmp_path / "does-not-exist.db")
    finally:
        store.close()


def test_revision_dedup_and_conflicts(tmp_path: Path):
    store = SqliteStore(tmp_path / "m.db")
    try:
        c1 = _candle()
        store.upsert_candle_meta(c1, "/tmp/x.parquet", "h1")
        # Same revision + same hash -> no-op (count stays 1, journal 1 row).
        store.upsert_candle_meta(c1, "/tmp/x.parquet", "h1")
        assert store.count_candle_meta() == 1
        assert len(store.list_revisions("okx-spot", "BTC-USDT", "1h", c1.open_time_utc)) == 1
        # Same revision + different hash -> conflict.
        c2 = _candle()
        c2 = c2.model_copy(update={"close": "106"})
        with pytest.raises(ValueError, match="revision conflict"):
            store.upsert_candle_meta(c2, "/tmp/x.parquet", "h2")
        # Lower revision -> regression error.
        c3 = c1.model_copy(update={"revision": 2})
        store.upsert_candle_meta(c3, "/tmp/x.parquet", "h3")
        with pytest.raises(ValueError, match="revision regression"):
            store.upsert_candle_meta(c1, "/tmp/x.parquet", "h1")
    finally:
        store.close()
