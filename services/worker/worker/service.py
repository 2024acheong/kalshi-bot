from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable

import httpx
try:
    import asyncpg
except ImportError:  # pragma: no cover - allows tests to run without optional deps installed
    asyncpg = None
try:
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover - allows tests to run without optional deps installed
    Redis = None

from core.schemas.market import MarketState, MarketStatus
from worker.cache import InMemoryMarketCache, MarketCache, RedisMarketCache
from worker.config import WorkerSettings
from worker.kalshi import (
    KalshiCredentials,
    KalshiRestClient,
    KalshiWebSocketClient,
    SequenceGapError,
)
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
        on_market_update: Callable[[MarketState], Awaitable[None]] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._tickers = tickers
        self._settings = settings
        self._kalshi_client = kalshi_client
        self._repository = repository
        self._cache = cache
        self._http_client = http_client
        self._logger = logger or logging.getLogger(__name__)
        self._on_market_update = on_market_update
        self._running = False
        self._using_websocket = False
        self._last_update_at: dict[str, datetime] = {}
        self._stale_alerted_tickers: set[str] = set()
        self._orderbooks: dict[str, dict[str, dict[int, int]]] = {}
        self._catalog_markets: dict[str, MarketState] = {}
        self._websocket_client = websocket_client or KalshiWebSocketClient(
            tickers=tickers,
            on_market_update=self._handle_websocket_message,
            on_disconnect=self._handle_websocket_disconnect,
            on_reconnect=self._handle_websocket_reconnect,
            credentials=KalshiCredentials.from_env(),
            logger_=self._logger,
        )

    async def run(self) -> None:
        if not self._tickers:
            self._logger.warning("No watched tickers configured; ingestion loops will not start")
            self._running = False
            return

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
        self._catalog_markets[market.ticker] = market
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
                if (
                    age_seconds > self.STALENESS_THRESHOLD_SECONDS
                    and ticker not in self._stale_alerted_tickers
                ):
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
        if message.get("type") == "orderbook_snapshot":
            self._store_orderbook_snapshot(message)

        market = normalize_ws_ticker_message(message)
        if market is None and message.get("type") == "orderbook_delta":
            market = self._apply_orderbook_delta(message)

        if market is None:
            self._logger.info(
                "Observed websocket message type=%s keys=%s",
                message.get("type"),
                sorted(message.keys()),
            )
            return
        await self._process_market_update(market)
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
            self._catalog_markets[market.ticker] = market
            await self._process_market_update(market)

    async def _sleep_while_running(self, seconds: float) -> None:
        remaining = seconds
        while self._running and remaining > 0:
            interval = min(1.0, remaining)
            await asyncio.sleep(interval)
            remaining -= interval

    async def _process_market_update(self, market: MarketState) -> None:
        market = self._enrich_with_catalog(market)
        try:
            await self._repository.insert_snapshot(market)
        except Exception as exc:
            self._logger.warning(
                "Skipping market snapshot for %s after persistence error: %s",
                market.ticker,
                exc,
                exc_info=True,
            )
            return

        await self._cache.set_market_state(market)
        self._last_update_at[market.ticker] = market.timestamp
        self._stale_alerted_tickers.discard(market.ticker)
        await self._notify_market_update(market)

    def _enrich_with_catalog(self, market: MarketState) -> MarketState:
        catalog_market = self._catalog_markets.get(market.ticker)
        if catalog_market is None:
            return market

        return MarketState(
            ticker=market.ticker,
            timestamp=market.timestamp,
            yes_bid=market.yes_bid,
            yes_ask=market.yes_ask,
            yes_bid_size=market.yes_bid_size,
            yes_ask_size=market.yes_ask_size,
            last_price=(
                market.last_price
                if market.last_price is not None
                else catalog_market.last_price
            ),
            volume_24h=(
                market.volume_24h
                if market.volume_24h is not None
                else catalog_market.volume_24h
            ),
            open_interest=(
                market.open_interest
                if market.open_interest is not None
                else catalog_market.open_interest
            ),
            close_time=(
                market.close_time
                if market.close_time is not None
                else catalog_market.close_time
            ),
            status=market.status,
            source=market.source,
            raw_sequence=market.raw_sequence,
        )

    async def _notify_market_update(self, market: MarketState) -> None:
        if self._on_market_update is not None:
            self._logger.info("Market update dispatched to runtime: %s", market.ticker)
            await self._on_market_update(market)

    def _store_orderbook_snapshot(self, message: dict[str, Any]) -> None:
        ticker = self._message_ticker(message)
        payload = message.get("msg")
        if ticker is None or not isinstance(payload, dict):
            return
        self._orderbooks[ticker] = {
            "yes": self._parse_orderbook_side(payload.get("yes")),
            "no": self._parse_orderbook_side(payload.get("no")),
        }

    def _apply_orderbook_delta(self, message: dict[str, Any]) -> MarketState | None:
        ticker = self._message_ticker(message)
        payload = message.get("msg")
        if ticker is None or not isinstance(payload, dict):
            return None

        orderbook = self._orderbooks.get(ticker)
        if orderbook is None:
            self._logger.info(
                "Observed orderbook_delta for %s before snapshot; waiting for reconstruction base",
                ticker,
            )
            return None

        side = str(payload.get("side") or "").lower()
        if side not in {"yes", "no"}:
            return None

        price = self._parse_int(payload.get("price") or payload.get("price_cents"))
        delta = self._parse_int(payload.get("delta") or payload.get("delta_quantity"))
        if price is None or delta is None:
            return None

        side_book = orderbook[side]
        updated_quantity = side_book.get(price, 0) + delta
        if updated_quantity <= 0:
            side_book.pop(price, None)
        else:
            side_book[price] = updated_quantity

        return self._market_from_orderbook(ticker, message)

    def _market_from_orderbook(
        self,
        ticker: str,
        message: dict[str, Any],
    ) -> MarketState | None:
        orderbook = self._orderbooks.get(ticker)
        if orderbook is None:
            return None

        yes_bid, yes_bid_size = self._best_book_level(orderbook["yes"])
        no_bid, no_bid_size = self._best_book_level(orderbook["no"])
        yes_ask = Decimal("1") - no_bid if no_bid is not None else None

        return MarketState(
            ticker=ticker,
            timestamp=datetime.now(timezone.utc),
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            yes_bid_size=yes_bid_size,
            yes_ask_size=no_bid_size,
            last_price=None,
            volume_24h=None,
            open_interest=None,
            close_time=None,
            status=MarketStatus.OPEN,
            source="websocket",
            raw_sequence=self._parse_int(message.get("seq")),
        )

    def _message_ticker(self, message: dict[str, Any]) -> str | None:
        ticker = message.get("market_ticker") or message.get("ticker")
        payload = message.get("msg")
        if ticker is None and isinstance(payload, dict):
            ticker = payload.get("market_ticker") or payload.get("ticker")
        return str(ticker) if ticker is not None else None

    def _parse_orderbook_side(self, levels: Any) -> dict[int, int]:
        if not isinstance(levels, list):
            return {}

        book: dict[int, int] = {}
        for level in levels:
            parsed = self._parse_book_level(level)
            if parsed is None:
                continue
            price, quantity = parsed
            if quantity > 0:
                book[price] = quantity
        return book

    def _parse_book_level(self, level: Any) -> tuple[int, int] | None:
        if isinstance(level, dict):
            price = level.get("price") or level.get("price_cents")
            quantity = level.get("quantity") or level.get("qty") or level.get("size")
        elif isinstance(level, (list, tuple)) and len(level) >= 2:
            price = level[0]
            quantity = level[1]
        else:
            return None

        parsed_price = self._parse_int(price)
        parsed_quantity = self._parse_int(quantity)
        if parsed_price is None or parsed_quantity is None:
            return None
        return parsed_price, parsed_quantity

    def _best_book_level(self, book: dict[int, int]) -> tuple[Decimal | None, int | None]:
        if not book:
            return None, None
        price = max(book)
        return Decimal(price) / Decimal("100"), book[price]

    def _parse_int(self, value: Any) -> int | None:
        if value is None or value == "":
            return None
        return int(Decimal(str(value)))


IngestionRuntime = IngestionService


async def build_runtime(
    *,
    settings: WorkerSettings,
    watched_tickers: list[str],
    on_market_update: Callable[[MarketState], Awaitable[None]] | None = None,
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
        on_market_update=on_market_update,
    )
