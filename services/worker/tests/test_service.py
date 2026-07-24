from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from worker.cache import InMemoryMarketCache
from worker.config import WorkerSettings
from worker.kalshi import KalshiRestClient
from worker.repository import InMemoryMarketRepository
from worker.service import IngestionService


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class StubKalshiClient:
    def __init__(self, markets: list[dict]) -> None:
        self._markets = markets
        self.get_markets_calls: list[list[str]] = []
        self.list_markets_calls = 0

    async def get_markets(self, tickers: list[str]) -> list[dict]:
        self.get_markets_calls.append(tickers)
        return [market for market in self._markets if market["ticker"] in tickers]

    async def list_markets(
        self,
        *,
        cursor: str | None = None,
        status: str = "open",
        limit: int = 200,
    ) -> tuple[list[dict], str | None]:
        del cursor, status, limit
        self.list_markets_calls += 1
        return self._markets, None


class StubWebSocketClient:
    def __init__(self, message: dict | None = None) -> None:
        self._message = message
        self.stopped = False

    async def run(self) -> None:
        return None

    def stop(self) -> None:
        self.stopped = True


class FailingSnapshotRepository(InMemoryMarketRepository):
    async def insert_snapshot(self, market):
        raise RuntimeError(f"foreign key violation for {market.ticker}")


@pytest.mark.anyio
async def test_run_syncs_catalog_before_websocket_loop() -> None:
    now = datetime.now(timezone.utc)
    repository = InMemoryMarketRepository()
    cache = InMemoryMarketCache()
    service = IngestionService(
        tickers=["KXBTC-26APR-B90000"],
        settings=WorkerSettings(),
        kalshi_client=StubKalshiClient(
            [
                {
                    "ticker": "KXBTC-26APR-B90000",
                    "title": "BTC above 90k by Apr 26?",
                    "status": "open",
                    "last_price_ts": now.isoformat(),
                    "yes_bid": "44",
                    "yes_ask": "46",
                }
            ]
        ),
        repository=repository,
        cache=cache,
        websocket_client=StubWebSocketClient(),
    )

    await service._sync_catalog()

    assert len(repository.catalog_rows) == 1
    assert repository.catalog_rows[0]["ticker"] == "KXBTC-26APR-B90000"


@pytest.mark.anyio
async def test_run_with_no_tickers_skips_ingestion_loops(caplog) -> None:
    service = IngestionService(
        tickers=[],
        settings=WorkerSettings(),
        kalshi_client=StubKalshiClient([]),
        repository=InMemoryMarketRepository(),
        cache=InMemoryMarketCache(),
        websocket_client=StubWebSocketClient(),
    )

    await service.run()

    assert service._running is False
    assert "No watched tickers configured" in caplog.text


@pytest.mark.anyio
async def test_handle_websocket_message_persists_snapshot_and_cache() -> None:
    repository = InMemoryMarketRepository()
    cache = InMemoryMarketCache()
    service = IngestionService(
        tickers=["KXBTC-26APR-B90000"],
        settings=WorkerSettings(),
        kalshi_client=StubKalshiClient([]),
        repository=repository,
        cache=cache,
        websocket_client=StubWebSocketClient(),
    )

    await service._handle_websocket_message(
        {
            "type": "ticker",
            "market_ticker": "KXBTC-26APR-B90000",
            "seq": 42,
            "msg": {
                "yes_bid": 45,
                "yes_ask": 47,
                "yes_bid_size": 100,
                "yes_ask_size": 80,
                "last_price": 46,
                "volume": 5000,
            },
        }
    )

    assert len(repository.snapshot_rows) == 1
    assert repository.snapshot_rows[0]["ticker"] == "KXBTC-26APR-B90000"
    assert "market:KXBTC-26APR-B90000:state" in cache.data


@pytest.mark.anyio
async def test_handle_orderbook_snapshot_dispatches_runtime_update() -> None:
    repository = InMemoryMarketRepository()
    cache = InMemoryMarketCache()
    dispatched = []

    async def on_market_update(market):
        dispatched.append(market)

    service = IngestionService(
        tickers=["KXBTC-26APR-B90000"],
        settings=WorkerSettings(),
        kalshi_client=StubKalshiClient([]),
        repository=repository,
        cache=cache,
        websocket_client=StubWebSocketClient(),
        on_market_update=on_market_update,
    )

    await service._handle_websocket_message(
        {
            "type": "orderbook_snapshot",
            "market_ticker": "KXBTC-26APR-B90000",
            "seq": 7,
            "sid": 1,
            "msg": {
                "yes": [[44, 120], [45, 100]],
                "no": [[54, 80], [53, 60]],
            },
        }
    )

    assert len(repository.snapshot_rows) == 1
    assert repository.snapshot_rows[0]["ticker"] == "KXBTC-26APR-B90000"
    assert "market:KXBTC-26APR-B90000:state" in cache.data
    assert len(dispatched) == 1
    assert dispatched[0].ticker == "KXBTC-26APR-B90000"
    assert dispatched[0].yes_bid == Decimal("0.45")
    assert dispatched[0].yes_ask == Decimal("0.46")
    assert dispatched[0].yes_bid_size == 100
    assert dispatched[0].yes_ask_size == 80


