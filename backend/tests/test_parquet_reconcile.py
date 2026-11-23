"""Parquet + reconcile tests (ACCEPTANCE A5): atomic publish, failures, manifest."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app.domain.candle import Candle
from backend.app.storage.parquet_store import ParquetStore, file_sha256
from backend.app.storage.sqlite_store import SqliteStore

UTC = timezone.utc


def _candle(open_t: datetime, close: str = "105", revision: int = 1, is_closed: bool = True) -> Candle:
    return Candle(
        source_id="okx-spot",
        instrument_id="BTC-USDT",
        timeframe="1h",
        open_time_utc=open_t,
        close_time_utc=open_t + timedelta(hours=1),
        open="100",
        high="110",
        low="90",
        close=close,
        volume="10",
        volume_type="exchange",
        price_basis="last",
        is_closed=is_closed,
        quality_flags=set(),
        revision=revision,
        received_at_utc=open_t + timedelta(hours=1, minutes=1),
        updated_at_utc=open_t + timedelta(hours=1, minutes=1),
    )


def test_write_and_read_back_closed_candles(tmp_path: Path):
    store = ParquetStore(tmp_path)
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)
    result = store.write_batch([_candle(t0), _candle(t1)], "okx", "spot", "BTC-USDT")
    assert Path(result["file_path"]).exists()
    assert result["candle_count"] == 2
    assert result["file_hash"] == file_sha256(Path(result["file_path"]))
    read_back = store.read_partition(result["file_path"])
    assert len(read_back) == 2
    assert {c.open_time_utc for c in read_back} == {t0, t1}
    # Exact decimal round-trip (no float loss).
    assert str(read_back[0].close) == "105"


def test_write_rejects_unclosed_only_batch_no_partial_publish(tmp_path: Path):
    store = ParquetStore(tmp_path)
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="at least one closed candle"):
        store.write_batch([_candle(t0, is_closed=False)], "okx", "spot", "BTC-USDT")
    assert store.scan_files() == []


def test_write_rejects_mixed_months_and_timeframes(tmp_path: Path):
    store = ParquetStore(tmp_path)
    t_jan = datetime(2026, 1, 31, 23, 0, tzinfo=UTC)
    t_feb = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="multiple months"):
        store.write_batch([_candle(t_jan), _candle(t_feb)], "okx", "spot", "BTC-USDT")
    assert store.scan_files() == []


def test_merge_dedup_keeps_highest_revision(tmp_path: Path):
    store = ParquetStore(tmp_path)
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    r1 = store.write_batch([_candle(t0, close="105", revision=1)], "okx", "spot", "BTC-USDT")
    # Same revision rewrite -> still 1 row.
    r2 = store.write_batch([_candle(t0, close="105", revision=1)], "okx", "spot", "BTC-USDT")
    assert r2["candle_count"] == 1
    # Higher revision wins.
    r3 = store.write_batch([_candle(t0, close="106", revision=2)], "okx", "spot", "BTC-USDT")
    assert r3["candle_count"] == 1
    read_back = store.read_partition(r3["file_path"])
    assert str(read_back[0].close) == "106"
    assert read_back[0].revision == 2
    # Lower revision does not overwrite.
    r4 = store.write_batch([_candle(t0, close="105", revision=1)], "okx", "spot", "BTC-USDT")
    read_back2 = store.read_partition(r4["file_path"])
    assert str(read_back2[0].close) == "106"


def test_failure_between_parquet_and_sqlite_then_reconcile(tmp_path: Path):
    data_dir = tmp_path / "data"
    sqlite = SqliteStore(tmp_path / "m.db")
    store = ParquetStore(data_dir)
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    # Simulate crash after Parquet publish, before SQLite commit.
    with pytest.raises(RuntimeError, match="injected failure"):
        store.write_batch([_candle(t0)], "okx", "spot", "BTC-USDT", _fail_after_publish=True)
    # File exists, but SQLite has no manifest (orphan file).
    files = store.scan_files()
    assert len(files) == 1
    assert sqlite.count_manifest() == 0
    # Reconciliation creates the missing manifest entry (idempotent).
    report = store.reconcile(sqlite)
    assert report.created == 1
    assert report.ok
    assert sqlite.count_manifest() == 1
    # Second reconcile verifies (no duplicate creation).
    report2 = store.reconcile(sqlite)
    assert report2.created == 0
    assert report2.verified == 1
    sqlite.close()


def test_hash_mismatch_is_reported_never_silently_overwritten(tmp_path: Path):
    data_dir = tmp_path / "data"
    sqlite = SqliteStore(tmp_path / "m.db")
    store = ParquetStore(data_dir)
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    result = store.write_batch([_candle(t0)], "okx", "spot", "BTC-USDT")
    sqlite.upsert_manifest(result["partition_key"], result["file_path"], result["file_hash"], 1, 1)
    # Corrupt the file (append bytes -> hash changes, still readable? append breaks parquet footer).
    # Instead, rewrite with different content but keep stale manifest hash.
    store.write_batch([_candle(t0, close="106", revision=2)], "okx", "spot", "BTC-USDT")
    # Manifest still holds old hash -> reconcile must report mismatch.
    report = store.reconcile(sqlite)
    assert not report.ok
    assert len(report.mismatched) == 1
    # Manifest was NOT silently overwritten.
    entry = sqlite.get_manifest(result["partition_key"], result["file_path"])
    assert entry is not None
    assert entry["file_hash"] == result["file_hash"]
    sqlite.close()


def test_orphan_sqlite_rows_detected(tmp_path: Path):
    data_dir = tmp_path / "data"
    sqlite = SqliteStore(tmp_path / "m.db")
    store = ParquetStore(data_dir)
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    sqlite.upsert_candle_meta(_candle(t0), parquet_path=str(data_dir / "missing.parquet"), file_hash="x")
    report = store.reconcile(sqlite)
    assert report.orphan_sqlite_rows == 1
    assert not report.ok
    sqlite.close()
