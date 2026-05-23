from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
try:
    import asyncpg
except ImportError:  # pragma: no cover - allows tests to run without optional deps installed
    asyncpg = None
try:
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover - allows tests to run without optional deps installed
    Redis = None

from worker.cache import InMemoryMarketCache, MarketCache, RedisMarketCache
from worker.config import WorkerSettings
from worker.kalshi import KalshiCredentials, KalshiRestClient, KalshiWebSocketClient, SequenceGapError
from worker.normalizer import normalize_market, normalize_ws_ticker_message
from worker.repository import InMemoryMarketRepository, MarketRepository, PostgresMarketRepository


class IngestionService:
    POLL_INTERVAL_SECONDS = 30
    STALENESS_CHECK_INTERVAL_SECONDS = 60
    STALENESS_THRESHOLD_SECONDS = 120

    def __init__(
        self,
        *,
        tickers: list[str],
        settings: WorkerSettings,
        kalshi_client: KalshiRestClient,
        repository: MarketRepository,
        cache: MarketCache,
        http_client: httpx.AsyncClient | None = None,
        websocket_client: KalshiWebSocketClient | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._tickers = tickers
        self._settings = settings
        self._kalshi_client = kalshi_client
        self._repository = repository
        self._cache = cache
        self._http_client = http_client
        self._logger = logger or logging.getLogger(__name__)
        self._running = False
        self._using_websocket = False
        self._last_update_at: dict[str, datetime] = {}
        self._stale_alerted_tickers: set[str] = set()
        self._websocket_client = websocket_client or KalshiWebSocketClient(
            tickers=tickers,
            on_market_update=self._handle_websocket_message,
            on_disconnect=self._handle_websocket_disconnect,
            on_reconnect=self._handle_websocket_reconnect,
            credentials=KalshiCredentials.from_env(),
            logger_=self._logger,
        )

    async def run(self) -> None:
        self._running = True
        self._logger.info("Starting watched ticker catalog sync")
        await self._sync_catalog()
        self._logger.info("Catalog sync completed; starting websocket and background loops")
        self._using_websocket = True
        await asyncio.gather(
            self._run_websocket_loop(),
            self._rest_fallback_loop(),
            self._staleness_loop(),
        )

    def stop(self) -> None:
        self._running = False
        self._websocket_client.stop()

    async def close(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
        await self._cache.close()
        await self._repository.close()

    async def persist_market_catalog(self, raw_market: dict[str, Any]) -> MarketState:
        market = normalize_market(raw_market, source="rest_snapshot")
        await self._repository.upsert_catalog(raw_market, market)
        self._last_update_at[market.ticker] = market.timestamp
        return market

    async def emit_alert(self, event_type: str, payload: dict[str, Any]) -> None:
        self._logger.warning("%s: %s", event_type, payload)
        await self._repository.insert_system_event(event_type, payload)

    async def _sync_catalog(self) -> None:
        if not self._tickers:
            return
        markets = await self._kalshi_client.get_markets(self._tickers)
        self._logger.info("Catalog sync fetched %d watched markets", len(markets))
        for raw_market in markets:
            await self.persist_market_catalog(raw_market)

    async def _run_websocket_loop(self) -> None:
        while self._running:
            try:
                await self._websocket_client.run()
                break
            except SequenceGapError as exc:
                await self._reconcile_via_rest([exc.ticker])
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("Kalshi websocket loop failed")
                self._using_websocket = False
                await self._sleep_while_running(1)

    async def _rest_fallback_loop(self) -> None:
        while self._running:
            if self._using_websocket:
                await self._sleep_while_running(1)
                continue
            try:
                await self._reconcile_via_rest(self._tickers)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("REST fallback reconcile failed")
            await self._sleep_while_running(self.POLL_INTERVAL_SECONDS)

    async def _staleness_loop(self) -> None:
        while self._running:
            now = datetime.now(timezone.utc)
            for ticker in self._tickers:
                last_updated = self._last_update_at.get(ticker)
                if last_updated is None:
                    continue
                age_seconds = (now - last_updated.astimezone(timezone.utc)).total_seconds()
                if age_seconds > self.STALENESS_THRESHOLD_SECONDS and ticker not in self._stale_alerted_tickers:
                    self._stale_alerted_tickers.add(ticker)
                    await self.emit_alert(
                        "stale_market",
                        {
                            "ticker": ticker,
                            "last_updated_at": last_updated.isoformat(),
                            "age_seconds": age_seconds,
                            "threshold_seconds": self.STALENESS_THRESHOLD_SECONDS,
                        },
                    )
            await self._sleep_while_running(self.STALENESS_CHECK_INTERVAL_SECONDS)

    async def _handle_websocket_message(self, message: dict[str, Any]) -> None:
        market = normalize_ws_ticker_message(message)
        if market is None:
            self._logger.info(
                "Observed websocket message type=%s keys=%s",
                message.get("type"),
                sorted(message.keys()),
            )
            return
        await self._repository.insert_snapshot(market)
        await self._cache.set_market_state(market)
        self._last_update_at[market.ticker] = market.timestamp
        self._stale_alerted_tickers.discard(market.ticker)
        self._logger.info(
            "Processed websocket update for %s seq=%s",
            market.ticker,
            market.raw_sequence,
        )

    async def _handle_websocket_disconnect(self) -> None:
        self._using_websocket = False
        self._logger.warning("Kalshi websocket disconnected; enabling REST fallback")
        await self.emit_alert(
            "ws_disconnect",
            {
                "tickers": self._tickers,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def _handle_websocket_reconnect(self) -> None:
        self._logger.info("Kalshi websocket reconnected; reconciling watched tickers via REST")
        await self._reconcile_via_rest(self._tickers)
        self._using_websocket = True
        await self.emit_alert(
            "ws_reconnect",
            {
                "tickers": self._tickers,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def _reconcile_via_rest(self, tickers: list[str]) -> None:
        self._logger.info("Reconciling %d tickers via REST", len(tickers))
        markets = await self._kalshi_client.get_markets(tickers)
        self._logger.info("REST reconcile fetched %d markets", len(markets))
        for raw_market in markets:
            market = normalize_market(raw_market, source="rest_snapshot")
            await self._repository.upsert_catalog(raw_market, market)
            await self._repository.insert_snapshot(market)
            await self._cache.set_market_state(market)
            self._last_update_at[market.ticker] = market.timestamp
            self._stale_alerted_tickers.discard(market.ticker)

    async def _sleep_while_running(self, seconds: float) -> None:
        remaining = seconds
        while self._running and remaining > 0:
            interval = min(1.0, remaining)
            await asyncio.sleep(interval)
            remaining -= interval


IngestionRuntime = IngestionService


async def build_runtime(
    *,
    settings: WorkerSettings,
    watched_tickers: list[str],
) -> IngestionService:
    http_client = httpx.AsyncClient(timeout=10.0)
    kalshi_client = KalshiRestClient(http_client=http_client, base_url=settings.kalshi_base_url)

    repository: MarketRepository
    if settings.database_url:
        if asyncpg is None:
            raise RuntimeError("asyncpg is required when DATABASE_URL is configured")
        pool = await asyncpg.create_pool(settings.database_url)
        repository = PostgresMarketRepository(pool)
    else:
        repository = InMemoryMarketRepository()

    cache: MarketCache
    if settings.redis_url:
        if Redis is None:
            raise RuntimeError("redis is required when REDIS_URL is configured")
        cache = RedisMarketCache(Redis.from_url(settings.redis_url, decode_responses=True))
    else:
        cache = InMemoryMarketCache()

    return IngestionService(
        tickers=watched_tickers,
        settings=settings,
        kalshi_client=kalshi_client,
        repository=repository,
        cache=cache,
        http_client=http_client,
    )
