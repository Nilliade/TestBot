"""WebSocket message models (SPEC §6: snapshot -> updates -> resync).

sequence_id is assigned AFTER coalescing, immediately before sending.
Each reconnect starts a new stream_epoch.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.domain.candle import Candle
from backend.app.domain.feed_health import FeedHealth
from backend.app.domain.quote import Quote

WS_SCHEMA_VERSION = "1.0"


class SnapshotMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = WS_SCHEMA_VERSION
    type: Literal["snapshot"] = "snapshot"
    stream_epoch: str
    sequence_id: int
    quotes: list[Quote] = Field(default_factory=list)
    candles: list[Candle] = Field(default_factory=list)
    feed_health: list[FeedHealth] = Field(default_factory=list)


class UpdateMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = WS_SCHEMA_VERSION
    type: Literal["update"] = "update"
    stream_epoch: str
    sequence_id: int
    kind: Literal["quote", "candle_close", "revision", "feed_health"]
    quote: Quote | None = None
    candle: Candle | None = None
    feed_health: FeedHealth | None = None


class ResyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = WS_SCHEMA_VERSION
    type: Literal["resync_request"] = "resync_request"
    stream_epoch: str
    last_sequence_id: int
