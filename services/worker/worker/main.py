from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

from core.execution.adapters import PaperAdapter
from core.risk.engine import RiskConfig, RiskEngine
from core.strategies.spread_capture import SpreadCaptureStrategy
from worker.config import WorkerSettings
from worker.command_listener import CommandListener
from worker.execution_repository import (
    ensure_strategy_config,
    get_or_create_strategy_run,
    load_open_positions,
    load_open_resting_orders,
)
from worker.runtime import TradingRuntime
from worker.service import build_runtime


load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

HARDCODED_CRYPTO_TICKERS = [
    "KXBTCD-26JUL2017-T64999.99",
    "KXBTCD-26JUL2017-T65749.99",
]


def build_spread_capture_risk_engine() -> RiskEngine:
    # Spread capture edge is the market-making spread itself, not a directional
    # mispricing estimate. Comparing it against the stricter directional 3%
    # threshold is a category error, so this strategy gets its own Kelly floor.
    spread_capture_risk_config = RiskConfig(
        min_edge_to_trade=0.003,
        kelly_fraction=0.5,
    )
    return RiskEngine(config=spread_capture_risk_config)


async def main() -> None:
    config_id = ensure_strategy_config(name="spread_capture", version=1, params={})
    run_id = get_or_create_strategy_run(config_id=config_id, mode="paper")
    logger.info("Using strategy_run: %s (config: %s)", run_id, config_id)
    logger.info("Starting trading runtime - run_id=%s", run_id)
    watched_tickers = HARDCODED_CRYPTO_TICKERS
    if not watched_tickers:
        raise RuntimeError("No liquid open Kalshi markets found; refusing to start ingestion")
    logger.info("Watching hardcoded crypto tickers: %s", watched_tickers)

    runtime = TradingRuntime(
        run_id=run_id,
        tickers=watched_tickers,
        strategy=SpreadCaptureStrategy(),
        risk_engine=build_spread_capture_risk_engine(),
        paper_adapter=PaperAdapter(),
    )
    restored_orders = load_open_resting_orders(run_id)
    runtime.restore_resting_orders(restored_orders)
    logger.info("Restored %d open resting orders for run_id=%s", len(restored_orders), run_id)
    restored_positions = load_open_positions(run_id)
    runtime.restore_positions(restored_positions)
    logger.info(
        "Restored %d open positions for run_id=%s",
        len(restored_positions),
        run_id,
    )
    ingestion = await build_runtime(
        settings=WorkerSettings.from_env(),
        watched_tickers=watched_tickers,
        on_market_update=runtime.on_market_update,
    )
    listener = CommandListener(runtime=runtime, config_id=config_id)

    try:
        await asyncio.gather(
            ingestion.run(),
            listener.listen(),
        )
    except KeyboardInterrupt:
        ingestion.stop()
        runtime.stop()
        listener.stop()
    finally:
        ingestion.stop()
        runtime.stop()
        listener.stop()
        await ingestion.close()


if __name__ == "__main__":
    asyncio.run(main())
