from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

from core.execution.adapters import PaperAdapter
from core.risk.engine import RiskEngine
from worker.config import WorkerSettings
from worker.command_listener import CommandListener
from worker.execution_repository import create_strategy_run, ensure_strategy_config
from worker.runtime import TradingRuntime
from worker.service import build_runtime
from worker.strategies.dummy import DummyStrategy


load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

HARDCODED_CRYPTO_TICKERS = [
    "KXBTCD-26JUL1917-T64499.99",
    "KXBTCD-26JUL1917-T63999.99",
    "KXBTCD-26JUL1917-T63749.99",
    "KXBTCD-26JUL1917-T64749.99",
    "KXBTCD-26JUL1917-T64249.99",
]


async def main() -> None:
    config_id = ensure_strategy_config(name="dummy_strategy", version=1, params={})
    run_id = create_strategy_run(config_id=config_id, mode="paper")
    logger.info("Created strategy_run: %s (config: %s)", run_id, config_id)
    logger.info("Starting trading runtime - run_id=%s", run_id)
    watched_tickers = HARDCODED_CRYPTO_TICKERS
    if not watched_tickers:
        raise RuntimeError("No liquid open Kalshi markets found; refusing to start ingestion")
    logger.info("Watching hardcoded crypto tickers: %s", watched_tickers)

    runtime = TradingRuntime(
        run_id=run_id,
        tickers=watched_tickers,
        strategy=DummyStrategy(),
        risk_engine=RiskEngine(),
        paper_adapter=PaperAdapter(),
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
