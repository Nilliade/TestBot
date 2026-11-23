"""Reconnect, history recovery, WS bounded queue/coalescing & FeedHealth tests (ACCEPTANCE A3, A7)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config.settings import load_instruments
from backend.app.domain.candle import Candle
from backend.app.domain.enums import FeedStatus
from backend.app.domain.quote import Quote
from backend.app.main import app
from backend.app.schemas.ws_models import ResyncRequest, SnapshotMessage, UpdateMessage
from backend.app.services.feed_recovery import UpstreamFeedCoordinator
from backend.app.services.market_hub import MarketHub
from backend.app.sessions.calendar import load_session_calendars
from backend.app.storage.parquet_store import ParquetStore
from backend.app.storage.sqlite_store import SqliteStore

UTC = timezone.utc
T0 = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)  # Monday


def _candle(
    hour_offset: int,
    *,
    source_id: str = "okx-spot",
    instrument_id: str = "BTC-USDT",
    close_p: str = "105",
    is_closed: bool = True,
    revision: int = 1,
    flags: set[str] | None = None,
) -> Candle:
    open_t = T0 + timedelta(hours=hour_offset)
    return Candle(
        source_id=source_id,
        instrument_id=instrument_id,
        timeframe="1h",
        open_time_utc=open_t,
        close_time_utc=open_t + timedelta(hours=1),
        open="100",
        high="110",
        low="90",
        close=close_p,
        volume="10",
        volume_type="exchange",
        price_basis="last",
        is_closed=is_closed,
        quality_flags=flags or set(),
        revision=revision,
        received_at_utc=open_t + timedelta(hours=1),
        updated_at_utc=open_t + timedelta(hours=1),
    )


def _quote(seq: int, price: str = "100.5") -> Quote:
    ts = T0 + timedelta(seconds=seq)
    return Quote(
        source_id="okx-spot",
        instrument_id="BTC-USDT",
        bid=Decimal(price) - Decimal("0.5"),
        ask=Decimal(price) + Decimal("0.5"),
        last=Decimal(price),
        exchange_ts_utc=ts,
        received_ts_utc=ts,
    )


class FakeUpstreamExchange:
    """Deterministic fake upstream server for Gate A3 tests."""

    def __init__(self, source_id: str = "okx-spot", *, authoritative: bool = True) -> None:
        self.source_id = source_id
        self.authoritative = authoritative
        self.history: list[Candle] = []
        self.fail_fetch: bool = False

    async def fetch_history_candles(
        self,
        instrument_id: str,
        timeframe: str,
        since_utc: datetime | None,
        until_utc: datetime | None = None,
    ) -> tuple[list[Candle], bool]:
        if self.fail_fetch:
            raise ConnectionError("simulated history endpoint failure")
        out = [
            c
            for c in self.history
            if c.instrument_id == instrument_id
            and c.timeframe == timeframe
            and (since_utc is None or c.open_time_utc >= since_utc)
            and (until_utc is None or c.close_time_utc <= until_utc)
        ]
        return out, self.authoritative


def _make_hub(tmp_path: Path | None = None, queue_limit: int = 100) -> MarketHub:
    instruments = load_instruments()
    calendars = load_session_calendars()
    sqlite_store = SqliteStore(tmp_path / "market.db") if tmp_path else None
    parquet_store = ParquetStore(tmp_path / "data") if tmp_path else None
    return MarketHub(
        instruments=instruments,
        calendars=calendars,
        sqlite_store=sqlite_store,
        parquet_store=parquet_store,
        ws_client_queue_limit=queue_limit,
    )


def test_reconnect_history_recovery_without_duplicates_and_gap_preservation(tmp_path: Path):
    async def _run():
        hub = _make_hub(tmp_path)
        try:
            fake = FakeUpstreamExchange("okx-spot", authoritative=True)
            # Initial bars 0, 1, 2
            fake.history = [_candle(0), _candle(1), _candle(2)]
            coord = UpstreamFeedCoordinator(hub, fake)

            added_init = await coord.connect_and_backfill("BTC-USDT", "1h", now_utc=T0 + timedelta(hours=3))
            assert added_init == 3

            # Simulate stream break
            coord.on_disconnect("BTC-USDT", now_utc=T0 + timedelta(hours=3, minutes=5))
            fhs = {(f.source_id, f.instrument_id): f for f in hub.get_feed_health()}
            assert fhs[("okx-spot", "BTC-USDT")].status == FeedStatus.OFFLINE
            assert fhs[("okx-spot", None)].status == FeedStatus.OFFLINE

            # While disconnected: bars 3 and 4 close, bar 5 is missing on exchange, bar 6 closes.
            # Note: fake.history still includes bar 2 -> overlap on reconnect must NOT create a duplicate!
            fake.history.extend([_candle(3), _candle(4), _candle(6)])

            added_rec = await coord.reconnect_and_recover("BTC-USDT", "1h", now_utc=T0 + timedelta(hours=7))
            assert added_rec == 3  # Only bars 3, 4, 6 added; bar 2 deduplicated!
            assert coord.reconnect_count == 1

            closed, _ = hub.get_candles("okx-spot", "BTC-USDT", "1h")
            assert [c.open_time_utc for c in closed] == [
                T0 + timedelta(hours=0),
                T0 + timedelta(hours=1),
                T0 + timedelta(hours=2),
                T0 + timedelta(hours=3),
                T0 + timedelta(hours=4),
                T0 + timedelta(hours=6),
            ]
            # Bar at hour 5 was NOT interpolated/synthesized, and bar at hour 6 is flagged CONFIRMED_GAP!
            assert "CONFIRMED_GAP" in closed[-1].quality_flags

            fhs2 = {(f.source_id, f.instrument_id): f for f in hub.get_feed_health()}
            assert fhs2[("okx-spot", "BTC-USDT")].status == FeedStatus.CONNECTED
            assert fhs2[("okx-spot", "BTC-USDT")].reconnect_count == 1
            assert fhs2[("okx-spot", "BTC-USDT")].gaps_detected == 1
        finally:
            if hub.sqlite_store:
                hub.sqlite_store.close()

    asyncio.run(_run())


def test_unconfirmed_gap_marks_possible_gap_and_degraded():
    async def _run():
        hub = _make_hub()
        fake = FakeUpstreamExchange("okx-spot", authoritative=False)
        fake.history = [_candle(0), _candle(2)]  # Bar 1 missing, source is NOT authoritative
        coord = UpstreamFeedCoordinator(hub, fake)

        await coord.connect_and_backfill("BTC-USDT", "1h", now_utc=T0 + timedelta(hours=3))
        closed, _ = hub.get_candles("okx-spot", "BTC-USDT", "1h")
        assert len(closed) == 2
        assert "POSSIBLE_GAP" in closed[-1].quality_flags
        assert "CONFIRMED_GAP" not in closed[-1].quality_flags

        fhs = {(f.source_id, f.instrument_id): f for f in hub.get_feed_health()}
        assert fhs[("okx-spot", "BTC-USDT")].status == FeedStatus.DEGRADED

    asyncio.run(_run())


def test_confirmed_forex_weekend_is_not_counted_as_gap_and_emits_market_closed():
    instruments = load_instruments()
    calendars = load_session_calendars()

    # Bind EURUSD to the confirmed Forex 24x5 calendar for this test.
    updated_insts = []
    for inst in instruments:
        if inst.instrument_id == "EURUSD":
            updated_insts.append(inst.model_copy(update={"session_calendar_id": "forex-24x5-confirmed"}))
        else:
            updated_insts.append(inst)

    hub = MarketHub(instruments=updated_insts, calendars=calendars)

    # Friday 2026-01-09 21:00 UTC is Friday 16:00 America/New_York (last 1h bar before 17:00 NY close).
    fri_last_open = datetime(2026, 1, 9, 21, 0, tzinfo=UTC)
    # Sunday 2026-01-11 22:00 UTC is Sunday 17:00 America/New_York (first 1h bar when market reopens).
    sun_first_open = datetime(2026, 1, 11, 22, 0, tzinfo=UTC)
    sat_mid = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)

    c_fri = Candle(
        source_id="mt5-demo",
        instrument_id="EURUSD",
        timeframe="1h",
        open_time_utc=fri_last_open,
        close_time_utc=fri_last_open + timedelta(hours=1),
        open="1.0500",
        high="1.0510",
        low="1.0490",
        close="1.0505",
        volume="100",
        volume_type="tick",
        price_basis="bid",
        is_closed=True,
        quality_flags=set(),
        revision=1,
        received_at_utc=fri_last_open + timedelta(hours=1),
        updated_at_utc=fri_last_open + timedelta(hours=1),
    )
    c_sun = c_fri.model_copy(
        update={
            "open_time_utc": sun_first_open,
            "close_time_utc": sun_first_open + timedelta(hours=1),
            "received_at_utc": sun_first_open + timedelta(hours=1),
            "updated_at_utc": sun_first_open + timedelta(hours=1),
        }
    )

    hub.publish_candle(c_fri, gap_confirmed_by_source=True)
    res_sun = hub.publish_candle(c_sun, gap_confirmed_by_source=True)

    # Confirmed weekend close must NOT be flagged as a gap!
    assert res_sun.gap_event is None
    assert "CONFIRMED_GAP" not in res_sun.candle.quality_flags
    assert "POSSIBLE_GAP" not in res_sun.candle.quality_flags

    # During Saturday, FeedHealth on confirmed calendar must be MARKET_CLOSED.
    fh_sat = hub.update_feed_health("mt5-demo", "EURUSD", connected=True, now_utc=sat_mid)
    assert fh_sat.status == FeedStatus.MARKET_CLOSED

    # By contrast, default unconfirmed forex-24x5 must NEVER emit MARKET_CLOSED.
    hub_unconfirmed = _make_hub()
    fh_unconfirmed = hub_unconfirmed.update_feed_health("mt5-demo", "EURUSD", connected=True, now_utc=sat_mid)
    assert fh_unconfirmed.status != FeedStatus.MARKET_CLOSED


def test_feed_health_states_connected_stale_degraded_offline():
    hub = _make_hub()
    # 1. CONNECTED
    fh = hub.update_feed_health("okx-spot", "BTC-USDT", connected=True, last_quote_age_ms=500, clock_skew_ms=10)
    assert fh.status == FeedStatus.CONNECTED

    # 2. DEGRADED (high clock skew or degraded flag)
    fh = hub.update_feed_health("okx-spot", "BTC-USDT", connected=True, last_quote_age_ms=500, clock_skew_ms=3500)
    assert fh.status == FeedStatus.DEGRADED

    # 3. STALE (last_quote_age_ms > stale_threshold_ms=30_000)
    fh = hub.update_feed_health("okx-spot", "BTC-USDT", connected=True, last_quote_age_ms=45_000, clock_skew_ms=10)
    assert fh.status == FeedStatus.STALE

    # 4. OFFLINE
    fh = hub.update_feed_health("okx-spot", "BTC-USDT", connected=False)
    assert fh.status == FeedStatus.OFFLINE


def test_ws_stream_coalescing_sequence_ids_and_queue_overflow_resync():
    hub = _make_hub(queue_limit=3)
    stream = hub.subscribe_client(queue_limit=3)

    # First message is always the atomic initial SnapshotMessage with sequence_id=1.
    first_msg = stream.pop_next_message()
    assert isinstance(first_msg, SnapshotMessage)
    assert first_msg.sequence_id == 1
    initial_epoch = first_msg.stream_epoch

    # Publish 10 quotes for the same instrument before client reads -> must coalesce into 1 item!
    for i in range(1, 11):
        hub.publish_quote(_quote(i, price=f"{100 + i}.0"))

    assert stream.pending_count == 1
    q_msg = stream.pop_next_message()
    assert isinstance(q_msg, UpdateMessage)
    assert q_msg.kind == "quote"
    assert q_msg.sequence_id == 2  # Assigned AFTER coalescing!
    assert q_msg.stream_epoch == initial_epoch
    assert str(q_msg.quote.last) == "110.0"

    # Now publish 4 non-coalesceable closed candles into a queue of limit 3 -> triggers overflow resync!
    for h in range(4):
        hub.publish_candle(_candle(h))

    overflow_msg = stream.pop_next_message()
    assert isinstance(overflow_msg, SnapshotMessage)
    assert overflow_msg.resync_reason == "queue_overflow"
    assert overflow_msg.stream_epoch != initial_epoch
    assert overflow_msg.sequence_id == 1
    # All 4 closed candles are present in the resync snapshot (none lost!).
    btc_candles = [c for c in overflow_msg.candles if c.instrument_id == "BTC-USDT" and c.is_closed]
    assert len(btc_candles) == 4


def test_closed_candle_revision_recomputes_indicators_and_emits_revision():
    hub = _make_hub()
    stream = hub.subscribe_client()
    _ = stream.pop_next_message()  # initial snapshot

    for h in range(20):
        hub.publish_candle(_candle(h, close_p="100"))
    while stream.pop_next_message() is not None:
        pass

    ind_before = hub.get_indicators("okx-spot", "BTC-USDT", "1h")
    assert ind_before.ema_fast[-1].value == 100.0

    # Revise bar 19 with close="110"
    res = hub.publish_candle(_candle(19, close_p="110"))
    assert res.action == "revised"
    assert res.candle.revision == 2
    assert "REVISED" in res.candle.quality_flags

    rev_msg = stream.pop_next_message()
    assert isinstance(rev_msg, UpdateMessage)
    assert rev_msg.kind == "revision"
    assert rev_msg.candle.revision == 2

    ind_after = hub.get_indicators("okx-spot", "BTC-USDT", "1h")
    assert ind_after.ema_fast[-1].value == 100.5  # SMA of nineteen 100s and one 110 = 2010 / 20 = 100.5


def test_fastapi_ws_endpoint_and_resync_request():
    with TestClient(app) as client:
        with client.websocket_connect("/ws/stream") as ws:
            snap_raw = ws.receive_json()
            snap = SnapshotMessage.model_validate(snap_raw)
            assert snap.type == "snapshot"
            assert snap.sequence_id == 1
            first_epoch = snap.stream_epoch

            # Client sends ResyncRequest -> server responds with a fresh SnapshotMessage on a new epoch.
            req = ResyncRequest(stream_epoch=first_epoch, last_sequence_id=1, reason="seq_gap_detected")
            ws.send_json(req.model_dump(mode="json"))

            resync_raw = ws.receive_json()
            resync_snap = SnapshotMessage.model_validate(resync_raw)
            assert resync_snap.type == "snapshot"
            assert resync_snap.sequence_id == 1
            assert resync_snap.stream_epoch != first_epoch
            assert resync_snap.resync_reason == "seq_gap_detected"

        # Also check the new Step 1.2 read-only HTTP GET endpoints.
        assert client.get("/api/v1/quotes").status_code == 200
        assert client.get("/api/v1/feed-health").status_code == 200
        r_c = client.get("/api/v1/candles?source_id=okx-spot&instrument_id=BTC-USDT&timeframe=1h")
        assert r_c.status_code == 200
        r_i = client.get("/api/v1/indicators?source_id=okx-spot&instrument_id=BTC-USDT&timeframe=1h")
        assert r_i.status_code == 200
