from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from core.execution.adapters import PaperAdapter
from core.risk.engine import RiskConfig, RiskEngine
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


def build_spread_capture_risk_engine() -> RiskEngine:
    # Spread capture edge is the market-making spread itself, not a directional
    # mispricing estimate. Comparing it against the stricter directional 3%
    # threshold is a category error, so this strategy gets its own Kelly floor.
    spread_capture_risk_config = RiskConfig(
        min_edge_to_trade=0.003,
        kelly_fraction=0.5,
    )
    return RiskEngine(config=spread_capture_risk_config)


def main() -> None:
    load_dotenv()
    now = datetime.now(timezone.utc)
    tickers = _configured_tickers()
    lookback_hours = _configured_lookback_hours()
    config = BacktestConfig(
        tickers=tickers,
        date_from=now - timedelta(hours=lookback_hours),
        date_to=now,
    )
    backtester = Backtester(
        strategy=SpreadCaptureStrategy(),
        risk_engine=build_spread_capture_risk_engine(),
        paper_adapter=PaperAdapter(),
        config=config,
    )
    result = backtester.run()
    print(f"Tickers: {', '.join(tickers)}")
    print(f"Window: {config.date_from.isoformat()} to {config.date_to.isoformat()}")
    print(f"Total events: {result['total_events']}")
    print(
        "Orders allowed: "
        f"{result['total_orders_allowed']}, blocked: {result['total_orders_blocked']}"
    )
    if result["blocked_orders"]:
        print("Blocked orders:")
        for blocked in result["blocked_orders"]:
            print(
                "  "
                f"{blocked['timestamp']} {blocked['ticker']} {blocked['side']} "
                f"price={blocked['price']} edge={blocked['estimated_edge']:.4f} "
                f"blocked_by={blocked['blocked_by']} reason={blocked['reason']} "
                f"metadata={blocked['metadata']}"
            )
    print(f"Metrics: {result['metrics']}")
    if result["total_events"] == 0:
        print(
            "No snapshots found. Start the worker first, then rerun with a window "
            "that overlaps rows in market_snapshots."
        )


if __name__ == "__main__":
    main()
