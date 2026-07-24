from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional


class MarketStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    RESOLVED = "resolved"


class RunMode(str, Enum):
    REPLAY = "replay"
    PAPER = "paper"
    LIVE = "live"


class RiskDecision(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    REDUCE_ONLY = "reduce_only"


class OrderIntentStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"


@dataclass
class MarketState:
    ticker: str
    timestamp: datetime
    yes_bid: Optional[Decimal]
    yes_ask: Optional[Decimal]
    yes_bid_size: Optional[int]
    yes_ask_size: Optional[int]
    last_price: Optional[Decimal]
    volume_24h: Optional[int]
    open_interest: Optional[int]
    close_time: Optional[datetime]
    status: MarketStatus
    source: str  # "websocket" | "rest_poll" | "rest_snapshot"
    raw_sequence: Optional[int] = None
    no_bid: Optional[Decimal] = None
    no_ask: Optional[Decimal] = None
    no_bid_size: Optional[int] = None
    no_ask_size: Optional[int] = None


@dataclass
class FeatureVector:
    ticker: str
    timestamp: datetime
    mid_price: Optional[float]
    spread_pct: Optional[float]
    spread_ticks: Optional[float]
    bid_ask_imbalance: Optional[float]
    time_to_close_hours: Optional[float]
    implied_probability: Optional[float]
    liquidity_score: Optional[float]
    price_momentum_1h: Optional[float]
    price_momentum_24h: Optional[float]
    volume_zscore: Optional[float]
    open_interest_delta: Optional[float]
