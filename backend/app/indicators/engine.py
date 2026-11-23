"""Stage 1 Indicator Engine (SPEC §7, ACCEPTANCE A4).

Implements:
- EMA(n)              (§7.1: SMA seed of first n closes, k = 2/(n+1), warm-up n-1)
- RSI(n) Wilder       (§7.2: Wilder initial mean of n deltas, warm-up n)
- ATR(n) Wilder       (§7.3: TR_0 = high_0 - low_0, Wilder mean of n TRs, warm-up n-1)
- Donchian(n)         (§7.4: inclusive upper/lower + no-look-ahead prev_upper/prev_lower, warm-up n-1)
- Bollinger(n, k=2.0) (§7.5: SMA middle, population std ddof=0, warm-up n-1)

Gap & unclosed candle rules (§3, §7.6):
- Only closed candles (is_closed=True) advance indicator state.
- An unclosed candle (is_closed=False) never mutates state; `preview_unclosed()`
  computes a non-mutating preview marked `status="PREVIEW_UNCONFIRMED"`.
- On `CONFIRMED_GAP` in `candle.quality_flags`, indicator state resets before
  processing that candle and warm-up restarts (that candle is bar 0 of the new segment).
- On `POSSIBLE_GAP` in `candle.quality_flags`, state does NOT reset, but affected
  values are marked `quality="UNCONFIRMED"`.
"""

from __future__ import annotations

from collections import deque
import copy
from dataclasses import dataclass
from datetime import datetime
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.app.domain.candle import Candle

IndicatorStatus = Literal["WARM_UP", "READY", "PREVIEW_UNCONFIRMED"]
IndicatorQuality = Literal["CONFIRMED", "UNCONFIRMED", "PREVIEW_UNCONFIRMED"]


class ScalarPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_time_utc: datetime
    value: float | None
    status: IndicatorStatus
    quality: IndicatorQuality
    reset_on_gap: bool = False


class DonchianPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_time_utc: datetime
    upper: float | None
    lower: float | None
    middle: float | None
    prev_upper: float | None = None
    prev_lower: float | None = None
    status: IndicatorStatus
    quality: IndicatorQuality
    reset_on_gap: bool = False


class BollingerPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_time_utc: datetime
    middle: float | None
    upper: float | None
    lower: float | None
    std: float | None
    status: IndicatorStatus
    quality: IndicatorQuality
    reset_on_gap: bool = False


def _validate_period(period: int, min_period: int = 1) -> None:
    if not isinstance(period, int) or period < min_period:
        raise ValueError(f"period must be an integer >= {min_period}, got {period!r}")


class IncrementalEMA:
    """Incremental EMA(n) per SPEC §7.1."""

    def __init__(self, period: int) -> None:
        _validate_period(period, 1)
        self.period = period
        self.k = 2.0 / (period + 1.0)
        self.warm_up_bars = period - 1
        self.reset()

    def reset(self) -> None:
        self._seed_closes: list[float] = []
        self._ema: float | None = None
        self._has_possible_gap: bool = False

    def _step(self, candle: Candle, *, is_preview: bool = False) -> ScalarPoint:
        reset_occurred = False
        if "CONFIRMED_GAP" in candle.quality_flags:
            self.reset()
            reset_occurred = True
        if "POSSIBLE_GAP" in candle.quality_flags:
            self._has_possible_gap = True

        c = float(candle.close)
        if self._ema is None:
            self._seed_closes.append(c)
            if len(self._seed_closes) < self.period:
                status: IndicatorStatus = "PREVIEW_UNCONFIRMED" if is_preview else "WARM_UP"
                quality: IndicatorQuality = (
                    "PREVIEW_UNCONFIRMED"
                    if is_preview
                    else ("UNCONFIRMED" if self._has_possible_gap else "CONFIRMED")
                )
                return ScalarPoint(
                    open_time_utc=candle.open_time_utc,
                    value=None,
                    status=status,
                    quality=quality,
                    reset_on_gap=reset_occurred,
                )
            self._ema = sum(self._seed_closes) / float(self.period)
        else:
            self._ema = c * self.k + self._ema * (1.0 - self.k)

        status = "PREVIEW_UNCONFIRMED" if is_preview else "READY"
        quality = (
            "PREVIEW_UNCONFIRMED"
            if is_preview
            else ("UNCONFIRMED" if self._has_possible_gap else "CONFIRMED")
        )
        return ScalarPoint(
            open_time_utc=candle.open_time_utc,
            value=self._ema,
            status=status,
            quality=quality,
            reset_on_gap=reset_occurred,
        )

    def update(self, candle: Candle) -> ScalarPoint:
        if not candle.is_closed:
            raise ValueError("IncrementalEMA.update requires a closed candle (is_closed=True)")
        return self._step(candle, is_preview=False)

    def preview(self, candle: Candle) -> ScalarPoint:
        clone = copy.deepcopy(self)
        return clone._step(candle, is_preview=True)


