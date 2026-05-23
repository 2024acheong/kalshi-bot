from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
