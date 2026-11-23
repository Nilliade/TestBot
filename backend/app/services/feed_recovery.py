"""Upstream feed reconnect & history recovery coordinator (SPEC §§3-6, Gate A3).

Strictly read-only: only history candle queries and quote/candle stream consumption.
No trading methods exist on UpstreamReadOnlyFeed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from backend.app.domain.candle import Candle
from backend.app.domain.quote import Quote
from backend.app.services.market_hub import MarketHub

UTC = timezone.utc


class UpstreamReadOnlyFeed(Protocol):
    """Read-only upstream market data interface (allowlist: history + stream only)."""

    source_id: str

    async def fetch_history_candles(
        self,
        instrument_id: str,
        timeframe: str,
        since_utc: datetime | None,
        until_utc: datetime | None = None,
    ) -> tuple[list[Candle], bool]:
        """Return `(closed_candles, is_authoritative)`.

        When `is_authoritative=True`, missing bars in an open session are tagged
        `CONFIRMED_GAP`; when `False`, `POSSIBLE_GAP`.
        """
        ...


class UpstreamFeedCoordinator:
    """Coordinates stream events, disconnect handling, reconnect & deduplicated history backfill."""

    def __init__(self, hub: MarketHub, upstream: UpstreamReadOnlyFeed) -> None:
        self.hub = hub
        self.upstream = upstream
        self.source_id = upstream.source_id
        self.connected: bool = False
        self.reconnect_count: int = 0

    async def connect_and_backfill(
        self,
        instrument_id: str,
        timeframe: str,
        *,
        now_utc: datetime | None = None,
    ) -> int:
        """Initial connection + history load. Returns count of newly stored closed candles."""
        now = now_utc or datetime.now(UTC)
        self.connected = True
        added = await self._backfill(instrument_id, timeframe, since_utc=None, until_utc=now, now_utc=now)
        return added

    def on_disconnect(self, instrument_id: str | None = None, *, now_utc: datetime | None = None) -> None:
        """Mark source and instrument(s) OFFLINE upon stream break."""
        now = now_utc or datetime.now(UTC)
        self.connected = False
        self.hub.update_feed_health(
            self.source_id,
            None,
            connected=False,
            now_utc=now,
            reconnect_count=self.reconnect_count,
            subscription_state="disconnected",
        )
        if instrument_id is not None:
            self.hub.update_feed_health(
                self.source_id,
                instrument_id,
                connected=False,
                now_utc=now,
                reconnect_count=self.reconnect_count,
                subscription_state="disconnected",
            )

    async def reconnect_and_recover(
        self,
        instrument_id: str,
        timeframe: str,
        *,
        now_utc: datetime | None = None,
    ) -> int:
        """Reconnect after break, increment reconnect_count, and backfill missing history without dupes."""
        now = now_utc or datetime.now(UTC)
        self.reconnect_count += 1
        self.connected = True

        closed, _ = self.hub.get_candles(self.source_id, instrument_id, timeframe)
        # Include 1-bar overlap (`closed[-1].open_time_utc`) to verify idempotent deduplication.
        since_utc = closed[-1].open_time_utc if closed else None
        added = await self._backfill(instrument_id, timeframe, since_utc=since_utc, until_utc=now, now_utc=now)
        return added

    async def _backfill(
        self,
        instrument_id: str,
        timeframe: str,
        *,
        since_utc: datetime | None,
        until_utc: datetime | None,
        now_utc: datetime,
    ) -> int:
        try:
            candles, is_authoritative = await self.upstream.fetch_history_candles(
                instrument_id,
                timeframe,
                since_utc=since_utc,
                until_utc=until_utc,
            )
        except Exception:
            # History recovery failed -> mark feed DEGRADED (unconfirmed gap state).
            self.hub.update_feed_health(
                self.source_id,
                instrument_id,
                connected=self.connected,
                now_utc=now_utc,
                reconnect_count=self.reconnect_count,
                degraded=True,
                subscription_state="Subscribed (history recovery failed)",
            )
            self.hub.update_feed_health(
                self.source_id,
                None,
                connected=self.connected,
                now_utc=now_utc,
                reconnect_count=self.reconnect_count,
                degraded=True,
                subscription_state="degraded",
            )
            return 0

        added = 0
        has_possible_gap = False
        for idx, c in enumerate(sorted(candles, key=lambda x: x.open_time_utc)):
            # If history is not authoritative, any gap on the first recovered candle after outage is POSSIBLE_GAP.
            res = self.hub.publish_candle(c, gap_confirmed_by_source=is_authoritative)
            if res.action == "closed":
                added += 1
            if "POSSIBLE_GAP" in res.candle.quality_flags or (not is_authoritative and idx == 0 and since_utc is not None):
                has_possible_gap = "POSSIBLE_GAP" in res.candle.quality_flags

        self.hub.flush_closed_candles()

        self.hub.update_feed_health(
            self.source_id,
            instrument_id,
            connected=self.connected,
            now_utc=now_utc,
            last_quote_age_ms=0,
            last_heartbeat_age_ms=0,
            reconnect_count=self.reconnect_count,
            degraded=has_possible_gap,
            subscription_state="subscribed",
        )
        self.hub.update_feed_health(
            self.source_id,
            None,
            connected=self.connected,
            now_utc=now_utc,
            last_quote_age_ms=0,
            last_heartbeat_age_ms=0,
            reconnect_count=self.reconnect_count,
            degraded=has_possible_gap,
            subscription_state="subscribed",
        )
        return added

    def on_quote(self, quote: Quote, *, now_utc: datetime | None = None) -> None:
        if not self.connected:
            return
        now = now_utc or quote.received_ts_utc
        self.hub.publish_quote(quote)
        age_ms = max(0, int((now - (quote.exchange_ts_utc or quote.received_ts_utc)).total_seconds() * 1000))
        self.hub.update_feed_health(
            self.source_id,
            quote.instrument_id,
            connected=True,
            now_utc=now,
            last_quote_age_ms=age_ms,
            last_heartbeat_age_ms=0,
            reconnect_count=self.reconnect_count,
            subscription_state="subscribed",
        )

    def on_candle(
        self,
        candle: Candle,
        *,
        gap_confirmed_by_source: bool = False,
        now_utc: datetime | None = None,
    ) -> None:
        if not self.connected:
            return
        now = now_utc or candle.received_at_utc
        res = self.hub.publish_candle(candle, gap_confirmed_by_source=gap_confirmed_by_source)
        degraded = "POSSIBLE_GAP" in res.candle.quality_flags
        self.hub.update_feed_health(
            self.source_id,
            candle.instrument_id,
            connected=True,
            now_utc=now,
            last_quote_age_ms=0,
            last_heartbeat_age_ms=0,
            reconnect_count=self.reconnect_count,
            degraded=degraded,
            subscription_state="subscribed",
        )
