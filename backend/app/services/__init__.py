"""Stage 1 services package (Step 1.2: CandleBuilder, MarketHub, UpstreamFeedCoordinator)."""

from backend.app.services.candle_builder import (
    CandleSeriesBuilder,
    GapEvent,
    IngestResult,
    bucket_bounds_utc,
    classify_gap,
    timeframe_delta,
)
from backend.app.services.feed_recovery import UpstreamFeedCoordinator, UpstreamReadOnlyFeed
from backend.app.services.market_hub import ClientStream, MarketHub

__all__ = [
    "CandleSeriesBuilder",
    "ClientStream",
    "GapEvent",
    "IngestResult",
    "MarketHub",
    "UpstreamFeedCoordinator",
    "UpstreamReadOnlyFeed",
    "bucket_bounds_utc",
    "classify_gap",
    "timeframe_delta",
]