class IncrementalRSI:
    """Incremental RSI(n) with Wilder smoothing per SPEC §7.2."""

    def __init__(self, period: int = 14) -> None:
        _validate_period(period, 1)
        self.period = period
        self.warm_up_bars = period
        self.reset()

    def reset(self) -> None:
        self._prev_close: float | None = None
        self._seed_gains: list[float] = []
        self._seed_losses: list[float] = []
        self._avg_gain: float | None = None
        self._avg_loss: float | None = None
        self._has_possible_gap: bool = False

    @staticmethod
    def _compute_rsi(avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0.0 and avg_gain == 0.0:
            return 50.0
        if avg_loss == 0.0:
            return 100.0
        if avg_gain == 0.0:
            return 0.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _step(self, candle: Candle, *, is_preview: bool = False) -> ScalarPoint:
        reset_occurred = False
        if "CONFIRMED_GAP" in candle.quality_flags:
            self.reset()
            reset_occurred = True
        if "POSSIBLE_GAP" in candle.quality_flags:
            self._has_possible_gap = True

        c = float(candle.close)
        quality: IndicatorQuality = (
            "PREVIEW_UNCONFIRMED"
            if is_preview
            else ("UNCONFIRMED" if self._has_possible_gap else "CONFIRMED")
        )

        if self._prev_close is None:
            self._prev_close = c
            status: IndicatorStatus = "PREVIEW_UNCONFIRMED" if is_preview else "WARM_UP"
            return ScalarPoint(
                open_time_utc=candle.open_time_utc,
                value=None,
                status=status,
                quality=quality,
                reset_on_gap=reset_occurred,
            )

        delta = c - self._prev_close
        self._prev_close = c
        gain = delta if delta > 0.0 else 0.0
        loss = -delta if delta < 0.0 else 0.0

        if self._avg_gain is None or self._avg_loss is None:
            self._seed_gains.append(gain)
            self._seed_losses.append(loss)
            if len(self._seed_gains) < self.period:
                status = "PREVIEW_UNCONFIRMED" if is_preview else "WARM_UP"
                return ScalarPoint(
                    open_time_utc=candle.open_time_utc,
                    value=None,
                    status=status,
                    quality=quality,
                    reset_on_gap=reset_occurred,
                )
            self._avg_gain = sum(self._seed_gains) / float(self.period)
            self._avg_loss = sum(self._seed_losses) / float(self.period)
        else:
            n = float(self.period)
            self._avg_gain = (self._avg_gain * (n - 1.0) + gain) / n
            self._avg_loss = (self._avg_loss * (n - 1.0) + loss) / n

        rsi = self._compute_rsi(self._avg_gain, self._avg_loss)
        status = "PREVIEW_UNCONFIRMED" if is_preview else "READY"
        return ScalarPoint(
            open_time_utc=candle.open_time_utc,
            value=rsi,
            status=status,
            quality=quality,
            reset_on_gap=reset_occurred,
        )

    def update(self, candle: Candle) -> ScalarPoint:
        if not candle.is_closed:
            raise ValueError("IncrementalRSI.update requires a closed candle (is_closed=True)")
        return self._step(candle, is_preview=False)

    def preview(self, candle: Candle) -> ScalarPoint:
        clone = copy.deepcopy(self)
        return clone._step(candle, is_preview=True)


class IncrementalATR:
    """Incremental ATR(n) with Wilder smoothing per SPEC §7.3."""

    def __init__(self, period: int = 14) -> None:
        _validate_period(period, 1)
        self.period = period
        self.warm_up_bars = period - 1
        self.reset()

    def reset(self) -> None:
        self._prev_close: float | None = None
        self._seed_trs: list[float] = []
        self._atr: float | None = None
        self._has_possible_gap: bool = False

    def _step(self, candle: Candle, *, is_preview: bool = False) -> ScalarPoint:
        reset_occurred = False
        if "CONFIRMED_GAP" in candle.quality_flags:
            self.reset()
            reset_occurred = True
        if "POSSIBLE_GAP" in candle.quality_flags:
            self._has_possible_gap = True

        h = float(candle.high)
        l = float(candle.low)
        c = float(candle.close)
        if self._prev_close is None:
            tr = h - l
        else:
            tr = max(h - l, abs(h - self._prev_close), abs(l - self._prev_close))
        self._prev_close = c

        quality: IndicatorQuality = (
            "PREVIEW_UNCONFIRMED"
            if is_preview
            else ("UNCONFIRMED" if self._has_possible_gap else "CONFIRMED")
        )

        if self._atr is None:
            self._seed_trs.append(tr)
            if len(self._seed_trs) < self.period:
                status: IndicatorStatus = "PREVIEW_UNCONFIRMED" if is_preview else "WARM_UP"
                return ScalarPoint(
                    open_time_utc=candle.open_time_utc,
                    value=None,
                    status=status,
                    quality=quality,
                    reset_on_gap=reset_occurred,
                )
            self._atr = sum(self._seed_trs) / float(self.period)
        else:
            n = float(self.period)
            self._atr = (self._atr * (n - 1.0) + tr) / n

        status = "PREVIEW_UNCONFIRMED" if is_preview else "READY"
        return ScalarPoint(
            open_time_utc=candle.open_time_utc,
            value=self._atr,
            status=status,
            quality=quality,
            reset_on_gap=reset_occurred,
        )

    def update(self, candle: Candle) -> ScalarPoint:
        if not candle.is_closed:
            raise ValueError("IncrementalATR.update requires a closed candle (is_closed=True)")
        return self._step(candle, is_preview=False)

    def preview(self, candle: Candle) -> ScalarPoint:
        clone = copy.deepcopy(self)
        return clone._step(candle, is_preview=True)


class IncrementalDonchian:
    """Incremental Donchian(n) per SPEC §7.4."""

    def __init__(self, period: int = 20) -> None:
        _validate_period(period, 1)
        self.period = period
        self.warm_up_bars = period - 1
        self.reset()

    def reset(self) -> None:
        self._highs: deque[float] = deque(maxlen=self.period + 1)
        self._lows: deque[float] = deque(maxlen=self.period + 1)
        self._gap_flags: deque[bool] = deque(maxlen=self.period + 1)

    def _step(self, candle: Candle, *, is_preview: bool = False) -> DonchianPoint:
        reset_occurred = False
        if "CONFIRMED_GAP" in candle.quality_flags:
            self.reset()
            reset_occurred = True

        has_pos_gap = "POSSIBLE_GAP" in candle.quality_flags
        self._highs.append(float(candle.high))
        self._lows.append(float(candle.low))
        self._gap_flags.append(has_pos_gap)

        count = len(self._highs)
        window_gaps = list(self._gap_flags)[-self.period :]
        quality: IndicatorQuality = (
            "PREVIEW_UNCONFIRMED"
            if is_preview
            else ("UNCONFIRMED" if any(window_gaps) else "CONFIRMED")
        )

        if count < self.period:
            status: IndicatorStatus = "PREVIEW_UNCONFIRMED" if is_preview else "WARM_UP"
            return DonchianPoint(
                open_time_utc=candle.open_time_utc,
                upper=None,
                lower=None,
                middle=None,
                prev_upper=None,
                prev_lower=None,
                status=status,
                quality=quality,
                reset_on_gap=reset_occurred,
            )

        curr_highs = list(self._highs)[-self.period :]
        curr_lows = list(self._lows)[-self.period :]
        upper = max(curr_highs)
        lower = min(curr_lows)
        middle = (upper + lower) / 2.0

        prev_upper: float | None = None
        prev_lower: float | None = None
        if count >= self.period + 1:
            prev_highs = list(self._highs)[: self.period]
            prev_lows = list(self._lows)[: self.period]
            prev_upper = max(prev_highs)
            prev_lower = min(prev_lows)

        status = "PREVIEW_UNCONFIRMED" if is_preview else "READY"
        return DonchianPoint(
            open_time_utc=candle.open_time_utc,
            upper=upper,
            lower=lower,
            middle=middle,
            prev_upper=prev_upper,
            prev_lower=prev_lower,
            status=status,
            quality=quality,
            reset_on_gap=reset_occurred,
        )

    def update(self, candle: Candle) -> DonchianPoint:
        if not candle.is_closed:
            raise ValueError("IncrementalDonchian.update requires a closed candle (is_closed=True)")
        return self._step(candle, is_preview=False)

    def preview(self, candle: Candle) -> DonchianPoint:
        clone = copy.deepcopy(self)
        return clone._step(candle, is_preview=True)


class IncrementalBollinger:
    """Incremental Bollinger Bands(n, k) with population std (ddof=0) per SPEC §7.5."""

    def __init__(self, period: int = 20, num_std: float = 2.0) -> None:
        _validate_period(period, 1)
        if num_std <= 0:
            raise ValueError("num_std must be > 0")
        self.period = period
        self.num_std = float(num_std)
        self.warm_up_bars = period - 1
        self.reset()

    def reset(self) -> None:
        self._closes: deque[float] = deque(maxlen=self.period)
        self._gap_flags: deque[bool] = deque(maxlen=self.period)

    def _step(self, candle: Candle, *, is_preview: bool = False) -> BollingerPoint:
        reset_occurred = False
        if "CONFIRMED_GAP" in candle.quality_flags:
            self.reset()
            reset_occurred = True

        self._closes.append(float(candle.close))
        self._gap_flags.append("POSSIBLE_GAP" in candle.quality_flags)

        quality: IndicatorQuality = (
            "PREVIEW_UNCONFIRMED"
            if is_preview
            else ("UNCONFIRMED" if any(self._gap_flags) else "CONFIRMED")
        )

        if len(self._closes) < self.period:
            status: IndicatorStatus = "PREVIEW_UNCONFIRMED" if is_preview else "WARM_UP"
            return BollingerPoint(
                open_time_utc=candle.open_time_utc,
                middle=None,
                upper=None,
                lower=None,
                std=None,
                status=status,
                quality=quality,
                reset_on_gap=reset_occurred,
            )

        n = float(self.period)
        middle = sum(self._closes) / n
        variance = sum((c - middle) ** 2 for c in self._closes) / n
        std = math.sqrt(variance)
        upper = middle + self.num_std * std
        lower = middle - self.num_std * std
        status = "PREVIEW_UNCONFIRMED" if is_preview else "READY"
        return BollingerPoint(
            open_time_utc=candle.open_time_utc,
            middle=middle,
            upper=upper,
            lower=lower,
            std=std,
            status=status,
            quality=quality,
            reset_on_gap=reset_occurred,
        )

    def update(self, candle: Candle) -> BollingerPoint:
        if not candle.is_closed:
            raise ValueError("IncrementalBollinger.update requires a closed candle (is_closed=True)")
        return self._step(candle, is_preview=False)

    def preview(self, candle: Candle) -> BollingerPoint:
        clone = copy.deepcopy(self)
        return clone._step(candle, is_preview=True)


# ---------------------------------------------------------------------------
# Independent Batch Functions (SPEC §7.1–7.7)
# ---------------------------------------------------------------------------


def _assert_closed_chronological(candles: list[Candle]) -> None:
    prev_t: datetime | None = None
    for c in candles:
        if not c.is_closed:
            raise ValueError("Batch indicator calculation accepts only closed candles (is_closed=True)")
        if prev_t is not None and c.open_time_utc <= prev_t:
            raise ValueError("Candles must be in strictly ascending chronological order by open_time_utc")
        prev_t = c.open_time_utc


def compute_ema_batch(candles: list[Candle], period: int) -> list[ScalarPoint]:
    _validate_period(period, 1)
    _assert_closed_chronological(candles)
    k = 2.0 / (period + 1.0)
    out: list[ScalarPoint] = []
    seg_closes: list[float] = []
    ema_val: float | None = None
    has_pos_gap = False

    for c in candles:
        reset_on_gap = False
        if "CONFIRMED_GAP" in c.quality_flags:
            seg_closes = []
            ema_val = None
            has_pos_gap = False
            reset_on_gap = True
        if "POSSIBLE_GAP" in c.quality_flags:
            has_pos_gap = True

        close_f = float(c.close)
        seg_closes.append(close_f)
        quality: IndicatorQuality = "UNCONFIRMED" if has_pos_gap else "CONFIRMED"
        if len(seg_closes) < period:
            out.append(
                ScalarPoint(
                    open_time_utc=c.open_time_utc,
                    value=None,
                    status="WARM_UP",
                    quality=quality,
                    reset_on_gap=reset_on_gap,
                )
            )
        elif len(seg_closes) == period:
            ema_val = sum(seg_closes) / float(period)
            out.append(
                ScalarPoint(
                    open_time_utc=c.open_time_utc,
                    value=ema_val,
                    status="READY",
                    quality=quality,
                    reset_on_gap=reset_on_gap,
                )
            )
        else:
            assert ema_val is not None
            ema_val = close_f * k + ema_val * (1.0 - k)
            out.append(
                ScalarPoint(
                    open_time_utc=c.open_time_utc,
                    value=ema_val,
                    status="READY",
                    quality=quality,
                    reset_on_gap=reset_on_gap,
                )
            )
    return out


def compute_rsi_batch(candles: list[Candle], period: int = 14) -> list[ScalarPoint]:
    _validate_period(period, 1)
    _assert_closed_chronological(candles)
    out: list[ScalarPoint] = []
    seg_closes: list[float] = []
    avg_gain: float | None = None
    avg_loss: float | None = None
    has_pos_gap = False

    for c in candles:
        reset_on_gap = False
        if "CONFIRMED_GAP" in c.quality_flags:
            seg_closes = []
            avg_gain = None
            avg_loss = None
            has_pos_gap = False
            reset_on_gap = True
        if "POSSIBLE_GAP" in c.quality_flags:
            has_pos_gap = True

        close_f = float(c.close)
        seg_closes.append(close_f)
        quality: IndicatorQuality = "UNCONFIRMED" if has_pos_gap else "CONFIRMED"

        if len(seg_closes) <= period:
            out.append(
                ScalarPoint(
                    open_time_utc=c.open_time_utc,
                    value=None,
                    status="WARM_UP",
                    quality=quality,
                    reset_on_gap=reset_on_gap,
                )
            )
            continue

        if len(seg_closes) == period + 1:
            gains = []
            losses = []
            for i in range(1, period + 1):
                d = seg_closes[i] - seg_closes[i - 1]
                gains.append(d if d > 0.0 else 0.0)
                losses.append(-d if d < 0.0 else 0.0)
            avg_gain = sum(gains) / float(period)
            avg_loss = sum(losses) / float(period)
        else:
            assert avg_gain is not None and avg_loss is not None
            d = seg_closes[-1] - seg_closes[-2]
            g = d if d > 0.0 else 0.0
            l = -d if d < 0.0 else 0.0
            n = float(period)
            avg_gain = (avg_gain * (n - 1.0) + g) / n
            avg_loss = (avg_loss * (n - 1.0) + l) / n

        rsi = IncrementalRSI._compute_rsi(avg_gain, avg_loss)
        out.append(
            ScalarPoint(
                open_time_utc=c.open_time_utc,
                value=rsi,
                status="READY",
                quality=quality,
                reset_on_gap=reset_on_gap,
            )
        )
    return out


def compute_atr_batch(candles: list[Candle], period: int = 14) -> list[ScalarPoint]:
    _validate_period(period, 1)
    _assert_closed_chronological(candles)
    out: list[ScalarPoint] = []
    seg_trs: list[float] = []
    prev_close: float | None = None
    atr_val: float | None = None
    has_pos_gap = False

    for c in candles:
        reset_on_gap = False
        if "CONFIRMED_GAP" in c.quality_flags:
            seg_trs = []
            prev_close = None
            atr_val = None
            has_pos_gap = False
            reset_on_gap = True
        if "POSSIBLE_GAP" in c.quality_flags:
            has_pos_gap = True

        h = float(c.high)
        l = float(c.low)
        close_f = float(c.close)
        if prev_close is None:
            tr = h - l
        else:
            tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        prev_close = close_f
        seg_trs.append(tr)
        quality: IndicatorQuality = "UNCONFIRMED" if has_pos_gap else "CONFIRMED"

        if len(seg_trs) < period:
            out.append(
                ScalarPoint(
                    open_time_utc=c.open_time_utc,
                    value=None,
                    status="WARM_UP",
                    quality=quality,
                    reset_on_gap=reset_on_gap,
                )
            )
        elif len(seg_trs) == period:
            atr_val = sum(seg_trs) / float(period)
            out.append(
                ScalarPoint(
                    open_time_utc=c.open_time_utc,
                    value=atr_val,
                    status="READY",
                    quality=quality,
                    reset_on_gap=reset_on_gap,
                )
            )
        else:
            assert atr_val is not None
            n = float(period)
            atr_val = (atr_val * (n - 1.0) + tr) / n
            out.append(
                ScalarPoint(
                    open_time_utc=c.open_time_utc,
                    value=atr_val,
                    status="READY",
                    quality=quality,
                    reset_on_gap=reset_on_gap,
                )
            )
    return out


def compute_donchian_batch(candles: list[Candle], period: int = 20) -> list[DonchianPoint]:
    _validate_period(period, 1)
    _assert_closed_chronological(candles)
    out: list[DonchianPoint] = []
    seg_highs: list[float] = []
    seg_lows: list[float] = []
    seg_gaps: list[bool] = []

    for c in candles:
        reset_on_gap = False
        if "CONFIRMED_GAP" in c.quality_flags:
            seg_highs = []
            seg_lows = []
            seg_gaps = []
            reset_on_gap = True

        seg_highs.append(float(c.high))
        seg_lows.append(float(c.low))
        seg_gaps.append("POSSIBLE_GAP" in c.quality_flags)

        win_gaps = seg_gaps[-period:]
        quality: IndicatorQuality = "UNCONFIRMED" if any(win_gaps) else "CONFIRMED"
        if len(seg_highs) < period:
            out.append(
                DonchianPoint(
                    open_time_utc=c.open_time_utc,
                    upper=None,
                    lower=None,
                    middle=None,
                    prev_upper=None,
                    prev_lower=None,
                    status="WARM_UP",
                    quality=quality,
                    reset_on_gap=reset_on_gap,
                )
            )
            continue

        upper = max(seg_highs[-period:])
        lower = min(seg_lows[-period:])
        middle = (upper + lower) / 2.0
        prev_upper: float | None = None
        prev_lower: float | None = None
        if len(seg_highs) >= period + 1:
            prev_upper = max(seg_highs[-period - 1 : -1])
            prev_lower = min(seg_lows[-period - 1 : -1])

        out.append(
            DonchianPoint(
                open_time_utc=c.open_time_utc,
                upper=upper,
                lower=lower,
                middle=middle,
                prev_upper=prev_upper,
                prev_lower=prev_lower,
                status="READY",
                quality=quality,
                reset_on_gap=reset_on_gap,
            )
        )
    return out


def compute_bollinger_batch(candles: list[Candle], period: int = 20, num_std: float = 2.0) -> list[BollingerPoint]:
    _validate_period(period, 1)
    if num_std <= 0:
        raise ValueError("num_std must be > 0")
    _assert_closed_chronological(candles)
    out: list[BollingerPoint] = []
    seg_closes: list[float] = []
    seg_gaps: list[bool] = []

    for c in candles:
        reset_on_gap = False
        if "CONFIRMED_GAP" in c.quality_flags:
            seg_closes = []
            seg_gaps = []
            reset_on_gap = True

        seg_closes.append(float(c.close))
        seg_gaps.append("POSSIBLE_GAP" in c.quality_flags)
        win_gaps = seg_gaps[-period:]
        quality: IndicatorQuality = "UNCONFIRMED" if any(win_gaps) else "CONFIRMED"

        if len(seg_closes) < period:
            out.append(
                BollingerPoint(
                    open_time_utc=c.open_time_utc,
                    middle=None,
                    upper=None,
                    lower=None,
                    std=None,
                    status="WARM_UP",
                    quality=quality,
                    reset_on_gap=reset_on_gap,
                )
            )
            continue

        win = seg_closes[-period:]
        n = float(period)
        middle = sum(win) / n
        variance = sum((x - middle) ** 2 for x in win) / n
        std = math.sqrt(variance)
        upper = middle + num_std * std
        lower = middle - num_std * std
        out.append(
            BollingerPoint(
                open_time_utc=c.open_time_utc,
                middle=middle,
                upper=upper,
                lower=lower,
                std=std,
                status="READY",
                quality=quality,
                reset_on_gap=reset_on_gap,
            )
        )
    return out


@dataclass
class IndicatorSuiteConfig:
    ema_fast_period: int = 20
    ema_slow_period: int = 50
    rsi_period: int = 14
    atr_period: int = 14
    donchian_period: int = 20
    bollinger_period: int = 20
    bollinger_std: float = 2.0


class IndicatorSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    instrument_id: str
    timeframe: str
    ema_fast: list[ScalarPoint]
    ema_slow: list[ScalarPoint]
    rsi: list[ScalarPoint]
    atr: list[ScalarPoint]
    donchian: list[DonchianPoint]
    bollinger: list[BollingerPoint]


class IndicatorSuite:
    """Maintains incremental indicators for one (source_id, instrument_id, timeframe) stream."""

    def __init__(
        self,
        source_id: str,
        instrument_id: str,
        timeframe: str,
        config: IndicatorSuiteConfig | None = None,
    ) -> None:
        self.source_id = source_id
        self.instrument_id = instrument_id
        self.timeframe = timeframe
        self.config = config or IndicatorSuiteConfig()
        self.reset()

    def reset(self) -> None:
        self._ema_fast = IncrementalEMA(self.config.ema_fast_period)
        self._ema_slow = IncrementalEMA(self.config.ema_slow_period)
        self._rsi = IncrementalRSI(self.config.rsi_period)
        self._atr = IncrementalATR(self.config.atr_period)
        self._donchian = IncrementalDonchian(self.config.donchian_period)
        self._bollinger = IncrementalBollinger(self.config.bollinger_period, self.config.bollinger_std)

        self.ema_fast_series: list[ScalarPoint] = []
        self.ema_slow_series: list[ScalarPoint] = []
        self.rsi_series: list[ScalarPoint] = []
        self.atr_series: list[ScalarPoint] = []
        self.donchian_series: list[DonchianPoint] = []
        self.bollinger_series: list[BollingerPoint] = []

    def update_closed(self, candle: Candle) -> None:
        if not candle.is_closed:
            raise ValueError("IndicatorSuite.update_closed requires a closed candle")
        self.ema_fast_series.append(self._ema_fast.update(candle))
        self.ema_slow_series.append(self._ema_slow.update(candle))
        self.rsi_series.append(self._rsi.update(candle))
        self.atr_series.append(self._atr.update(candle))
        self.donchian_series.append(self._donchian.update(candle))
        self.bollinger_series.append(self._bollinger.update(candle))

    def recompute_from_closed(self, candles: list[Candle]) -> None:
        """Full deterministic recomputation (used on history load or closed candle revision)."""
        self.reset()
        for c in sorted(candles, key=lambda x: x.open_time_utc):
            if c.is_closed:
                self.update_closed(c)

    def snapshot(self, *, open_candle: Candle | None = None, include_preview: bool = False) -> IndicatorSnapshot:
        ema_fast = list(self.ema_fast_series)
        ema_slow = list(self.ema_slow_series)
        rsi = list(self.rsi_series)
        atr = list(self.atr_series)
        donchian = list(self.donchian_series)
        bollinger = list(self.bollinger_series)

        if include_preview and open_candle is not None and not open_candle.is_closed:
            ema_fast.append(self._ema_fast.preview(open_candle))
            ema_slow.append(self._ema_slow.preview(open_candle))
            rsi.append(self._rsi.preview(open_candle))
            atr.append(self._atr.preview(open_candle))
            donchian.append(self._donchian.preview(open_candle))
            bollinger.append(self._bollinger.preview(open_candle))

        return IndicatorSnapshot(
            source_id=self.source_id,
            instrument_id=self.instrument_id,
            timeframe=self.timeframe,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            rsi=rsi,
            atr=atr,
            donchian=donchian,
            bollinger=bollinger,
        )
