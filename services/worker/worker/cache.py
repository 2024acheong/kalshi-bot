from __future__ import annotations

import json
from datetime import timezone
from decimal import Decimal
from typing import Any, Protocol

try:
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover - allows tests to run without optional deps installed
    Redis = Any  # type: ignore[misc,assignment]

from core.schemas.market import MarketState


class MarketCache(Protocol):
    async def set_market_state(self, market: MarketState) -> None: ...

    async def close(self) -> None: ...


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    return value


def serialize_market_state(market: MarketState) -> str:
    payload = {
        "ticker": market.ticker,
        "timestamp": market.timestamp.astimezone(timezone.utc).isoformat(),
        "yes_bid": market.yes_bid,
        "yes_ask": market.yes_ask,
        "yes_bid_size": market.yes_bid_size,
        "yes_ask_size": market.yes_ask_size,
        "no_bid": market.no_bid,
        "no_ask": market.no_ask,
        "no_bid_size": market.no_bid_size,
        "no_ask_size": market.no_ask_size,
        "last_price": market.last_price,
        "volume_24h": market.volume_24h,
        "open_interest": market.open_interest,
        "close_time": market.close_time.astimezone(timezone.utc).isoformat()
        if market.close_time
        else None,
        "status": market.status.value,
        "source": market.source,
        "raw_sequence": market.raw_sequence,
    }
    return json.dumps(payload, default=_json_default, sort_keys=True)


class RedisMarketCache:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def set_market_state(self, market: MarketState) -> None:
        await self._redis.set(f"market:{market.ticker}:state", serialize_market_state(market))

    async def close(self) -> None:
        await self._redis.aclose()


class InMemoryMarketCache:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def set_market_state(self, market: MarketState) -> None:
        self.data[f"market:{market.ticker}:state"] = serialize_market_state(market)

    async def close(self) -> None:
        return None