@pytest.mark.anyio
async def test_websocket_update_is_enriched_with_catalog_close_time() -> None:
    close_time = datetime.now(timezone.utc) + timedelta(hours=24)
    repository = InMemoryMarketRepository()
    cache = InMemoryMarketCache()
    dispatched = []

    async def on_market_update(market):
        dispatched.append(market)

    service = IngestionService(
        tickers=["KXBTC-26APR-B90000"],
        settings=WorkerSettings(),
        kalshi_client=StubKalshiClient(
            [
                {
                    "ticker": "KXBTC-26APR-B90000",
                    "title": "BTC above 90k by Apr 26?",
                    "status": "open",
                    "last_price_ts": datetime.now(timezone.utc).isoformat(),
                    "close_time": close_time.isoformat(),
                    "yes_bid": "44",
                    "yes_ask": "46",
                }
            ]
        ),
        repository=repository,
        cache=cache,
        websocket_client=StubWebSocketClient(),
        on_market_update=on_market_update,
    )

    await service._sync_catalog()
    await service._handle_websocket_message(
        {
            "type": "orderbook_snapshot",
            "market_ticker": "KXBTC-26APR-B90000",
            "seq": 7,
            "sid": 1,
            "msg": {
                "yes": [[44, 120], [45, 100]],
                "no": [[54, 80], [53, 60]],
            },
        }
    )

    assert dispatched
    assert dispatched[-1].close_time == close_time


@pytest.mark.anyio
async def test_handle_orderbook_delta_reconstructs_market_update() -> None:
    repository = InMemoryMarketRepository()
    cache = InMemoryMarketCache()
    dispatched = []

    async def on_market_update(market):
        dispatched.append(market)

    service = IngestionService(
        tickers=["KXBTC-26APR-B90000"],
        settings=WorkerSettings(),
        kalshi_client=StubKalshiClient([]),
        repository=repository,
        cache=cache,
        websocket_client=StubWebSocketClient(),
        on_market_update=on_market_update,
    )

    await service._handle_websocket_message(
        {
            "type": "orderbook_snapshot",
            "market_ticker": "KXBTC-26APR-B90000",
            "seq": 7,
            "sid": 1,
            "msg": {
                "yes": [[44, 120], [45, 100]],
                "no": [[54, 80]],
            },
        }
    )
    await service._handle_websocket_message(
        {
            "type": "orderbook_delta",
            "market_ticker": "KXBTC-26APR-B90000",
            "seq": 8,
            "sid": 1,
            "msg": {
                "side": "yes",
                "price": 46,
                "delta": 50,
            },
        }
    )

    assert len(repository.snapshot_rows) == 2
    assert len(dispatched) == 2
    assert dispatched[-1].yes_bid == Decimal("0.46")
    assert dispatched[-1].yes_bid_size == 50


@pytest.mark.anyio
async def test_snapshot_persistence_error_does_not_raise(caplog) -> None:
    cache = InMemoryMarketCache()
    service = IngestionService(
        tickers=["KXBTC-26APR-B90000"],
        settings=WorkerSettings(),
        kalshi_client=StubKalshiClient([]),
        repository=FailingSnapshotRepository(),
        cache=cache,
        websocket_client=StubWebSocketClient(),
    )

    await service._handle_websocket_message(
        {
            "type": "orderbook_snapshot",
            "market_ticker": "KXBTC-26APR-B90000",
            "seq": 7,
            "sid": 1,
            "msg": {
                "yes": [[44, 120], [45, 100]],
                "no": [[54, 80]],
            },
        }
    )

    assert "Skipping market snapshot for KXBTC-26APR-B90000" in caplog.text
    assert cache.data == {}


@pytest.mark.anyio
async def test_staleness_loop_emits_alert_for_old_market() -> None:
    repository = InMemoryMarketRepository()
    cache = InMemoryMarketCache()
    service = IngestionService(
        tickers=["KXBTC-26APR-B90000"],
        settings=WorkerSettings(),
        kalshi_client=StubKalshiClient([]),
        repository=repository,
        cache=cache,
        websocket_client=StubWebSocketClient(),
    )
    service._last_update_at["KXBTC-26APR-B90000"] = datetime.now(timezone.utc) - timedelta(
        seconds=IngestionService.STALENESS_THRESHOLD_SECONDS + 1
    )
    service._running = True

    async def stop_after_first_sleep(seconds: float) -> None:
        del seconds
        service._running = False

    service._sleep_while_running = stop_after_first_sleep  # type: ignore[method-assign]
    await service._staleness_loop()

    assert repository.system_events
    assert repository.system_events[0]["event_type"] == "stale_market"


@pytest.mark.anyio
async def test_kalshi_client_returns_empty_on_404() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = KalshiRestClient(http_client=http_client, base_url="https://example.test")
        markets = await client.get_markets(["DOES-NOT-EXIST"])

    assert markets == []
