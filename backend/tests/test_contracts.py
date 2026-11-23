"""Contract tests (ACCEPTANCE A2): Instrument/Quote/Candle/FeedHealth validation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.app.config.settings import load_instruments
from backend.app.domain.candle import Candle
from backend.app.domain.enums import FeedStatus, MarketType, PriceBasis, VolumeType
from backend.app.domain.feed_health import FeedHealth
from backend.app.domain.instrument import Instrument, InstrumentSpecs
from backend.app.domain.quote import Quote
from backend.app.schemas.ws_models import ResyncRequest, SnapshotMessage, UpdateMessage

UTC = timezone.utc


def _valid_instrument_kwargs() -> dict:
    return {
        "source_id": "okx-spot",
        "instrument_id": "BTC-USDT",
        "instrument_storage_key": "BTC-USDT",
        "alias": "BTC/USDT",
        "venue_id": "okx",
        "account_or_feed_id": "okx-public-spot",
        "provider_symbol": "BTC-USDT",
        "market_type": "spot",
        "base_ccy": "BTC",
        "quote_ccy": "USDT",
        "settlement_ccy": "USDT",
        "price_basis": "last",
        "volume_type": "exchange",
        "supported_timeframes": ["1m", "1h"],
        "specs": {"min_qty": "0.00001", "price_step": "0.1"},
        "supports_short": False,
        "session_calendar_id": "crypto-24x7",
    }


def _valid_candle_kwargs() -> dict:
    open_t = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    return {
        "source_id": "okx-spot",
        "instrument_id": "BTC-USDT",
        "timeframe": "1h",
        "open_time_utc": open_t,
        "close_time_utc": open_t + timedelta(hours=1),
        "open": "100",
        "high": "110",
        "low": "90",
        "close": "105",
        "volume": "10",
        "volume_type": "exchange",
        "price_basis": "last",
        "is_closed": True,
        "quality_flags": set(),
        "revision": 1,
        "received_at_utc": open_t + timedelta(hours=1, minutes=1),
        "updated_at_utc": open_t + timedelta(hours=1, minutes=1),
    }


def test_valid_instrument():
    inst = Instrument(**_valid_instrument_kwargs())
    assert inst.instrument_storage_key == "BTC-USDT"
    assert inst.specs.min_qty == Decimal("0.00001")


@pytest.mark.parametrize("bad_key", ["BTC/USDT", "BTC:USDT", "BTC USDT", "../BTC", "..", ".", "BTC\\USDT", ""])
def test_instrument_storage_key_rejects_traversal_and_separators(bad_key):
    kw = _valid_instrument_kwargs()
    kw["instrument_storage_key"] = bad_key
    with pytest.raises(ValidationError):
        Instrument(**kw)


def test_instrument_rejects_empty_and_unsupported_timeframes():
    kw = _valid_instrument_kwargs()
    kw["supported_timeframes"] = []
    with pytest.raises(ValidationError):
        Instrument(**kw)
    kw["supported_timeframes"] = ["1m", "2m"]
    with pytest.raises(ValidationError):
        Instrument(**kw)
    kw["supported_timeframes"] = ["1m", "1m"]
    with pytest.raises(ValidationError):
        Instrument(**kw)


def test_instrument_rejects_float_decimal():
    kw = _valid_instrument_kwargs()
    kw["specs"] = {"min_qty": 0.00001}  # float -> forbidden
    with pytest.raises(ValidationError):
        Instrument(**kw)


def test_instrument_rejects_wrong_schema_version():
    kw = _valid_instrument_kwargs()
    kw["schema_version"] = "2.0"
    with pytest.raises(ValidationError):
        Instrument(**kw)


def test_quote_spread_bps_valid():
    q = Quote(
        source_id="okx-spot",
        instrument_id="BTC-USDT",
        bid="100",
        ask="101",
        last="100.5",
        received_ts_utc=datetime(2026, 1, 1, tzinfo=UTC),
    )
    # mid=100.5, spread=1 -> 1/100.5*10000
    assert q.mid == Decimal("100.5")
    assert q.spread_bps is not None
    assert abs(float(q.spread_bps) - (1 / 100.5 * 10000)) < 1e-6


def test_quote_spread_bps_null_on_crossed_or_missing():
    crossed = Quote(
        source_id="okx-spot",
        instrument_id="BTC-USDT",
        bid="101",
        ask="100",
        received_ts_utc=datetime(2026, 1, 1, tzinfo=UTC),
        quality_flags={"CROSSED"},
    )
    assert crossed.spread_bps is None
    assert crossed.mid is None
    missing = Quote(
        source_id="okx-spot",
        instrument_id="BTC-USDT",
        bid="100",
        ask=None,
        received_ts_utc=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert missing.spread_bps is None


def test_quote_rejects_naive_and_non_utc_datetimes():
    with pytest.raises(ValidationError):
        Quote(source_id="s", instrument_id="i", received_ts_utc=datetime(2026, 1, 1))  # naive
    with pytest.raises(ValidationError):
        Quote(
            source_id="s",
            instrument_id="i",
            received_ts_utc=datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=3))),
        )


def test_quote_rejects_non_positive_prices():
    with pytest.raises(ValidationError):
        Quote(source_id="s", instrument_id="i", bid="0", received_ts_utc=datetime(2026, 1, 1, tzinfo=UTC))


def test_candle_valid_and_primary_key():
    c = Candle(**_valid_candle_kwargs())
    assert c.primary_key == ("okx-spot", "BTC-USDT", "1h", c.open_time_utc)


def test_candle_interval_semantics_open_close():
    kw = _valid_candle_kwargs()
    kw["close_time_utc"] = kw["open_time_utc"]  # not > open
    with pytest.raises(ValidationError):
        Candle(**kw)
    kw = _valid_candle_kwargs()
    kw["close_time_utc"] = kw["open_time_utc"] - timedelta(hours=1)
    with pytest.raises(ValidationError):
        Candle(**kw)


def test_candle_ohlc_consistency():
    kw = _valid_candle_kwargs()
    kw["high"] = "95"  # < close 105
    with pytest.raises(ValidationError):
        Candle(**kw)
    kw = _valid_candle_kwargs()
    kw["low"] = "102"  # > open 100
    with pytest.raises(ValidationError):
        Candle(**kw)
    kw = _valid_candle_kwargs()
    kw["high"], kw["low"] = "90", "110"
    with pytest.raises(ValidationError):
        Candle(**kw)


def test_candle_nullable_volume_and_revision():
    kw = _valid_candle_kwargs()
    kw["volume"] = None
    c = Candle(**kw)
    assert c.volume is None
    kw["revision"] = 0
    with pytest.raises(ValidationError):
        Candle(**kw)


def test_candle_gap_flags_distinct_no_interpolation():
    # CONFIRMED_GAP vs POSSIBLE_GAP are distinct flags; contracts accept both
    # but storage never synthesizes zero candles (verified in parquet tests).
    kw = _valid_candle_kwargs()
    kw["quality_flags"] = {"CONFIRMED_GAP"}
    assert Candle(**kw).quality_flags == {"CONFIRMED_GAP"}
    kw["quality_flags"] = {"POSSIBLE_GAP"}
    assert Candle(**kw).quality_flags == {"POSSIBLE_GAP"}
    kw["quality_flags"] = {"BOGUS_FLAG"}
    with pytest.raises(ValidationError):
        Candle(**kw)


def test_feed_health_statuses():
    for status in FeedStatus:
        fh = FeedHealth(source_id="okx-spot", status=status)
        assert fh.status == status
    with pytest.raises(ValidationError):
        FeedHealth(source_id="okx-spot", status="BOGUS")
    with pytest.raises(ValidationError):
        FeedHealth(source_id="okx-spot", status=FeedStatus.CONNECTED, reconnect_count=-1)


def test_default_catalog_loads_and_is_unique():
    instruments = load_instruments()
    assert len(instruments) == 7
    okx = [i for i in instruments if i.venue_id == "okx"]
    mt5 = [i for i in instruments if i.venue_id == "mt5"]
    assert len(okx) == 4 and len(mt5) == 3
    ids = {(i.source_id, i.instrument_id) for i in instruments}
    assert len(ids) == 7
    for inst in instruments:
        # Every catalog entry must pass full validation (already done by loader).
        assert inst.supported_timeframes


def test_ws_models_validate():
    snap = SnapshotMessage(stream_epoch="e1", sequence_id=1, quotes=[], candles=[], feed_health=[])
    assert snap.type == "snapshot"
    upd = UpdateMessage(stream_epoch="e1", sequence_id=2, kind="quote", quote=None)
    assert upd.kind == "quote"
    req = ResyncRequest(stream_epoch="e1", last_sequence_id=1)
    assert req.type == "resync_request"
    with pytest.raises(ValidationError):
        SnapshotMessage(stream_epoch="e1", sequence_id=1, type="bogus")


def test_generated_ws_json_schemas_exist_and_match():
    import json

    from backend.app.schemas.generate_ws_schemas import OUT_DIR

    expected_models = {
        "snapshot.schema.json": SnapshotMessage,
        "update.schema.json": UpdateMessage,
        "resync_request.schema.json": ResyncRequest,
    }
    for filename, model in expected_models.items():
        path = OUT_DIR / filename
        assert path.exists(), f"missing generated schema: {path}"
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        expected = model.model_json_schema()
        assert on_disk == expected


def test_offline_app_starts_and_serves_readonly_endpoints():
    from fastapi.testclient import TestClient

    from backend.app.main import app

    with TestClient(app) as client:
        r_health = client.get("/healthz")
        assert r_health.status_code == 200
        body = r_health.json()
        assert body["status"] == "ok"
        assert body["network"] == "offline"

        r_inst = client.get("/api/v1/instruments")
        assert r_inst.status_code == 200
        inst_body = r_inst.json()
        assert len(inst_body["instruments"]) == 7


def test_preflight_offline_never_fakes_network_or_mt5():
    from backend.app.tools.preflight_check import run_preflight

    reports = {r.source: r for r in run_preflight(offline=True)}
    assert reports["config"].status == "ok"
    assert reports["storage"].status == "ok"
    assert reports["clock"].status == "ok"
    assert reports["okx-spot"].status == "not_run"
    assert reports["mt5-demo"].status in {"blocked", "not_run"}

