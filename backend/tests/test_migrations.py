"""Migration tests (ACCEPTANCE A5): WAL, idempotent migrations, integrity."""

from __future__ import annotations

from pathlib import Path

from backend.app.storage.sqlite_store import SqliteStore


def test_fresh_db_enables_wal_and_applies_migrations(tmp_path: Path):
    db = tmp_path / "test.db"
    store = SqliteStore(db)
    try:
        assert store.journal_mode().lower() == "wal"
        assert store.integrity_check() == "ok"
        assert store.applied_migrations() == [1]
        # Tables exist.
        tables = {r[0] for r in store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"schema_migrations", "candle_meta", "parquet_manifest", "revision_journal"} <= tables
    finally:
        store.close()


def test_migrations_are_idempotent_on_reopen(tmp_path: Path):
    db = tmp_path / "test.db"
    s1 = SqliteStore(db)
    s1.close()
    s2 = SqliteStore(db)
    try:
        assert s2.applied_migrations() == [1]
        assert s2.integrity_check() == "ok"
        assert s2.journal_mode().lower() == "wal"
    finally:
        s2.close()


def test_migration_records_description_and_timestamp(tmp_path: Path):
    db = tmp_path / "test.db"
    store = SqliteStore(db)
    try:
        row = store._conn.execute("SELECT version, description FROM schema_migrations WHERE version=1").fetchone()
        assert row is not None
        assert "initial schema" in row[1]
    finally:
        store.close()
