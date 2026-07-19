from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.execution.adapters import PaperAdapter
from core.risk.engine import RiskEngine
from research.backtester import Backtester, BacktestConfig
from worker.strategies.dummy import DummyStrategy


def main() -> None:
    now = datetime.now(timezone.utc)
    config = BacktestConfig(
        tickers=[
            "KXBTCD-26JUL1917-T64499.99",
            "KXBTCD-26JUL1917-T63999.99",
        ],
        date_from=now - timedelta(hours=6),
        date_to=now,
    )
    backtester = Backtester(
        strategy=DummyStrategy(),
        risk_engine=RiskEngine(),
        paper_adapter=PaperAdapter(),
        config=config,
    )
    result = backtester.run()
    print(f"Total events: {result['total_events']}")
    print(
        "Orders allowed: "
        f"{result['total_orders_allowed']}, blocked: {result['total_orders_blocked']}"
    )
    print(f"Metrics: {result['metrics']}")


if __name__ == "__main__":
    main()
