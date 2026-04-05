from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import asyncpg
import httpx
from redis.asyncio import Redis

from core.schemas import MarketState
from worker.cache import InMemoryMarketCache, MarketCache, RedisMarketCache
from worker.config import WorkerSettings
from worker.kalshi import KalshiRestClient
from worker.monitoring import AlertEvent, MarketMonitor
from worker.normalization import normalize_market
from worker.repository import InMemoryMarketRepository, MarketRepository, PostgresMarketRepository


class IngestionRuntime:
    def __init__(
        self,
        *,
        settings: WorkerSettings,
        watched_tickers: list[str],
        kalshi_client: KalshiRestClient,
        repository: MarketRepository,
        cache: MarketCache,
        monitor: MarketMonitor,
        http_client: httpx.AsyncClient | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._watched_tickers = watched_tickers
        self._kalshi_client = kalshi_client
        self._repository = repository
        self._cache = cache
        self._monitor = monitor
        self._http_client = http_client
        self._logger = logger or logging.getLogger(__name__)
        self._running = False

    async def poll_once(self) -> tuple[list[MarketState], list[AlertEvent]]:
        now = datetime.now(timezone.utc)
        alerts = self._monitor.on_poll_started(now)

        raw_markets = await self._kalshi_client.get_markets(self._watched_tickers)
        found_tickers = {str(market.get("ticker")) for market in raw_markets}
        missing = sorted(set(self._watched_tickers) - found_tickers)
        for ticker in missing:
            alerts.append(
                AlertEvent(
                    level="warning",
                    code="missing_market",
                    ticker=ticker,
                    message=f"Watched ticker {ticker} was not returned by the Kalshi REST poll",
                )
            )

        normalized_markets: list[MarketState] = []
        for raw_market in raw_markets:
            market = normalize_market(raw_market, source="rest_poll")
            await self._repository.upsert_catalog(raw_market, market)
            await self._repository.insert_snapshot(market)
            await self._cache.set_market_state(market)
            normalized_markets.append(market)
            alerts.extend(self._monitor.evaluate_market(market, now))

        self._monitor.on_poll_completed(datetime.now(timezone.utc))
        self._monitor.emit(alerts)
        for alert in alerts:
            await self._repository.insert_system_event(
                alert.code,
                alert.as_payload(),
            )
        return normalized_markets, alerts

    async def run_forever(self) -> None:
        self._running = True
        self._logger.info(
            "Starting REST ingestion for %d watched markets at %.1fs intervals",
            len(self._watched_tickers),
            self._settings.poll_interval_seconds,
        )
        while self._running:
            try:
                markets, alerts = await self.poll_once()
                self._logger.info(
                    "Polled %d markets, emitted %d alerts",
                    len(markets),
                    len(alerts),
                )
            except Exception:
                self._logger.exception("REST ingestion poll failed")

            if self._running:
                await asyncio.sleep(self._settings.poll_interval_seconds)

    def stop(self) -> None:
        self._running = False

    async def close(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
        await self._cache.close()
        await self._repository.close()


async def build_runtime(
    *,
    settings: WorkerSettings,
    watched_tickers: list[str],
) -> IngestionRuntime:
    http_client = httpx.AsyncClient(timeout=10.0)
    kalshi_client = KalshiRestClient(http_client=http_client, base_url=settings.kalshi_base_url)

    repository: MarketRepository
    if settings.database_url:
        pool = await asyncpg.create_pool(settings.database_url)
        repository = PostgresMarketRepository(pool)
    else:
        repository = InMemoryMarketRepository()

    cache: MarketCache
    if settings.redis_url:
        cache = RedisMarketCache(Redis.from_url(settings.redis_url, decode_responses=True))
    else:
        cache = InMemoryMarketCache()

    runtime = IngestionRuntime(
        settings=settings,
        watched_tickers=watched_tickers,
        kalshi_client=kalshi_client,
        repository=repository,
        cache=cache,
        monitor=MarketMonitor(
            poll_interval_seconds=settings.poll_interval_seconds,
            staleness_threshold_seconds=settings.staleness_threshold_seconds,
            gap_alert_factor=settings.gap_alert_factor,
        ),
        http_client=http_client,
    )
    return runtime
