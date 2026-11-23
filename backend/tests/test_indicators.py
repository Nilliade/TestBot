"""Indicator reference vector & batch-vs-incremental tests (SPEC §7, ACCEPTANCE A4).

All expected reference values are hand-calculated from SPEC §7.1–7.6 formulas
(never generated from the implementation under test).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

import pytest

from backend.app.domain.candle import Candle
from backend.app.indicators.engine import (
    IncrementalATR,
    IncrementalBollinger,
    IncrementalDonchian,
    IncrementalEMA,
    IncrementalRSI,
    IndicatorSuite,
    IndicatorSuiteConfig,
    compute_atr_batch,
    compute_bollinger_batch,
    compute_donchian_batch,
    compute_ema_batch,
    compute_rsi_batch,
)

UTC = timezone.utc
T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
REL_TOL = 1e-9


def _make_candle(
    idx: int,
    *,
    open_p: str = "100",
    high_p: str = "110",
    low_p: str = "90",
    close_p: str = "100",
    is_closed: bool = True,
    flags: set[str] | None = None,
) -> Candle:
    open_t = T0 + timedelta(hours=idx)
    return Candle(
        source_id="okx-spot",
        instrument_id="BTC-USDT",
        timeframe="1h",
        open_time_utc=open_t,
        close_time_utc=open_t + timedelta(hours=1),
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume="10",
        volume_type="exchange",
        price_basis="last",
        is_closed=is_closed,
        quality_flags=flags or set(),
        revision=1,
        received_at_utc=open_t + timedelta(hours=1),
        updated_at_utc=open_t + timedelta(hours=1),
    )


def test_ema_hand_calculated_reference_vectors():
    # EMA(3): closes = [10, 20, 30, 40, 50], k = 2/(3+1) = 0.5
    # idx 0..1: warm-up (None)
    # idx 2: SMA(10, 20, 30) = 20.0
    # idx 3: 40 * 0.5 + 20 * 0.5 = 30.0
    # idx 4: 50 * 0.5 + 30 * 0.5 = 40.0
    closes = ["10", "20", "30", "40", "50"]
    candles = [_make_candle(i, open_p=c, high_p=c, low_p=c, close_p=c) for i, c in enumerate(closes)]

    batch = compute_ema_batch(candles, period=3)
    inc = IncrementalEMA(period=3)
    inc_out = [inc.update(c) for c in candles]

    expected = [None, None, 20.0, 30.0, 40.0]
    for i, exp in enumerate(expected):
        if exp is None:
            assert batch[i].value is None and batch[i].status == "WARM_UP"
            assert inc_out[i].value is None and inc_out[i].status == "WARM_UP"
        else:
            assert batch[i].status == "READY" and inc_out[i].status == "READY"
            assert math.isclose(batch[i].value, exp, rel_tol=REL_TOL)
            assert math.isclose(inc_out[i].value, exp, rel_tol=REL_TOL)


def test_rsi_wilder_hand_calculated_reference_vectors_and_edges():
    # RSI(3) Wilder: closes = [44, 46, 48, 45, 48]
    # deltas: +2, +2, -3, +3
    # idx 0..2: warm-up (None)
    # idx 3: avg_gain = (2+2+0)/3 = 4/3, avg_loss = (0+0+3)/3 = 1.0 -> RSI = 400/7
    # idx 4: avg_gain = ((4/3)*2 + 3)/3 = 17/9, avg_loss = (1*2 + 0)/3 = 2/3 -> RS = 17/6 -> RSI = 1700/23
    closes = ["44", "46", "48", "45", "48"]
    candles = [_make_candle(i, open_p=c, high_p=c, low_p=c, close_p=c) for i, c in enumerate(closes)]

    batch = compute_rsi_batch(candles, period=3)
    inc = IncrementalRSI(period=3)
    inc_out = [inc.update(c) for c in candles]

    expected = [None, None, None, 400.0 / 7.0, 1700.0 / 23.0]
    for i, exp in enumerate(expected):
        if exp is None:
            assert batch[i].value is None and batch[i].status == "WARM_UP"
            assert inc_out[i].value is None and inc_out[i].status == "WARM_UP"
        else:
            assert math.isclose(batch[i].value, exp, rel_tol=REL_TOL)
            assert math.isclose(inc_out[i].value, exp, rel_tol=REL_TOL)

    # Edge case 1: strictly rising prices -> avg_loss == 0, avg_gain > 0 -> RSI == 100.0
    up_candles = [_make_candle(i, open_p=str(10 + i), high_p=str(10 + i), low_p=str(10 + i), close_p=str(10 + i)) for i in range(5)]
    assert compute_rsi_batch(up_candles, period=3)[3].value == 100.0

    # Edge case 2: strictly falling prices -> avg_gain == 0, avg_loss > 0 -> RSI == 0.0
    down_candles = [_make_candle(i, open_p=str(20 - i), high_p=str(20 - i), low_p=str(20 - i), close_p=str(20 - i)) for i in range(5)]
    assert compute_rsi_batch(down_candles, period=3)[3].value == 0.0

    # Edge case 3: flat prices -> avg_gain == 0 and avg_loss == 0 -> RSI == 50.0
    flat_candles = [_make_candle(i, open_p="100", high_p="100", low_p="100", close_p="100") for i in range(5)]
    assert compute_rsi_batch(flat_candles, period=3)[3].value == 50.0


def test_atr_wilder_hand_calculated_reference_vectors():
    # ATR(3) Wilder:
    # t=0: H=12, L=10, C=11 -> TR_0 = 2.0
    # t=1: H=15, L=11, C=14 (prev_C=11) -> TR_1 = max(4, 4, 0) = 4.0
    # t=2: H=16, L=10, C=12 (prev_C=14) -> TR_2 = max(6, 2, 4) = 6.0 -> ATR_2 = (2+4+6)/3 = 4.0
    # t=3: H=15, L=12, C=13 (prev_C=12) -> TR_3 = max(3, 3, 0) = 3.0 -> ATR_3 = (4*2 + 3)/3 = 11/3
    bars = [
        ("11", "12", "10", "11"),
        ("12", "15", "11", "14"),
        ("14", "16", "10", "12"),
        ("12", "15", "12", "13"),
    ]
    candles = [_make_candle(i, open_p=o, high_p=h, low_p=l, close_p=c) for i, (o, h, l, c) in enumerate(bars)]

    batch = compute_atr_batch(candles, period=3)
    inc = IncrementalATR(period=3)
    inc_out = [inc.update(c) for c in candles]

    expected = [None, None, 4.0, 11.0 / 3.0]
    for i, exp in enumerate(expected):
        if exp is None:
            assert batch[i].value is None and batch[i].status == "WARM_UP"
            assert inc_out[i].value is None and inc_out[i].status == "WARM_UP"
        else:
            assert math.isclose(batch[i].value, exp, rel_tol=REL_TOL)
            assert math.isclose(inc_out[i].value, exp, rel_tol=REL_TOL)


def test_donchian_hand_calculated_reference_vectors():
    # Donchian(3):
    # t=0: H=12, L=8
    # t=1: H=15, L=9
    # t=2: H=14, L=7  -> upper=15, lower=7, middle=11, prev_upper=None
    # t=3: H=18, L=10 -> upper=18, lower=7, middle=12.5, prev_upper=15, prev_lower=7
    hl = [("12", "8"), ("15", "9"), ("14", "7"), ("18", "10")]
    candles = [_make_candle(i, open_p="10", high_p=h, low_p=l, close_p="10") for i, (h, l) in enumerate(hl)]

    batch = compute_donchian_batch(candles, period=3)
    inc = IncrementalDonchian(period=3)
    inc_out = [inc.update(c) for c in candles]

    assert batch[0].upper is None and batch[1].upper is None
    assert batch[2].upper == 15.0 and batch[2].lower == 7.0 and batch[2].middle == 11.0
    assert batch[2].prev_upper is None and batch[2].prev_lower is None
    assert batch[3].upper == 18.0 and batch[3].lower == 7.0 and batch[3].middle == 12.5
    assert batch[3].prev_upper == 15.0 and batch[3].prev_lower == 7.0
    assert batch == inc_out


def test_bollinger_ddof0_hand_calculated_reference_vectors():
    # Bollinger(3, k=2.0) population std (ddof=0):
    # closes = [10, 20, 30] -> mean = 20, var = ((100 + 0 + 100)/3) = 200/3, std = sqrt(200/3)
    closes = ["10", "20", "30", "20"]
    candles = [_make_candle(i, open_p=c, high_p=c, low_p=c, close_p=c) for i, c in enumerate(closes)]

    batch = compute_bollinger_batch(candles, period=3, num_std=2.0)
    inc = IncrementalBollinger(period=3, num_std=2.0)
    inc_out = [inc.update(c) for c in candles]

    assert batch[0].middle is None and batch[1].middle is None
    exp_std_2 = math.sqrt(200.0 / 3.0)
    assert math.isclose(batch[2].middle, 20.0, rel_tol=REL_TOL)
    assert math.isclose(batch[2].std, exp_std_2, rel_tol=REL_TOL)
    assert math.isclose(batch[2].upper, 20.0 + 2.0 * exp_std_2, rel_tol=REL_TOL)
    assert math.isclose(batch[2].lower, 20.0 - 2.0 * exp_std_2, rel_tol=REL_TOL)

    for b, x in zip(batch, inc_out):
        assert b.status == x.status
        if b.middle is not None:
            assert math.isclose(b.middle, x.middle, rel_tol=REL_TOL)
            assert math.isclose(b.upper, x.upper, rel_tol=REL_TOL)
            assert math.isclose(b.lower, x.lower, rel_tol=REL_TOL)
            assert math.isclose(b.std, x.std, rel_tol=REL_TOL)


def test_confirmed_gap_resets_state_while_possible_gap_flags_unconfirmed():
    # 4 bars before gap (period=3 reaches READY at idx 2 and 3), then bar 4 has CONFIRMED_GAP -> resets to WARM_UP!
    candles = [
        _make_candle(0, open_p="10", high_p="12", low_p="8", close_p="10"),
        _make_candle(1, open_p="20", high_p="22", low_p="18", close_p="20"),
        _make_candle(2, open_p="30", high_p="32", low_p="28", close_p="30"),
        _make_candle(3, open_p="40", high_p="42", low_p="38", close_p="40"),
        # Bar 4 has CONFIRMED_GAP -> resets state, becomes bar 0 of new segment (WARM_UP)
        _make_candle(5, open_p="100", high_p="105", low_p="95", close_p="100", flags={"CONFIRMED_GAP"}),
        _make_candle(6, open_p="110", high_p="115", low_p="105", close_p="110"),
        _make_candle(7, open_p="120", high_p="125", low_p="115", close_p="120"),
        # Bar 7 has POSSIBLE_GAP -> does NOT reset state, marks quality="UNCONFIRMED"
        _make_candle(9, open_p="130", high_p="135", low_p="125", close_p="130", flags={"POSSIBLE_GAP"}),
    ]

    ema_b = compute_ema_batch(candles, period=3)
    assert ema_b[2].status == "READY" and ema_b[3].status == "READY"
    assert ema_b[4].status == "WARM_UP" and ema_b[4].value is None and ema_b[4].reset_on_gap is True
    assert ema_b[5].status == "WARM_UP" and ema_b[5].value is None
    assert ema_b[6].status == "READY" and math.isclose(ema_b[6].value, 110.0, rel_tol=REL_TOL)
    assert ema_b[6].quality == "CONFIRMED"
    # POSSIBLE_GAP at index 7 does NOT reset, marks UNCONFIRMED
    assert ema_b[7].status == "READY" and ema_b[7].reset_on_gap is False
    assert ema_b[7].quality == "UNCONFIRMED"
    assert math.isclose(ema_b[7].value, 120.0, rel_tol=REL_TOL)


def test_batch_vs_incremental_equality_across_long_series_with_gaps():
    candles: list[Candle] = []
    for i in range(80):
        base = 100.0 + 5.0 * math.sin(i * 0.3) + i * 0.25
        flags: set[str] = set()
        if i == 25:
            flags.add("POSSIBLE_GAP")
        elif i == 50:
            flags.add("CONFIRMED_GAP")
        candles.append(
            _make_candle(
                i,
                open_p=f"{base:.4f}",
                high_p=f"{base + 2.5:.4f}",
                low_p=f"{base - 2.0:.4f}",
                close_p=f"{base + 0.75:.4f}",
                flags=flags,
            )
        )

    cfg = IndicatorSuiteConfig(
        ema_fast_period=5,
        ema_slow_period=12,
        rsi_period=7,
        atr_period=7,
        donchian_period=8,
        bollinger_period=8,
        bollinger_std=2.0,
    )
    suite = IndicatorSuite("okx-spot", "BTC-USDT", "1h", config=cfg)
    for c in candles:
        suite.update_closed(c)
    snap = suite.snapshot()

    ema_fast_b = compute_ema_batch(candles, cfg.ema_fast_period)
    ema_slow_b = compute_ema_batch(candles, cfg.ema_slow_period)
    rsi_b = compute_rsi_batch(candles, cfg.rsi_period)
    atr_b = compute_atr_batch(candles, cfg.atr_period)
    don_b = compute_donchian_batch(candles, cfg.donchian_period)
    bol_b = compute_bollinger_batch(candles, cfg.bollinger_period, cfg.bollinger_std)

    for a, b in zip(snap.ema_fast, ema_fast_b):
        assert a.status == b.status and a.quality == b.quality and a.reset_on_gap == b.reset_on_gap
        assert (a.value is None and b.value is None) or math.isclose(a.value, b.value, rel_tol=REL_TOL)

    for a, b in zip(snap.ema_slow, ema_slow_b):
        assert a.status == b.status and a.quality == b.quality and a.reset_on_gap == b.reset_on_gap
        assert (a.value is None and b.value is None) or math.isclose(a.value, b.value, rel_tol=REL_TOL)

    for a, b in zip(snap.rsi, rsi_b):
        assert a.status == b.status and a.quality == b.quality and a.reset_on_gap == b.reset_on_gap
        assert (a.value is None and b.value is None) or math.isclose(a.value, b.value, rel_tol=REL_TOL)

    for a, b in zip(snap.atr, atr_b):
        assert a.status == b.status and a.quality == b.quality and a.reset_on_gap == b.reset_on_gap
        assert (a.value is None and b.value is None) or math.isclose(a.value, b.value, rel_tol=REL_TOL)

    for a, b in zip(snap.donchian, don_b):
        assert a == b

    for a, b in zip(snap.bollinger, bol_b):
        assert a.status == b.status and a.quality == b.quality and a.reset_on_gap == b.reset_on_gap
        if a.middle is not None:
            assert math.isclose(a.middle, b.middle, rel_tol=REL_TOL)
            assert math.isclose(a.upper, b.upper, rel_tol=REL_TOL)
            assert math.isclose(a.lower, b.lower, rel_tol=REL_TOL)
            assert math.isclose(a.std, b.std, rel_tol=REL_TOL)


def test_unclosed_candle_preview_is_marked_and_does_not_mutate_state():
    candles = [
        _make_candle(i, open_p="10", high_p=str(15 + i * 10), low_p="8", close_p=str(10 + i * 10))
        for i in range(3)
    ]
    suite = IndicatorSuite("okx-spot", "BTC-USDT", "1h", config=IndicatorSuiteConfig(ema_fast_period=3))
    for c in candles:
        suite.update_closed(c)

    open_bar = _make_candle(3, open_p="40", high_p="55", low_p="35", close_p="50", is_closed=False)
    with pytest.raises(ValueError, match="closed candle"):
        suite.update_closed(open_bar)

    snap_no_preview = suite.snapshot(open_candle=open_bar, include_preview=False)
    assert len(snap_no_preview.ema_fast) == 3

    snap_preview = suite.snapshot(open_candle=open_bar, include_preview=True)
    assert len(snap_preview.ema_fast) == 4
    assert snap_preview.ema_fast[-1].status == "PREVIEW_UNCONFIRMED"
    assert snap_preview.ema_fast[-1].quality == "PREVIEW_UNCONFIRMED"
    # Verify internal closed series was not mutated by preview.
    assert len(suite.ema_fast_series) == 3
