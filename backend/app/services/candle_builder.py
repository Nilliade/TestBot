"""CandleBuilder & Gap Detector (SPEC §3, §4, ACCEPTANCE A2, A3).

Invariants:
- Interval semantics: `[open_time_utc, close_time_utc)` in UTC.
- Never synthesizes zero-volume / interpolated candles in missing intervals.
- A confirmed closed session window (`SessionCalendar.is_interval_confirmed_closed`)
  is NEVER counted as a gap.
- Missing open-session intervals are tagged `CONFIRMED_GAP` only when confirmed
  by the source/history reload on a confirmed-open calendar; otherwise `POSSIBLE_GAP`.
- Primary key `(source_id, instrument_id, timeframe, open_time_utc)`:
  - Identical duplicate closed candle -> idempotent no-op (`action="duplicate"`).
  - Modified OHLCV on an existing closed candle -> increments `revision`,
    adds `"REVISED"` flag (`action="revised"`).
  - Unclosed candle (`is_closed=False`) is held in memory (`open_candle`) with
    `"PREVIEW"` flag and never persisted to Parquet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from backend.app.domain.candle import Candle
from backend.app.domain.enums import PriceBasis, VolumeType
from backend.app.domain.quote import Quote
from backend.app.sessions.calendar import SessionCalendar

UTC = timezone.utc

TIMEFRAME_DELTAS: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}


def timeframe_delta(timeframe: str) -> timedelta:
    if timeframe not in TIMEFRAME_DELTAS:
        raise ValueError(f"unsupported timeframe: {timeframe!r}")
    return TIMEFRAME_DELTAS[timeframe]


def bucket_bounds_utc(ts_utc: datetime, timeframe: str) -> tuple[datetime, datetime]:
    """Return `[open_time_utc, close_time_utc)` UTC bucket containing `ts_utc`."""
    if ts_utc.tzinfo is None or ts_utc.utcoffset() != UTC.utcoffset(ts_utc):
        raise ValueError("ts_utc must be timezone-aware UTC")
    delta = timeframe_delta(timeframe)
    step_sec = int(delta.total_seconds())
    epoch_sec = int(ts_utc.timestamp())
    open_sec = (epoch_sec // step_sec) * step_sec
    open_dt = datetime.fromtimestamp(open_sec, tz=UTC)
    return open_dt, open_dt + delta


@dataclass(frozen=True)
class GapEvent:
    source_id: str
    instrument_id: str
    timeframe: str
    kind: Literal["CONFIRMED_GAP", "POSSIBLE_GAP"]
    missing_from_utc: datetime
    missing_until_utc: datetime
    missing_bars: int


@dataclass
class IngestResult:
    action: Literal["open_updated", "closed", "duplicate", "revised"]
    candle: Candle
    closed_previous: Candle | None = None
    gap_event: GapEvent | None = None


def classify_gap(
    prev_closed: Candle,
    curr: Candle,
    *,
    calendar: SessionCalendar | None = None,
    gap_confirmed_by_source: bool = False,
) -> GapEvent | None:
    """Inspect `[prev_closed.close_time_utc, curr.open_time_utc)` without synthesizing bars."""
    if curr.open_time_utc <= prev_closed.close_time_utc:
        return None

    step = timeframe_delta(curr.timeframe)
    cursor = prev_closed.close_time_utc
    missing_open_bars = 0
    all_open_bars_confirmed_open = True
    first_missing_open: datetime | None = None
    last_missing_close: datetime | None = None

    while cursor < curr.open_time_utc:
        bar_end = min(cursor + step, curr.open_time_utc)
        if calendar is not None and calendar.is_interval_confirmed_closed(cursor, bar_end):
            # Confirmed market close (e.g. confirmed Forex weekend): not a gap.
            cursor = bar_end
            continue

        missing_open_bars += 1
        if first_missing_open is None:
            first_missing_open = cursor
        last_missing_close = bar_end

        if calendar is not None and not calendar.is_confirmed_open(cursor):
            all_open_bars_confirmed_open = False

        cursor = bar_end

    if missing_open_bars == 0 or first_missing_open is None or last_missing_close is None:
        return None

    if gap_confirmed_by_source and all_open_bars_confirmed_open:
        kind: Literal["CONFIRMED_GAP", "POSSIBLE_GAP"] = "CONFIRMED_GAP"
    else:
        kind = "POSSIBLE_GAP"

    return GapEvent(
        source_id=curr.source_id,
        instrument_id=curr.instrument_id,
        timeframe=curr.timeframe,
        kind=kind,
        missing_from_utc=first_missing_open,
        missing_until_utc=last_missing_close,
        missing_bars=missing_open_bars,
    )


def _same_ohlcv(a: Candle, b: Candle) -> bool:
    return (
        a.open == b.open
        and a.high == b.high
        and a.low == b.low
        and a.close == b.close
        and a.volume == b.volume
        and a.price_basis == b.price_basis
        and a.volume_type == b.volume_type
    )


class CandleSeriesBuilder:
    """Manages one `(source_id, instrument_id, timeframe)` candle series in memory."""

    def __init__(
        self,
        source_id: str,
        instrument_id: str,
        timeframe: str,
        *,
        price_basis: PriceBasis = PriceBasis.LAST,
        volume_type: VolumeType = VolumeType.EXCHANGE,
        calendar: SessionCalendar | None = None,
    ) -> None:
        timeframe_delta(timeframe)
        self.source_id = source_id
        self.instrument_id = instrument_id
        self.timeframe = timeframe
        self.price_basis = price_basis
        self.volume_type = volume_type
        self.calendar = calendar

        self._closed: dict[datetime, Candle] = {}
        self.open_candle: Candle | None = None
        self.gaps: list[GapEvent] = []

    def closed_candles(self) -> list[Candle]:
        return [self._closed[t] for t in sorted(self._closed)]

    def get_closed(self, open_time_utc: datetime) -> Candle | None:
        return self._closed.get(open_time_utc)

    def ingest_candle(
        self,
        candle: Candle,
        *,
        gap_confirmed_by_source: bool = False,
    ) -> IngestResult:
        if (
            candle.source_id != self.source_id
            or candle.instrument_id != self.instrument_id
            or candle.timeframe != self.timeframe
        ):
            raise ValueError("Candle key does not match CandleSeriesBuilder key")

        if not candle.is_closed:
            flags = set(candle.quality_flags) | {"PREVIEW"}
            preview_candle = candle.model_copy(update={"quality_flags": flags})
            self.open_candle = preview_candle
            return IngestResult(action="open_updated", candle=preview_candle)

        # Clear open_candle if it belonged to this or an earlier bucket.
        if self.open_candle is not None and self.open_candle.open_time_utc <= candle.open_time_utc:
            self.open_candle = None

        existing = self._closed.get(candle.open_time_utc)
        if existing is not None:
            new_flags = set(candle.quality_flags) - {"PREVIEW"}
            old_flags = set(existing.quality_flags) - {"PREVIEW", "REVISED"}
            if _same_ohlcv(existing, candle) and new_flags == old_flags and candle.revision <= existing.revision:
                return IngestResult(action="duplicate", candle=existing)

            # Provider revision / correction of a closed candle.
            next_rev = max(existing.revision + 1, candle.revision)
            merged_flags = (set(existing.quality_flags) | set(candle.quality_flags) | {"REVISED"}) - {"PREVIEW"}
            revised = candle.model_copy(
                update={
                    "revision": next_rev,
                    "quality_flags": merged_flags,
                }
            )
            self._closed[candle.open_time_utc] = revised
            return IngestResult(action="revised", candle=revised)

        # New closed candle: check gap against immediate predecessor.
        prior_times = [t for t in self._closed if t < candle.open_time_utc]
        gap_event: GapEvent | None = None
        flags = set(candle.quality_flags) - {"PREVIEW"}

        if prior_times:
            prev_t = max(prior_times)
            prev_candle = self._closed[prev_t]
            if "CONFIRMED_GAP" in flags:
                gap_event = classify_gap(
                    prev_candle,
                    candle,
                    calendar=self.calendar,
                    gap_confirmed_by_source=True,
                )
            elif "POSSIBLE_GAP" in flags:
                gap_event = classify_gap(
                    prev_candle,
                    candle,
                    calendar=self.calendar,
                    gap_confirmed_by_source=False,
                )
            else:
                gap_event = classify_gap(
                    prev_candle,
                    candle,
                    calendar=self.calendar,
                    gap_confirmed_by_source=gap_confirmed_by_source,
                )
                if gap_event is not None:
                    flags.add(gap_event.kind)

        stored = candle.model_copy(update={"quality_flags": flags})
        self._closed[stored.open_time_utc] = stored
        if gap_event is not None:
            self.gaps.append(gap_event)

        # Also check if a successor already exists (out-of-order history fill):
        # if this new bar filled a previously flagged gap on the successor, recompute successor gap flag.
        later_times = [t for t in self._closed if t > stored.open_time_utc]
        if later_times:
            next_t = min(later_times)
            next_c = self._closed[next_t]
            if stored.close_time_utc == next_c.open_time_utc and (
                "CONFIRMED_GAP" in next_c.quality_flags or "POSSIBLE_GAP" in next_c.quality_flags
            ):
                cleaned_flags = set(next_c.quality_flags) - {"CONFIRMED_GAP", "POSSIBLE_GAP"}
                self._closed[next_t] = next_c.model_copy(update={"quality_flags": cleaned_flags})

        return IngestResult(action="closed", candle=stored, gap_event=gap_event)

    def ingest_quote(
        self,
        quote: Quote,
        *,
        gap_confirmed_by_source: bool = False,
    ) -> IngestResult | None:
        """Update current open bar from a Quote (or close previous open bar on bucket rollover).

        Never synthesizes bars for quiet intervals without quotes.
        """
        price: Decimal | None = None
        if self.price_basis == PriceBasis.LAST:
            price = quote.last
        elif self.price_basis == PriceBasis.MID:
            price = quote.mid
        elif self.price_basis == PriceBasis.BID:
            price = quote.bid
        elif self.price_basis == PriceBasis.ASK:
            price = quote.ask

        if price is None:
            price = quote.last or quote.mid or quote.bid or quote.ask
        if price is None:
            return None

        ts = quote.exchange_ts_utc or quote.received_ts_utc
        open_t, close_t = bucket_bounds_utc(ts, self.timeframe)

        closed_prev: Candle | None = None
        gap_ev: GapEvent | None = None

        if self.open_candle is not None and open_t >= self.open_candle.close_time_utc:
            # Close the previous open candle at rollover.
            closing = self.open_candle.model_copy(
                update={
                    "is_closed": True,
                    "quality_flags": (set(self.open_candle.quality_flags) - {"PREVIEW"}) | {"UNCONFIRMED"},
                    "updated_at_utc": quote.received_ts_utc,
                }
            )
            res = self.ingest_candle(closing, gap_confirmed_by_source=gap_confirmed_by_source)
            closed_prev = res.candle
            gap_ev = res.gap_event

        if self.open_candle is None or self.open_candle.open_time_utc != open_t:
            new_open = Candle(
                source_id=self.source_id,
                instrument_id=self.instrument_id,
                timeframe=self.timeframe,
                open_time_utc=open_t,
                close_time_utc=close_t,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Decimal("1") if self.volume_type == VolumeType.TICK else None,
                volume_type=self.volume_type,
                price_basis=self.price_basis,
                is_closed=False,
                quality_flags={"PREVIEW", "UNCONFIRMED"},
                revision=1,
                received_at_utc=quote.received_ts_utc,
                updated_at_utc=quote.received_ts_utc,
            )
            self.open_candle = new_open
        else:
            cur = self.open_candle
            next_vol: Decimal | None = cur.volume
            if self.volume_type == VolumeType.TICK:
                next_vol = (cur.volume or Decimal("0")) + Decimal("1")
            self.open_candle = cur.model_copy(
                update={
                    "high": max(cur.high, price),
                    "low": min(cur.low, price),
                    "close": price,
                    "volume": next_vol,
                    "updated_at_utc": quote.received_ts_utc,
                }
            )

        return IngestResult(
            action="open_updated",
            candle=self.open_candle,
            closed_previous=closed_prev,
            gap_event=gap_ev,
        )
