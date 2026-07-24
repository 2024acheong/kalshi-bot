from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from core.schemas.market import MarketState, MarketStatus
from worker.orchestrator import ManagedRuntime, MultiStrategyOrchestrator


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make_market(ticker: str) -> MarketState:
    now = datetime.now(timezone.utc)
    return MarketState(
        ticker=ticker,
        timestamp=now,
        yes_bid=Decimal("0.45"),
        yes_ask=Decimal("0.47"),
        yes_bid_size=100,
        yes_ask_size=100,
        last_price=Decimal("0.46"),
        volume_24h=100,
        open_interest=1000,
        close_time=now + timedelta(hours=1),
        status=MarketStatus.OPEN,
        source="test",
    )


class FakeRuntime:
    def __init__(self, tickers: list[str]) -> None:
        self.tickers = tickers
        self.on_market_update = AsyncMock()
        self.stop = AsyncMock()


@pytest.mark.anyio
async def test_orchestrator_dispatches_shared_ticker_to_multiple_runtimes() -> None:
    first = FakeRuntime(["KXONE", "KXSHARED"])
    second = FakeRuntime(["KXSHARED", "KXTWO"])
    orchestrator = MultiStrategyOrchestrator(
        [
            ManagedRuntime("config-1", "mean_reversion", "run-1", first),
            ManagedRuntime("config-2", "event_drift", "run-2", second),
        ]
    )
    market = make_market("KXSHARED")

    await orchestrator.on_market_update(market)

    assert orchestrator.watched_tickers == ["KXONE", "KXSHARED", "KXTWO"]
    first.on_market_update.assert_awaited_once_with(market)
    second.on_market_update.assert_awaited_once_with(market)


@pytest.mark.anyio
async def test_orchestrator_ignores_unwatched_ticker() -> None:
    runtime = FakeRuntime(["KXONE"])
    orchestrator = MultiStrategyOrchestrator(
        [ManagedRuntime("config-1", "mean_reversion", "run-1", runtime)]
    )

    await orchestrator.on_market_update(make_market("KXTWO"))

    runtime.on_market_update.assert_not_called()
