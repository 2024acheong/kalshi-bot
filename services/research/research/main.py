from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from core.execution.adapters import PaperAdapter
from core.risk.engine import RiskConfig, RiskEngine
from core.strategies.mean_reversion import MeanReversionStrategy
from core.strategies.spread_capture import SpreadCaptureStrategy
from research.backtester import Backtester, BacktestConfig


DEFAULT_TICKERS = [
    "KXBTCD-26JUL2017-T64999.99",
    "KXBTCD-26JUL2017-T65749.99",
]


def _configured_tickers() -> list[str]:
    tickers = os.getenv("BACKTEST_TICKERS")
    if not tickers:
        return DEFAULT_TICKERS
    return [ticker.strip() for ticker in tickers.split(",") if ticker.strip()]


def _configured_lookback_hours() -> float:
    return float(os.getenv("BACKTEST_LOOKBACK_HOURS", "6"))


def _configured_strategy_name() -> str:
    return os.getenv("BACKTEST_STRATEGY", "spread_capture").strip().lower()


def _configured_max_blocked_print() -> int:
    return int(os.getenv("BACKTEST_MAX_BLOCKED_PRINT", "20"))


def build_spread_capture_risk_engine() -> RiskEngine:
    # Spread capture edge is the market-making spread itself, not a directional
    # mispricing estimate. Comparing it against the stricter directional 3%
    # threshold is a category error, so this strategy gets its own Kelly floor.
    spread_capture_risk_config = RiskConfig(
        min_edge_to_trade=0.003,
        kelly_fraction=0.5,
    )
    return RiskEngine(config=spread_capture_risk_config)


def build_strategy_and_risk_engine(strategy_name: str):
    if strategy_name == "mean_reversion":
        return MeanReversionStrategy(), RiskEngine()
    if strategy_name == "spread_capture":
        return SpreadCaptureStrategy(), build_spread_capture_risk_engine()
    raise ValueError(
        "BACKTEST_STRATEGY must be one of: mean_reversion, spread_capture"
    )


def main() -> None:
    load_dotenv()
    now = datetime.now(timezone.utc)
    tickers = _configured_tickers()
    lookback_hours = _configured_lookback_hours()
    strategy_name = _configured_strategy_name()
    max_blocked_print = _configured_max_blocked_print()
    strategy, risk_engine = build_strategy_and_risk_engine(strategy_name)
    config = BacktestConfig(
        tickers=tickers,
        date_from=now - timedelta(hours=lookback_hours),
        date_to=now,
    )
    backtester = Backtester(
        strategy=strategy,
        risk_engine=risk_engine,
        paper_adapter=PaperAdapter(),
        config=config,
    )
    result = backtester.run()
    print(f"Strategy: {strategy_name}")
    print(f"Tickers: {', '.join(tickers)}")
    print(f"Window: {config.date_from.isoformat()} to {config.date_to.isoformat()}")
    print(f"Total events: {result['total_events']}")
    print(
        "Orders allowed: "
        f"{result['total_orders_allowed']}, blocked: {result['total_orders_blocked']}"
    )
    if result["blocked_orders"]:
        print("Blocked orders:")
        shown_blocked_orders = result["blocked_orders"][:max_blocked_print]
        for blocked in shown_blocked_orders:
            print(
                "  "
                f"{blocked['timestamp']} {blocked['ticker']} {blocked['side']} "
                f"price={blocked['price']} edge={blocked['estimated_edge']:.4f} "
                f"blocked_by={blocked['blocked_by']} reason={blocked['reason']} "
                f"metadata={blocked['metadata']}"
            )
        hidden_count = len(result["blocked_orders"]) - len(shown_blocked_orders)
        if hidden_count > 0:
            print(
                f"  ... {hidden_count} more blocked orders not shown "
                "(set BACKTEST_MAX_BLOCKED_PRINT to change)"
            )
    print(f"Metrics: {result['metrics']}")
    if result["total_events"] == 0:
        print(
            "No snapshots found. Start the worker first, then rerun with a window "
            "that overlaps rows in market_snapshots."
        )


if __name__ == "__main__":
    main()
