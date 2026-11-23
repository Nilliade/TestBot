# Services Package (Stage 1, Step 1.2 — IMPLEMENTED)

Implements:
- `candle_builder.py`: `CandleSeriesBuilder`, `[open_time_utc, close_time_utc)` UTC bucket geometry, `CONFIRMED_GAP` vs `POSSIBLE_GAP` classification without synthetic bar interpolation, duplicate deduplication, and closed-candle revision handling (`revision` increment + `REVISED` flag).
- `market_hub.py`: `MarketHub` and `ClientStream` (`snapshot -> updates -> resync`, bounded per-client queue, coalescing of `quote` and `open_candle` visual updates, post-coalesce `sequence_id`, overflow resync, and `FeedHealth` state machine).
- `feed_recovery.py`: `UpstreamFeedCoordinator` and `UpstreamReadOnlyFeed` protocol (disconnect, reconnect, deduplicated history backfill, and gap preservation).
