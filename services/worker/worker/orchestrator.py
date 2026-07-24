from __future__ import annotations

import logging
from dataclasses import dataclass

from core.schemas.market import MarketState
from worker.runtime import TradingRuntime

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ManagedRuntime:
    config_id: str
    name: str
    run_id: str
    runtime: TradingRuntime


class MultiStrategyOrchestrator:
    def __init__(self, runtimes: list[ManagedRuntime]) -> None:
        self.runtimes = runtimes
        self._runtimes_by_ticker: dict[str, list[ManagedRuntime]] = {}
        for managed in runtimes:
            for ticker in managed.runtime.tickers:
                self._runtimes_by_ticker.setdefault(ticker, []).append(managed)

    @property
    def watched_tickers(self) -> list[str]:
        return sorted(self._runtimes_by_ticker)

    async def on_market_update(self, market: MarketState) -> None:
        for managed in self._runtimes_by_ticker.get(market.ticker, []):
            LOGGER.debug(
                "Dispatching %s to strategy=%s run_id=%s",
                market.ticker,
                managed.name,
                managed.run_id,
            )
            await managed.runtime.on_market_update(market)

    def stop(self) -> None:
        for managed in self.runtimes:
            managed.runtime.stop()
