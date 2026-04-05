from datetime import datetime, timedelta, timezone

import pytest

from worker.config import WorkerSettings
from worker.kalshi import KalshiRestClient
from worker.monitoring import MarketMonitor
from worker.service import IngestionRuntime
from worker.repository import InMemoryMarketRepository
from worker.cache import InMemoryMarketCache
import httpx


class StubKalshiClient:
    def __init__(self, markets: list[dict]) -> None:
        self._markets = markets

    async def get_markets(self, tickers: list[str]) -> list[dict]:
        return [market for market in self._markets if market["ticker"] in tickers]


@pytest.mark.asyncio
async def test_poll_once_persists_snapshots_and_caches_latest_state() -> None:
    repository = InMemoryMarketRepository()
    cache = InMemoryMarketCache()
    runtime = IngestionRuntime(
        settings=WorkerSettings(),
        watched_tickers=["KXBTC-26APR-B90000", "KXETH-26APR-B2000"],
        kalshi_client=StubKalshiClient(
            [
                {
                    "ticker": "KXBTC-26APR-B90000",
                    "title": "BTC above 90k by Apr 26?",
                    "status": "open",
                    "last_price_ts": "2026-04-05T13:00:00Z",
                    "yes_bid": "44",
                    "yes_ask": "46",
                },
                {
                    "ticker": "KXETH-26APR-B2000",
                    "title": "ETH above 2k by Apr 26?",
                    "status": "open",
                    "last_price_ts": "2026-04-05T13:00:01Z",
                    "yes_bid": "51",
                    "yes_ask": "52",
                },
            ]
        ),
        repository=repository,
        cache=cache,
        monitor=MarketMonitor(
            poll_interval_seconds=5,
            staleness_threshold_seconds=30,
            gap_alert_factor=2.5,
        ),
    )

    markets, alerts = await runtime.poll_once()

    assert len(markets) == 2
    assert alerts == []
    assert len(repository.catalog_rows) == 2
    assert len(repository.snapshot_rows) == 2
    assert "market:KXBTC-26APR-B90000:state" in cache.data
    assert "market:KXETH-26APR-B2000:state" in cache.data
    assert repository.system_events == []


@pytest.mark.asyncio
async def test_poll_once_emits_missing_and_stale_alerts() -> None:
    repository = InMemoryMarketRepository()
    cache = InMemoryMarketCache()
    runtime = IngestionRuntime(
        settings=WorkerSettings(staleness_threshold_seconds=5),
        watched_tickers=["KXBTC-26APR-B90000", "KXBTC-26APR-B95000"],
        kalshi_client=StubKalshiClient(
            [
                {
                    "ticker": "KXBTC-26APR-B90000",
                    "title": "BTC above 90k by Apr 26?",
                    "status": "open",
                    "last_price_ts": (
                        datetime.now(timezone.utc) - timedelta(seconds=20)
                    ).isoformat(),
                    "yes_bid": "44",
                    "yes_ask": "46",
                }
            ]
        ),
        repository=repository,
        cache=cache,
        monitor=MarketMonitor(
            poll_interval_seconds=5,
            staleness_threshold_seconds=5,
            gap_alert_factor=2.5,
        ),
    )

    _, alerts = await runtime.poll_once()

    codes = {alert.code for alert in alerts}
    assert "missing_market" in codes
    assert "stale_market_data" in codes
    assert {event["event_type"] for event in repository.system_events} == codes


def test_market_monitor_detects_poll_gaps() -> None:
    monitor = MarketMonitor(
        poll_interval_seconds=5,
        staleness_threshold_seconds=30,
        gap_alert_factor=2.0,
    )
    start = datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc)
    monitor.on_poll_completed(start)

    alerts = monitor.on_poll_started(start + timedelta(seconds=11))

    assert len(alerts) == 1
    assert alerts[0].code == "poll_gap_detected"


@pytest.mark.asyncio
async def test_run_forever_is_stoppable() -> None:
    repository = InMemoryMarketRepository()
    cache = InMemoryMarketCache()

    class StopAfterOnePollClient(StubKalshiClient):
        def __init__(self, runtime: IngestionRuntime | None = None) -> None:
            super().__init__(
                [
                    {
                        "ticker": "KXBTC-26APR-B90000",
                        "title": "BTC above 90k by Apr 26?",
                        "status": "open",
                        "last_price_ts": "2026-04-05T13:00:00Z",
                        "yes_bid": "44",
                        "yes_ask": "46",
                    }
                ]
            )
            self.runtime = runtime
            self.calls = 0

        async def get_markets(self, tickers: list[str]) -> list[dict]:
            self.calls += 1
            if self.runtime is not None:
                self.runtime.stop()
            return await super().get_markets(tickers)

    client = StopAfterOnePollClient()
    runtime = IngestionRuntime(
        settings=WorkerSettings(poll_interval_seconds=0.01),
        watched_tickers=["KXBTC-26APR-B90000"],
        kalshi_client=client,
        repository=repository,
        cache=cache,
        monitor=MarketMonitor(
            poll_interval_seconds=0.01,
            staleness_threshold_seconds=30,
            gap_alert_factor=2.5,
        ),
    )
    client.runtime = runtime

    await runtime.run_forever()

    assert client.calls == 1


@pytest.mark.asyncio
async def test_kalshi_client_returns_empty_on_404() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = KalshiRestClient(http_client=http_client, base_url="https://example.test")
        markets = await client.get_markets(["DOES-NOT-EXIST"])

    assert markets == []
