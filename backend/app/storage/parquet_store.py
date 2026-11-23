"""Parquet store: batch write, atomic publish, manifest + reconcile (SPEC §5.3–5.4)."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from backend.app.domain.candle import Candle
from backend.app.domain.enums import PriceBasis, VolumeType

SCHEMA_VERSION = "1.0"

# Parquet schema: decimals as strings (exact, no float loss); timestamps as UTC.
PARQUET_SCHEMA = pa.schema(
    [
        ("schema_version", pa.string()),
        ("source_id", pa.string()),
        ("instrument_id", pa.string()),
        ("timeframe", pa.string()),
        ("open_time_utc", pa.timestamp("us", tz="UTC")),
        ("close_time_utc", pa.timestamp("us", tz="UTC")),
        ("open", pa.string()),
        ("high", pa.string()),
        ("low", pa.string()),
        ("close", pa.string()),
        ("volume", pa.string()),
        ("volume_type", pa.string()),
        ("price_basis", pa.string()),
        ("is_closed", pa.bool_()),
        ("quality_flags", pa.list_(pa.string())),
        ("revision", pa.int64()),
        ("received_at_utc", pa.timestamp("us", tz="UTC")),
        ("updated_at_utc", pa.timestamp("us", tz="UTC")),
    ]
)


def _candle_to_row(c: Candle) -> dict:
    return {
        "schema_version": c.schema_version,
        "source_id": c.source_id,
        "instrument_id": c.instrument_id,
        "timeframe": c.timeframe,
        "open_time_utc": c.open_time_utc,
        "close_time_utc": c.close_time_utc,
        "open": str(c.open),
        "high": str(c.high),
        "low": str(c.low),
        "close": str(c.close),
        "volume": str(c.volume) if c.volume is not None else None,
        "volume_type": c.volume_type.value,
        "price_basis": c.price_basis.value,
        "is_closed": c.is_closed,
        "quality_flags": sorted(c.quality_flags),
        "revision": c.revision,
        "received_at_utc": c.received_at_utc,
        "updated_at_utc": c.updated_at_utc,
    }


def _row_to_candle(row: dict) -> Candle:
    return Candle(
        schema_version=row["schema_version"],
        source_id=row["source_id"],
        instrument_id=row["instrument_id"],
        timeframe=row["timeframe"],
        open_time_utc=row["open_time_utc"],
        close_time_utc=row["close_time_utc"],
        open=row["open"],
        high=row["high"],
        low=row["low"],
        close=row["close"],
        volume=row["volume"],
        volume_type=VolumeType(row["volume_type"]),
        price_basis=PriceBasis(row["price_basis"]),
        is_closed=row["is_closed"],
        quality_flags=set(row["quality_flags"] or []),
        revision=row["revision"],
        received_at_utc=row["received_at_utc"],
        updated_at_utc=row["updated_at_utc"],
    )


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def partition_path(data_dir: Path, venue_slug: str, market_type: str, instrument_storage_key: str, timeframe: str, open_time_utc: datetime) -> Path:
    """data/market/{venue_slug}/{market_type}/{instrument_storage_key}/{timeframe}/{YYYY-MM}.parquet"""
    month = open_time_utc.strftime("%Y-%m")
    return data_dir / "market" / venue_slug / market_type / instrument_storage_key / timeframe / f"{month}.parquet"


def partition_key(venue_slug: str, market_type: str, instrument_storage_key: str, timeframe: str, month: str) -> str:
    return f"{venue_slug}/{market_type}/{instrument_storage_key}/{timeframe}/{month}"


@dataclass
class ReconcileReport:
    verified: int = 0
    created: int = 0
    mismatched: list[str] = field(default_factory=list)
    orphan_sqlite_rows: int = 0
    files_scanned: int = 0

    @property
    def ok(self) -> bool:
        return not self.mismatched and self.orphan_sqlite_rows == 0


class ParquetStore:
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def write_batch(
        self,
        candles: list[Candle],
        venue_slug: str,
        market_type: str,
        instrument_storage_key: str,
        *,
        _fail_after_publish: bool = False,
    ) -> dict:
        """Write closed candles batch. Only is_closed=True candles are written.

        Merges with existing file (dedupe by PK, keep highest revision),
        writes temp file on same FS -> fsync -> atomic replace.
        Returns {file_path, file_hash, file_version, candle_count, partition_key}.
        _fail_after_publish is a test hook: raise after atomic replace but
        before caller commits SQLite (simulates crash between publish and commit).
        """
        closed = [c for c in candles if c.is_closed]
        if not closed:
            raise ValueError("write_batch requires at least one closed candle (is_closed=True)")
        # All candles in one batch must share the same partition month.
        months = {c.open_time_utc.strftime("%Y-%m") for c in closed}
        if len(months) != 1:
            raise ValueError(f"batch spans multiple months {sorted(months)}; split by month")
        month = months.pop()
        target = partition_path(self.data_dir, venue_slug, market_type, instrument_storage_key, closed[0].timeframe, closed[0].open_time_utc)
        # Timeframe must be uniform in batch (one file per timeframe).
        tfs = {c.timeframe for c in closed}
        if len(tfs) != 1:
            raise ValueError(f"batch spans multiple timeframes {sorted(tfs)}; split by timeframe")

        # Read existing rows (if any) for merge.
        existing: dict[tuple, dict] = {}
        if target.exists():
            with open(target, "rb") as rf:
                table = pq.read_table(rf)
            for row in table.to_pylist():
                key = (row["source_id"], row["instrument_id"], row["timeframe"], row["open_time_utc"])
                existing[key] = row

        for c in closed:
            key = (c.source_id, c.instrument_id, c.timeframe, c.open_time_utc)
            new_row = _candle_to_row(c)
            if key in existing:
                if int(existing[key]["revision"]) > c.revision:
                    continue  # keep higher revision
            existing[key] = new_row

        rows = sorted(existing.values(), key=lambda r: (r["open_time_utc"], r["source_id"], r["instrument_id"]))
        table = pa.Table.from_pylist(rows, schema=PARQUET_SCHEMA)

        target.parent.mkdir(parents=True, exist_ok=True)
        # Temp file strictly on same filesystem (same directory).
        fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".parquet", dir=str(target.parent))
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            # Open in writable binary mode ("wb") so os.fsync (Win32 FlushFileBuffers)
            # has GENERIC_WRITE access on Windows as well as POSIX.
            with open(tmp_path, "wb") as f:
                pq.write_table(table, f, compression="zstd", write_statistics=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, target)
            # fsync directory to persist the rename (best-effort on platforms that support it).
            try:
                dir_fd = os.open(str(target.parent), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        except BaseException:
            # Never leave a partial temp file; never publish partial data.
            if tmp_path.exists():
                tmp_path.unlink()
            raise

        file_hash = file_sha256(target)
        # file_version: increment based on existing manifest handled by caller via SQLite;
        # here we return candle_count and let caller upsert manifest. For standalone use,
        # version is derived from hash prefix? Instead, caller passes version via SQLite.
        # We return a version of 1 if no prior manifest; caller reconciles.
        result = {
            "file_path": str(target),
            "file_hash": file_hash,
            "candle_count": len(rows),
            "partition_key": partition_key(venue_slug, market_type, instrument_storage_key, closed[0].timeframe, month),
            "month": month,
        }
        if _fail_after_publish:
            raise RuntimeError("injected failure: after Parquet publish, before SQLite commit")
        return result

    def read_partition(self, file_path: Path | str) -> list[Candle]:
        with open(file_path, "rb") as rf:
            table = pq.read_table(rf)
        return [_row_to_candle(r) for r in table.to_pylist()]

    def scan_files(self) -> list[Path]:
        return sorted(self.data_dir.glob("market/**/*.parquet"))

    def reconcile(self, sqlite) -> ReconcileReport:
        """Idempotent reconciliation: scan Parquet files, compare with SQLite manifest.

        - Missing manifest entries are created (verified=False -> created).
        - Hash mismatches are reported in `mismatched` (never silently overwritten).
        - Orphan SQLite candle_meta rows without Parquet data are counted.
        """
        report = ReconcileReport()
        manifest_rows = sqlite.list_manifest()
        manifest_by_path = {r["file_path"]: r for r in manifest_rows}

        for f in self.scan_files():
            report.files_scanned += 1
            fhash = file_sha256(f)
            entry = manifest_by_path.get(str(f))
            if entry is None:
                # Create manifest entry from file metadata.
                with open(f, "rb") as rf:
                    table = pq.read_table(rf)
                count = table.num_rows
                # Derive partition key from path: market/{venue}/{mtype}/{key}/{tf}/{month}.parquet
                parts = f.relative_to(self.data_dir / "market").parts
                if len(parts) != 5:
                    report.mismatched.append(f"{f}: unexpected path depth")
                    continue
                venue, mtype, key, tf, month_file = parts
                month = month_file.removesuffix(".parquet")
                pkey = partition_key(venue, mtype, key, tf, month)
                # file_version starts at 1 for newly discovered files.
                sqlite.upsert_manifest(pkey, str(f), fhash, 1, count)
                report.created += 1
            elif entry["file_hash"] != fhash:
                report.mismatched.append(f"{f}: hash mismatch (sqlite={entry['file_hash'][:12]}…, file={fhash[:12]}…)")
            else:
                report.verified += 1

        # Orphan check: candle_meta rows whose parquet_path does not exist.
        # (We check existence of the file, not full hash, for speed; hash check is above.)
        # Count distinct parquet_paths in candle_meta that are missing on disk.
        # Access via sqlite connection directly for distinct paths.
        rows = sqlite._conn.execute("SELECT DISTINCT parquet_path FROM candle_meta").fetchall()
        for (p,) in rows:
            if not Path(p).exists():
                report.orphan_sqlite_rows += 1
        return report
