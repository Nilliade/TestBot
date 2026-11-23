"""SQLite store: WAL mode, migrations, backup/restore (SPEC §5.2).

WAL-aware backup uses sqlite3.Connection.backup() (online backup API).
File-copy backup is supported only after wal_checkpoint + quiesce and is
documented as such; copying -wal/-shm files as a backup is forbidden.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from backend.app.domain.candle import Candle

MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "initial schema: schema_migrations, candle_meta, parquet_manifest, revision_journal",
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at_utc TEXT NOT NULL,
            description TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS candle_meta (
            source_id TEXT NOT NULL,
            instrument_id TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            open_time_utc TEXT NOT NULL,
            close_time_utc TEXT NOT NULL,
            revision INTEGER NOT NULL,
            received_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            quality_flags TEXT NOT NULL,
            parquet_path TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            PRIMARY KEY (source_id, instrument_id, timeframe, open_time_utc)
        );
        CREATE TABLE IF NOT EXISTS parquet_manifest (
            partition_key TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            file_version INTEGER NOT NULL,
            candle_count INTEGER NOT NULL,
            updated_at_utc TEXT NOT NULL,
            PRIMARY KEY (partition_key, file_path)
        );
        CREATE TABLE IF NOT EXISTS revision_journal (
            source_id TEXT NOT NULL,
            instrument_id TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            open_time_utc TEXT NOT NULL,
            revision INTEGER NOT NULL,
            candle_hash TEXT NOT NULL,
            recorded_at_utc TEXT NOT NULL,
            PRIMARY KEY (source_id, instrument_id, timeframe, open_time_utc, revision)
        );
        """,
    ),
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def candle_hash(candle: Candle) -> str:
    """Stable hash of canonical candle fields (for revision journal)."""
    canonical = "|".join(
        [
            candle.source_id,
            candle.instrument_id,
            candle.timeframe,
            candle.open_time_utc.isoformat(),
            candle.close_time_utc.isoformat(),
            str(candle.open),
            str(candle.high),
            str(candle.low),
            str(candle.close),
            str(candle.volume) if candle.volume is not None else "null",
            candle.volume_type.value,
            candle.price_basis.value,
            str(candle.is_closed),
            ",".join(sorted(candle.quality_flags)),
            str(candle.revision),
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SqliteStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._enable_wal()
        self._apply_migrations()

    def _enable_wal(self) -> None:
        cur = self._conn.execute("PRAGMA journal_mode=WAL;")
        mode = cur.fetchone()[0]
        if mode.lower() != "wal":
            raise RuntimeError(f"failed to enable WAL mode, got journal_mode={mode!r}")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.commit()

    def journal_mode(self) -> str:
        cur = self._conn.execute("PRAGMA journal_mode;")
        return str(cur.fetchone()[0])

    def _apply_migrations(self) -> None:
        # schema_migrations table itself is created by migration 1; ensure it exists first.
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_at_utc TEXT NOT NULL, description TEXT NOT NULL)"
        )
        self._conn.commit()
        applied = {row[0] for row in self._conn.execute("SELECT version FROM schema_migrations")}
        for version, description, sql in MIGRATIONS:
            if version in applied:
                continue
            # Each migration is transactional and idempotent.
            with self._conn:
                self._conn.executescript(sql)
                self._conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at_utc, description) VALUES (?, ?, ?)",
                    (version, _utc_now_iso(), description),
                )

    def applied_migrations(self) -> list[int]:
        return [row[0] for row in self._conn.execute("SELECT version FROM schema_migrations ORDER BY version")]

    def integrity_check(self) -> str:
        cur = self._conn.execute("PRAGMA integrity_check;")
        return str(cur.fetchone()[0])

    def wal_checkpoint(self) -> None:
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    # --- candle_meta ---
    def upsert_candle_meta(self, candle: Candle, parquet_path: str, file_hash: str) -> None:
        """Insert or update candle metadata. Revision dedup: same revision + same hash is a no-op."""
        chash = candle_hash(candle)
        with self._conn:
            existing = self._conn.execute(
                "SELECT revision FROM candle_meta WHERE source_id=? AND instrument_id=? AND timeframe=? AND open_time_utc=?",
                (candle.source_id, candle.instrument_id, candle.timeframe, candle.open_time_utc.isoformat()),
            ).fetchone()
            if existing is not None:
                existing_rev = int(existing[0])
                if existing_rev == candle.revision:
                    # Check journal for same hash -> dedup no-op.
                    j = self._conn.execute(
                        "SELECT candle_hash FROM revision_journal WHERE source_id=? AND instrument_id=? AND timeframe=? AND open_time_utc=? AND revision=?",
                        (candle.source_id, candle.instrument_id, candle.timeframe, candle.open_time_utc.isoformat(), candle.revision),
                    ).fetchone()
                    if j is not None and j[0] == chash:
                        return
                    if j is not None and j[0] != chash:
                        raise ValueError(
                            f"revision conflict: same revision {candle.revision} with different hash for "
                            f"{candle.source_id}/{candle.instrument_id}/{candle.timeframe}/{candle.open_time_utc.isoformat()}"
                        )
                if candle.revision < existing_rev:
                    raise ValueError(
                        f"revision regression: existing {existing_rev}, new {candle.revision} for "
                        f"{candle.source_id}/{candle.instrument_id}/{candle.timeframe}/{candle.open_time_utc.isoformat()}"
                    )
            self._conn.execute(
                "INSERT OR REPLACE INTO candle_meta "
                "(source_id, instrument_id, timeframe, open_time_utc, close_time_utc, revision, "
                "received_at_utc, updated_at_utc, quality_flags, parquet_path, file_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    candle.source_id,
                    candle.instrument_id,
                    candle.timeframe,
                    candle.open_time_utc.isoformat(),
                    candle.close_time_utc.isoformat(),
                    candle.revision,
                    candle.received_at_utc.isoformat(),
                    candle.updated_at_utc.isoformat(),
                    ",".join(sorted(candle.quality_flags)),
                    parquet_path,
                    file_hash,
                ),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO revision_journal "
                "(source_id, instrument_id, timeframe, open_time_utc, revision, candle_hash, recorded_at_utc) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    candle.source_id,
                    candle.instrument_id,
                    candle.timeframe,
                    candle.open_time_utc.isoformat(),
                    candle.revision,
                    chash,
                    _utc_now_iso(),
                ),
            )

    def get_candle_meta(self, source_id: str, instrument_id: str, timeframe: str, open_time_utc: datetime) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM candle_meta WHERE source_id=? AND instrument_id=? AND timeframe=? AND open_time_utc=?",
            (source_id, instrument_id, timeframe, open_time_utc.isoformat()),
        ).fetchone()
        return dict(row) if row is not None else None

    def count_candle_meta(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM candle_meta").fetchone()[0])

    # --- parquet_manifest ---
    def upsert_manifest(self, partition_key: str, file_path: str, file_hash: str, file_version: int, candle_count: int) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO parquet_manifest "
                "(partition_key, file_path, file_hash, file_version, candle_count, updated_at_utc) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (partition_key, file_path, file_hash, file_version, candle_count, _utc_now_iso()),
            )

    def get_manifest(self, partition_key: str, file_path: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM parquet_manifest WHERE partition_key=? AND file_path=?",
            (partition_key, file_path),
        ).fetchone()
        return dict(row) if row is not None else None

    def list_manifest(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM parquet_manifest ORDER BY partition_key, file_path")]

    def count_manifest(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM parquet_manifest").fetchone()[0])

    # --- revision_journal ---
    def list_revisions(self, source_id: str, instrument_id: str, timeframe: str, open_time_utc: datetime) -> list[dict]:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM revision_journal WHERE source_id=? AND instrument_id=? AND timeframe=? AND open_time_utc=? ORDER BY revision",
                (source_id, instrument_id, timeframe, open_time_utc.isoformat()),
            )
        ]

    # --- backup / restore (WAL-aware) ---
    def backup(self, dest_path: Path | str) -> None:
        """Online backup via sqlite3 backup API (correctly handles WAL)."""
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        dest_conn = sqlite3.connect(str(dest))
        try:
            with dest_conn:
                self._conn.backup(dest_conn)
        finally:
            dest_conn.close()
        # Verify backup integrity.
        check = sqlite3.connect(str(dest))
        try:
            result = check.execute("PRAGMA integrity_check;").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"backup integrity_check failed: {result}")
        finally:
            check.close()

    def restore(self, src_path: Path | str) -> None:
        """Restore from backup file. Caller must stop the writer first."""
        src = Path(src_path)
        if not src.exists():
            raise FileNotFoundError(f"backup not found: {src}")
        # Close current connection, replace file, reopen.
        self.close()
        # Remove WAL/SHM of the target to avoid stale WAL replay.
        for suffix in ("-wal", "-shm"):
            wal = Path(str(self.db_path) + suffix)
            if wal.exists():
                wal.unlink()
        shutil.copyfile(str(src), str(self.db_path))
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._enable_wal()
        result = self.integrity_check()
        if result != "ok":
            raise RuntimeError(f"restore integrity_check failed: {result}")
