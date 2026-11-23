"""MarketHub: single point of distribution for quotes, candles, indicators & feed health (SPEC §§3-7, Gates A3, A7)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
import uuid

from backend.app.domain.candle import Candle
from backend.app.domain.enums import FeedStatus
from backend.app.domain.feed_health import FeedHealth
from backend.app.domain.instrument import Instrument
from backend.app.domain.quote import Quote
from backend.app.indicators.engine import IndicatorSnapshot, IndicatorSuite, IndicatorSuiteConfig
from backend.app.schemas.ws_models import ResyncRequest, SnapshotMessage, UpdateMessage
from backend.app.services.candle_builder import CandleSeriesBuilder, IngestResult
from backend.app.sessions.calendar import SessionCalendar, SessionCalendarRegistry
from backend.app.storage.parquet_store import ParquetStore
from backend.app.storage.sqlite_store import SqliteStore

UTC = timezone.utc
UpdateKind = Literal["quote", "open_candle", "candle_close", "revision", "feed_health"]


@dataclass
class _PendingItem:
    kind: UpdateKind
    coalesce_key: tuple[str, ...] | None
    quote: Quote | None = None
    candle: Candle | None = None
    feed_health: FeedHealth | None = None


class ClientStream:
    """Per-client WebSocket stream with bounded queue, coalescing, and post-coalesce sequence_id (SPEC §6)."""

    def __init__(self, hub: MarketHub, queue_limit: int = 100) -> None:
        if queue_limit < 1:
            raise ValueError("queue_limit must be >= 1")
        self._hub = hub
        self.queue_limit = queue_limit
        self.stream_epoch: str = f"epoch-{uuid.uuid4().hex[:12]}"
        self._next_seq: int = 1
        self._pending_snapshot: SnapshotMessage | None = None
        self._pending: list[_PendingItem] = []
        self._overflow_resync: bool = False
        self._resync_reason: str | None = None
        self._notify = asyncio.Event()
        self.closed: bool = False

    @property
    def pending_count(self) -> int:
        return len(self._pending) + (1 if (self._pending_snapshot is not None or self._overflow_resync) else 0)

    def _assign_seq(self) -> int:
        seq = self._next_seq
        self._next_seq += 1
        return seq

    def queue_initial_snapshot(self, quotes: list[Quote], candles: list[Candle], feed_health: list[FeedHealth]) -> None:
        self._next_seq = 1
        seq = self._assign_seq()
        self._pending_snapshot = SnapshotMessage(
            stream_epoch=self.stream_epoch,
            sequence_id=seq,
            quotes=quotes,
            candles=candles,
            feed_health=feed_health,
            resync_reason=None,
        )
        self._notify.set()

    def request_resync(self, reason: str = "client_resync") -> None:
        """Rotate stream_epoch, clear stale updates, and schedule a fresh snapshot."""
        self.stream_epoch = f"epoch-{uuid.uuid4().hex[:12]}"
        self._next_seq = 1
        self._pending.clear()
        self._pending_snapshot = None
        self._overflow_resync = True
        self._resync_reason = reason
        self._notify.set()

    def enqueue(self, item: _PendingItem) -> None:
        if self.closed:
            return
        # If already marked for overflow resync, incoming updates will be captured by the fresh snapshot.
        if self._overflow_resync:
            return

        # Coalescing is allowed ONLY for replaceable visual states ("quote" and "open_candle").
        if item.coalesce_key is not None and item.kind in {"quote", "open_candle"}:
            for idx, existing in enumerate(self._pending):
                if existing.kind == item.kind and existing.coalesce_key == item.coalesce_key:
                    self._pending[idx] = item
                    self._notify.set()
                    return

        # Non-coalesceable event or new key: check bounded queue limit.
        if len(self._pending) >= self.queue_limit:
            self.request_resync(reason="queue_overflow")
            return

        self._pending.append(item)
        self._notify.set()

    def pop_next_message(self) -> SnapshotMessage | UpdateMessage | None:
        """Dequeue next message and assign sequence_id AFTER coalescing (immediately before send)."""
        if self._overflow_resync:
            self._overflow_resync = False
            reason = self._resync_reason
            self._resync_reason = None
            self._pending.clear()
            seq = self._assign_seq()
            snap = self._hub.build_snapshot_message(
                stream_epoch=self.stream_epoch,
                sequence_id=seq,
                resync_reason=reason,
            )
            return snap

        if self._pending_snapshot is not None:
            snap = self._pending_snapshot
            self._pending_snapshot = None
            return snap

        if not self._pending:
            self._notify.clear()
            return None

        item = self._pending.pop(0)
        if not self._pending and self._pending_snapshot is None and not self._overflow_resync:
            self._notify.clear()

        seq = self._assign_seq()
        return UpdateMessage(
            stream_epoch=self.stream_epoch,
            sequence_id=seq,
            kind=item.kind,
            quote=item.quote,
            candle=item.candle,
            feed_health=item.feed_health,
        )

    async def wait_next_message(self, timeout: float | None = None) -> SnapshotMessage | UpdateMessage | None:
        msg = self.pop_next_message()
        if msg is not None:
            return msg
        try:
            await asyncio.wait_for(self._notify.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        return self.pop_next_message()


class MarketHub:
    """Central in-memory hub for quotes, candles, indicators, storage flushing, and WS clients."""

    def __init__(
        self,
        instruments: list[Instrument],
        calendars: SessionCalendarRegistry,
        *,
        sqlite_store: SqliteStore | None = None,
        parquet_store: ParquetStore | None = None,
        ws_client_queue_limit: int = 100,
        stale_threshold_ms: int = 30_000,
        indicator_config: IndicatorSuiteConfig | None = None,
    ) -> None:
        self.instruments: dict[tuple[str, str], Instrument] = {}
        self.calendars = calendars
        self.sqlite_store = sqlite_store
        self.parquet_store = parquet_store
        self.ws_client_queue_limit = ws_client_queue_limit
        self.stale_threshold_ms = stale_threshold_ms
        self.indicator_config = indicator_config or IndicatorSuiteConfig()

        self._quotes: dict[tuple[str, str], Quote] = {}
        self._builders: dict[tuple[str, str, str], CandleSeriesBuilder] = {}
        self._indicators: dict[tuple[str, str, str], IndicatorSuite] = {}
        self._feed_health: dict[tuple[str, str | None], FeedHealth] = {}
        self._unflushed_closed: dict[tuple[str, str, str, str], list[Candle]] = {}
        self._clients: list[ClientStream] = []

        for inst in instruments:
            key = (inst.source_id, inst.instrument_id)
            self.instruments[key] = inst
            cal = self._resolve_calendar(inst.session_calendar_id)

            # Venue-level FeedHealth (instrument_id=None).
            venue_fh_key = (inst.source_id, None)
            if venue_fh_key not in self._feed_health:
                self._feed_health[venue_fh_key] = FeedHealth(
                    source_id=inst.source_id,
                    instrument_id=None,
                    status=FeedStatus.OFFLINE,
                )

            # Instrument-level FeedHealth.
            self._feed_health[(inst.source_id, inst.instrument_id)] = FeedHealth(
                source_id=inst.source_id,
                instrument_id=inst.instrument_id,
                status=FeedStatus.OFFLINE,
            )

            for tf in inst.supported_timeframes:
                series_key = (inst.source_id, inst.instrument_id, tf)
                self._builders[series_key] = CandleSeriesBuilder(
                    inst.source_id,
                    inst.instrument_id,
                    tf,
                    price_basis=inst.price_basis,
                    volume_type=inst.volume_type,
                    calendar=cal,
                )
                self._indicators[series_key] = IndicatorSuite(
                    inst.source_id,
                    inst.instrument_id,
                    tf,
                    config=self.indicator_config,
                )

    def _resolve_calendar(self, calendar_id: str) -> SessionCalendar | None:
        try:
            return self.calendars.get(calendar_id)
        except KeyError:
            return None

    # ------------------------------------------------------------------
    # Client subscription & snapshot management
    # ------------------------------------------------------------------

    def subscribe_client(self, queue_limit: int | None = None) -> ClientStream:
        """Create and register a ClientStream with an atomic initial snapshot (no lost updates)."""
        limit = queue_limit if queue_limit is not None else self.ws_client_queue_limit
        stream = ClientStream(self, queue_limit=limit)
        quotes, candles, fhs = self._collect_snapshot_state()
        stream.queue_initial_snapshot(quotes, candles, fhs)
        self._clients.append(stream)
        return stream

    def unsubscribe_client(self, stream: ClientStream) -> None:
        stream.closed = True
        if stream in self._clients:
            self._clients.remove(stream)

    def handle_client_resync(self, stream: ClientStream, req: ResyncRequest) -> None:
        reason = req.reason or "client_resync_request"
        stream.request_resync(reason=reason)

    def _collect_snapshot_state(self) -> tuple[list[Quote], list[Candle], list[FeedHealth]]:
        quotes = [self._quotes[k] for k in sorted(self._quotes)]
        candles: list[Candle] = []
        for s_key in sorted(self._builders):
            builder = self._builders[s_key]
            candles.extend(builder.closed_candles())
            if builder.open_candle is not None:
                candles.append(builder.open_candle)
        fhs = [self._feed_health[k] for k in sorted(self._feed_health, key=lambda x: (x[0], x[1] or ""))]
        return quotes, candles, fhs

    def build_snapshot_message(
        self,
        *,
        stream_epoch: str,
        sequence_id: int,
        resync_reason: str | None = None,
    ) -> SnapshotMessage:
        quotes, candles, fhs = self._collect_snapshot_state()
        return SnapshotMessage(
            stream_epoch=stream_epoch,
            sequence_id=sequence_id,
            quotes=quotes,
            candles=candles,
            feed_health=fhs,
            resync_reason=resync_reason,
        )

    def _broadcast(self, item: _PendingItem) -> None:
        for client in list(self._clients):
            if not client.closed:
                client.enqueue(item)

    # ------------------------------------------------------------------
    # Quote, Candle, Storage, and Indicator Ingestion
    # ------------------------------------------------------------------

    def publish_quote(self, quote: Quote) -> None:
        key = (quote.source_id, quote.instrument_id)
        self._quotes[key] = quote
        self._broadcast(
            _PendingItem(
                kind="quote",
                coalesce_key=("quote", quote.source_id, quote.instrument_id),
                quote=quote,
            )
        )

    def publish_candle(
        self,
        candle: Candle,
        *,
        gap_confirmed_by_source: bool = False,
        flush_immediately: bool = False,
    ) -> IngestResult:
        series_key = (candle.source_id, candle.instrument_id, candle.timeframe)
        if series_key not in self._builders:
            inst = self.instruments.get((candle.source_id, candle.instrument_id))
            cal = self._resolve_calendar(inst.session_calendar_id) if inst else None
            self._builders[series_key] = CandleSeriesBuilder(
                candle.source_id,
                candle.instrument_id,
                candle.timeframe,
                price_basis=candle.price_basis,
                volume_type=candle.volume_type,
                calendar=cal,
            )
            self._indicators[series_key] = IndicatorSuite(
                candle.source_id,
                candle.instrument_id,
                candle.timeframe,
                config=self.indicator_config,
            )

        builder = self._builders[series_key]
        ind_suite = self._indicators[series_key]
        res = builder.ingest_candle(candle, gap_confirmed_by_source=gap_confirmed_by_source)

        if res.action == "open_updated":
            self._broadcast(
                _PendingItem(
                    kind="open_candle",
                    coalesce_key=("open_candle", candle.source_id, candle.instrument_id, candle.timeframe),
                    candle=res.candle,
                )
            )
            return res

        if res.action == "duplicate":
            return res

        if res.action == "closed":
            # If out-of-order fill or normal append: if candle is at the tail, incremental update; else full recompute.
            closed_list = builder.closed_candles()
            if closed_list and closed_list[-1].open_time_utc == res.candle.open_time_utc and len(closed_list) == len(ind_suite.ema_fast_series) + 1:
                ind_suite.update_closed(res.candle)
            else:
                ind_suite.recompute_from_closed(closed_list)

            if res.gap_event is not None:
                self._increment_gap_counter(candle.source_id, candle.instrument_id)

            self._queue_for_storage(res.candle)
            if flush_immediately:
                self.flush_closed_candles()

            self._broadcast(
                _PendingItem(
                    kind="candle_close",
                    coalesce_key=None,
                    candle=res.candle,
                )
            )
            return res

        if res.action == "revised":
            # Recompute indicators across the series on revision of a closed candle.
            ind_suite.recompute_from_closed(builder.closed_candles())
            self._queue_for_storage(res.candle)
            if flush_immediately:
                self.flush_closed_candles()

            self._broadcast(
                _PendingItem(
                    kind="revision",
                    coalesce_key=None,
                    candle=res.candle,
                )
            )
            return res

        return res

    def _queue_for_storage(self, candle: Candle) -> None:
        if not candle.is_closed:
            return
        month = candle.open_time_utc.strftime("%Y-%m")
        part_key = (candle.source_id, candle.instrument_id, candle.timeframe, month)
        self._unflushed_closed.setdefault(part_key, []).append(candle)

    def flush_closed_candles(self) -> int:
        """Persist buffered closed candles to ParquetStore + SqliteStore."""
        if not self._unflushed_closed:
            return 0
        if self.parquet_store is None:
            self._unflushed_closed.clear()
            return 0

        flushed_count = 0
        pending = dict(self._unflushed_closed)
        self._unflushed_closed.clear()

        for (source_id, instrument_id, _tf, _month), batch in pending.items():
            inst = self.instruments.get((source_id, instrument_id))
            venue_slug = inst.venue_id if inst else source_id
            market_type = inst.market_type.value if inst else "spot"
            storage_key = inst.instrument_storage_key if inst else instrument_id

            p_res = self.parquet_store.write_batch(batch, venue_slug, market_type, storage_key)
            flushed_count += len(batch)
            if self.sqlite_store is not None:
                existing_manifest = self.sqlite_store.get_manifest(p_res["partition_key"], p_res["file_path"])
                next_ver = (int(existing_manifest["file_version"]) + 1) if existing_manifest else 1
                self.sqlite_store.upsert_manifest(
                    p_res["partition_key"],
                    p_res["file_path"],
                    p_res["file_hash"],
                    next_ver,
                    p_res["candle_count"],
                )
                for c in batch:
                    self.sqlite_store.upsert_candle_meta(c, p_res["file_path"], p_res["file_hash"])

        return flushed_count

    # ------------------------------------------------------------------
    # Feed Health State Machine (SPEC §4, Gate A7)
    # ------------------------------------------------------------------

    def _increment_gap_counter(self, source_id: str, instrument_id: str) -> None:
        for fh_key in [(source_id, None), (source_id, instrument_id)]:
            cur = self._feed_health.get(fh_key)
            if cur is not None:
                updated = cur.model_copy(update={"gaps_detected": cur.gaps_detected + 1})
                self._feed_health[fh_key] = updated

    def update_feed_health(
        self,
        source_id: str,
        instrument_id: str | None = None,
        *,
        connected: bool,
        now_utc: datetime | None = None,
        last_quote_age_ms: int | None = None,
        last_heartbeat_age_ms: int | None = None,
        reconnect_count: int | None = None,
        clock_skew_ms: int | None = None,
        degraded: bool = False,
        subscription_state: str | None = None,
    ) -> FeedHealth:
        """Evaluate and transition FeedHealth state machine deterministically (SPEC §4, Gate A7)."""
        now = now_utc or datetime.now(UTC)
        fh_key = (source_id, instrument_id)
        prev = self._feed_health.get(fh_key) or FeedHealth(
            source_id=source_id,
            instrument_id=instrument_id,
            status=FeedStatus.OFFLINE,
        )

        # Check confirmed session calendar for MARKET_CLOSED (never emit without confirmed calendar).
        market_closed = False
        if instrument_id is not None:
            inst = self.instruments.get((source_id, instrument_id))
            if inst is not None:
                cal = self._resolve_calendar(inst.session_calendar_id)
                if cal is not None and cal.is_confirmed_closed(now):
                    market_closed = True
        else:
            # Venue-level: closed only if all instruments of this source have a confirmed closed calendar now.
            venue_insts = [i for (s, _), i in self.instruments.items() if s == source_id]
            if venue_insts and all(
                (cal := self._resolve_calendar(i.session_calendar_id)) is not None and cal.is_confirmed_closed(now)
                for i in venue_insts
            ):
                market_closed = True

        if market_closed:
            next_status = FeedStatus.MARKET_CLOSED
        elif not connected:
            next_status = FeedStatus.OFFLINE
        elif last_quote_age_ms is not None and last_quote_age_ms > self.stale_threshold_ms:
            next_status = FeedStatus.STALE
        elif degraded or (clock_skew_ms is not None and abs(clock_skew_ms) > 2_000):
            next_status = FeedStatus.DEGRADED
        else:
            next_status = FeedStatus.CONNECTED

        updated = prev.model_copy(
            update={
                "status": next_status,
                "last_quote_age_ms": last_quote_age_ms if last_quote_age_ms is not None else prev.last_quote_age_ms,
                "last_heartbeat_age_ms": (
                    last_heartbeat_age_ms if last_heartbeat_age_ms is not None else prev.last_heartbeat_age_ms
                ),
                "reconnect_count": reconnect_count if reconnect_count is not None else prev.reconnect_count,
                "clock_skew_ms": clock_skew_ms if clock_skew_ms is not None else prev.clock_skew_ms,
                "subscription_state": (
                    subscription_state if subscription_state is not None else prev.subscription_state
                ),
            }
        )
        self._feed_health[fh_key] = updated
        if updated != prev:
            self._broadcast(_PendingItem(kind="feed_health", coalesce_key=None, feed_health=updated))
        return updated

    # ------------------------------------------------------------------
    # Read-Only Query Methods for HTTP / WS
    # ------------------------------------------------------------------

    def get_quotes(self) -> list[Quote]:
        return [self._quotes[k] for k in sorted(self._quotes)]

    def get_candles(
        self,
        source_id: str,
        instrument_id: str,
        timeframe: str,
        *,
        limit: int | None = None,
    ) -> tuple[list[Candle], Candle | None]:
        builder = self._builders.get((source_id, instrument_id, timeframe))
        if builder is None:
            return [], None
        closed = builder.closed_candles()
        if limit is not None and limit > 0:
            closed = closed[-limit:]
        return closed, builder.open_candle

    def get_indicators(
        self,
        source_id: str,
        instrument_id: str,
        timeframe: str,
        *,
        include_preview: bool = False,
    ) -> IndicatorSnapshot:
        series_key = (source_id, instrument_id, timeframe)
        suite = self._indicators.get(series_key)
        builder = self._builders.get(series_key)
        if suite is None:
            suite = IndicatorSuite(source_id, instrument_id, timeframe, config=self.indicator_config)
        open_c = builder.open_candle if builder is not None else None
        return suite.snapshot(open_candle=open_c, include_preview=include_preview)

    def get_feed_health(self) -> list[FeedHealth]:
        return [self._feed_health[k] for k in sorted(self._feed_health, key=lambda x: (x[0], x[1] or ""))]
