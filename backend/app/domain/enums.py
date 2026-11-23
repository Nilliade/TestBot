"""Closed enums for Stage 1 contracts."""

from enum import Enum


class MarketType(str, Enum):
    SPOT = "spot"
    CFD = "cfd"


class PriceBasis(str, Enum):
    BID = "bid"
    ASK = "ask"
    MID = "mid"
    LAST = "last"
    UNKNOWN = "unknown"


class VolumeType(str, Enum):
    EXCHANGE = "exchange"
    TICK = "tick"
    NONE = "none"


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class FeedStatus(str, Enum):
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    MARKET_CLOSED = "MARKET_CLOSED"
    OFFLINE = "OFFLINE"
    RATE_LIMITED = "RATE_LIMITED"


ALLOWED_TIMEFRAMES = {t.value for t in Timeframe}
