from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

from worker.config import WorkerSettings
from worker.command_listener import CommandListener
from worker.execution_repository import (
    ensure_strategy_config,
    get_or_create_strategy_run,
    load_enabled_strategy_configs,
    load_open_positions,
    load_open_resting_orders,
    persist_worker_heartbeat,
)
from worker.kalshi.client import KalshiRestClient
from worker.orchestrator import ManagedRuntime, MultiStrategyOrchestrator
from worker.runtime import TradingRuntime
from worker.service import build_runtime
from worker.strategy_factory import build_strategy_runtime_spec
from worker.ticker_discovery import TickerDiscoveryConfig, discover_live_tickers


load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _ticker_discovery_config(strategy_name: str) -> TickerDiscoveryConfig:
    return TickerDiscoveryConfig(
        strategy_name=strategy_name,
        limit=int(os.getenv("TICKER_DISCOVERY_LIMIT", "5")),
        min_volume=int(os.getenv("TICKER_DISCOVERY_MIN_VOLUME", "0")),
        max_pages=int(os.getenv("TICKER_DISCOVERY_MAX_PAGES", "10")),
    )


async def discover_worker_tickers(settings: WorkerSettings, strategy_name: str) -> list[str]:
    async with httpx.AsyncClient(timeout=10.0) as http_client:
        client = KalshiRestClient(
            http_client=http_client,
            base_url=settings.kalshi_base_url,
        )
        return await discover_live_tickers(client, _ticker_discovery_config(strategy_name))


async def _heartbeat_loop(orchestrator: MultiStrategyOrchestrator) -> None:
    interval_seconds = int(os.getenv("WORKER_HEARTBEAT_INTERVAL_SECONDS", "60"))
    while True:
        try:
            persist_worker_heartbeat(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "strategy_runs": [
                        {
                            "config_id": managed.config_id,
                            "name": managed.name,
                            "run_id": managed.run_id,
                        }
                        for managed in orchestrator.runtimes
                    ],
                    "watched_tickers": orchestrator.watched_tickers,
                }
            )
        except Exception:
            logger.exception("Failed to persist worker heartbeat")
        await asyncio.sleep(interval_seconds)


def _enabled_or_default_configs() -> list[dict]:
    configs = load_enabled_strategy_configs()
    if configs:
        return configs

    config_id = ensure_strategy_config(name="spread_capture", version=1, params={})
    logger.warning(
        "No enabled strategy_configs found; falling back to spread_capture config=%s",
        config_id,
    )
    return [
        {
            "id": config_id,
            "name": "spread_capture",
            "version": 1,
            "params_json": {},
            "status": "enabled",
        }
    ]


async def _build_managed_runtime(
    *,
    settings: WorkerSettings,
    config: dict,
) -> ManagedRuntime:
    spec = build_strategy_runtime_spec(config)
    run_id = get_or_create_strategy_run(config_id=spec.config_id, mode="paper")
    watched_tickers = await discover_worker_tickers(settings, spec.name)
    if not watched_tickers:
        raise RuntimeError(f"No live tickers discovered for strategy {spec.name}")

    runtime = TradingRuntime(
        run_id=run_id,
        tickers=watched_tickers,
        strategy=spec.strategy,
        risk_engine=spec.risk_engine,
        paper_adapter=spec.paper_adapter,
    )
    restored_orders = load_open_resting_orders(run_id)
    runtime.restore_resting_orders(restored_orders)
    restored_positions = load_open_positions(run_id)
    runtime.restore_positions(restored_positions)
    logger.info(
        "Prepared strategy=%s config=%s run_id=%s tickers=%s restored_orders=%d restored_positions=%d",
        spec.name,
        spec.config_id,
        run_id,
        watched_tickers,
        len(restored_orders),
        len(restored_positions),
    )
    return ManagedRuntime(
        config_id=spec.config_id,
        name=spec.name,
        run_id=run_id,
        runtime=runtime,
    )


async def main() -> None:
    settings = WorkerSettings.from_env()
    managed_runtimes = [
        await _build_managed_runtime(settings=settings, config=config)
        for config in _enabled_or_default_configs()
    ]
    orchestrator = MultiStrategyOrchestrator(managed_runtimes)
    if not orchestrator.watched_tickers:
        raise RuntimeError("No live tickers discovered; refusing to start ingestion")
    logger.info("Watching %d live-discovered tickers", len(orchestrator.watched_tickers))

    ingestion = await build_runtime(
        settings=settings,
        watched_tickers=orchestrator.watched_tickers,
        on_market_update=orchestrator.on_market_update,
    )
    listeners = [
        CommandListener(runtime=managed.runtime, config_id=managed.config_id)
        for managed in managed_runtimes
    ]

    try:
        await asyncio.gather(
            ingestion.run(),
            _heartbeat_loop(orchestrator),
            *(listener.listen() for listener in listeners),
        )
    except KeyboardInterrupt:
        ingestion.stop()
        orchestrator.stop()
        for listener in listeners:
            listener.stop()
    finally:
        ingestion.stop()
        orchestrator.stop()
        for listener in listeners:
            listener.stop()
        await ingestion.close()


if __name__ == "__main__":
    asyncio.run(main())
