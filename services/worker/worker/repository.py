from __future__ import annotations

import json
from typing import Any, Protocol

try:
    import asyncpg
except ImportError:  # pragma: no cover - allows tests to run without optional deps installed
    asyncpg = Any  # type: ignore[assignment]

from core.schemas.market import MarketState
from worker.normalizer import market_catalog_row, market_snapshot_row


class MarketRepository(Protocol):
    async def upsert_catalog(self, raw_market: dict[str, Any], market: MarketState) -> None: ...

    async def insert_snapshot(self, market: MarketState) -> None: ...

    async def insert_system_event(self, event_type: str, payload: dict[str, Any]) -> None: ...

    async def close(self) -> None: ...


class PostgresMarketRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert_catalog(self, raw_market: dict[str, Any], market: MarketState) -> None:
        row = market_catalog_row(raw_market, market)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO market_catalog (
                    ticker, title, category, close_time, status, synced_at
                )
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (ticker) DO UPDATE SET
                    title = EXCLUDED.title,
                    category = EXCLUDED.category,
                    close_time = EXCLUDED.close_time,
                    status = EXCLUDED.status,
                    synced_at = EXCLUDED.synced_at
                """,
                row["ticker"],
                row["title"],
                row["category"],
                row["close_time"],
                row["status"],
                row["synced_at"],
            )

    async def insert_snapshot(self, market: MarketState) -> None:
        row = market_snapshot_row(market)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO market_snapshots (
                    ticker, timestamp, yes_bid, yes_ask, yes_bid_size,
                    yes_ask_size, no_bid, no_ask, no_bid_size, no_ask_size,
                    last_price, volume_24h, open_interest, source, raw_sequence
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                """,
                row["ticker"],
                row["timestamp"],
                row["yes_bid"],
                row["yes_ask"],
                row["yes_bid_size"],
                row["yes_ask_size"],
                row["no_bid"],
                row["no_ask"],
                row["no_bid_size"],
                row["no_ask_size"],
                row["last_price"],
                row["volume_24h"],
                row["open_interest"],
                row["source"],
                row["raw_sequence"],
            )

    async def insert_system_event(self, event_type: str, payload: dict[str, Any]) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO system_events (event_type, payload_json, created_at)
                VALUES ($1, $2::jsonb, NOW())
                """,
                event_type,
                json.dumps(payload),
            )

    async def close(self) -> None:
        await self._pool.close()


class InMemoryMarketRepository:
    def __init__(self) -> None:
        self.catalog_rows: list[dict[str, Any]] = []
        self.snapshot_rows: list[dict[str, Any]] = []
        self.system_events: list[dict[str, Any]] = []

    async def upsert_catalog(self, raw_market: dict[str, Any], market: MarketState) -> None:
        row = market_catalog_row(raw_market, market)
        self.catalog_rows = [
            existing
            for existing in self.catalog_rows
            if existing["ticker"] != row["ticker"]
        ]
        self.catalog_rows.append(row)

    async def insert_snapshot(self, market: MarketState) -> None:
        self.snapshot_rows.append(market_snapshot_row(market))

    async def insert_system_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.system_events.append({"event_type": event_type, "payload": payload})

    async def close(self) -> None:
        return None
