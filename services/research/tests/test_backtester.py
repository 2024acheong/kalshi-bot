from __future__ import annotations

import math
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.execution.adapters import PaperAdapter
from core.risk.engine import OrderIntent, RiskEngine
from core.schemas.market import FeatureVector, MarketState, MarketStatus
from research.backtester import Backtester, BacktestConfig
from research.metrics import BacktestMetrics, compute_metrics
from worker.strategies.dummy import DummyStrategy


BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def make_market(index: int = 0, **kwargs) -> MarketState:
    defaults = {
        "ticker": "KXTEST-26JAN-YES",
        "timestamp": BASE_TIME + timedelta(minutes=index),
        "yes_bid": Decimal("0.46"),
        "yes_ask": Decimal("0.48"),
        "yes_bid_size": 100,
        "yes_ask_size": 100,
        "last_price": Decimal("0.47"),
        "volume_24h": 1000 + index,
        "open_interest": 5000 + index,
        "close_time": None,
        "status": MarketStatus.OPEN,
        "source": "test_fixture",
        "raw_sequence": index,
    }
    defaults.update(kwargs)
    return MarketState(**defaults)


class LowEdgeStrategy:
    def evaluate(
        self,
        market: MarketState,
        features: FeatureVector,
        run_id: str,
    ) -> OrderIntent | None:
        if market.yes_ask is None:
            return None
        return OrderIntent(
            ticker=market.ticker,
            side="yes",
            price=market.yes_ask,
            qty=10,
            estimated_edge=0.01,
            model_prob=float(market.yes_ask) + 0.01,
            run_id=run_id,
        )


def make_config() -> BacktestConfig:
    return BacktestConfig(
        tickers=["KXTEST-26JAN-YES"],
        date_from=BASE_TIME - timedelta(minutes=1),
        date_to=BASE_TIME + timedelta(minutes=10),
    )


def patch_loader(monkeypatch: pytest.MonkeyPatch, snapshots: list[MarketState]) -> None:
    monkeypatch.setattr(
        "research.backtester.data_loader.load_snapshots",
        lambda tickers, date_from, date_to: {"KXTEST-26JAN-YES": snapshots},
    )
    monkeypatch.setattr(
        "research.backtester.data_loader.get_close_times",
        lambda tickers: {"KXTEST-26JAN-YES": BASE_TIME + timedelta(hours=24)},
    )


def test_backtest_is_deterministic(monkeypatch) -> None:
    snapshots = [make_market(index) for index in range(3)]
    patch_loader(monkeypatch, snapshots)
    config = make_config()

    first = Backtester(DummyStrategy(), RiskEngine(), PaperAdapter(), config).run()
    second = Backtester(DummyStrategy(), RiskEngine(), PaperAdapter(), config).run()

    assert first["fills"] == second["fills"]
    assert asdict(first["metrics"]) == asdict(second["metrics"])


def test_backtest_produces_fills_on_favorable_conditions(monkeypatch) -> None:
    snapshots = [make_market(index) for index in range(2)]
    patch_loader(monkeypatch, snapshots)

    result = Backtester(DummyStrategy(), RiskEngine(), PaperAdapter(), make_config()).run()

    assert result["total_events"] == 2
    assert result["total_orders_allowed"] >= 1
    assert any(fill["fill_qty"] > 0 for fill in result["fills"])
    assert result["metrics"].total_trades >= 1


def test_backtest_blocks_via_risk_engine(monkeypatch) -> None:
    snapshots = [make_market()]
    patch_loader(monkeypatch, snapshots)

    result = Backtester(LowEdgeStrategy(), RiskEngine(), PaperAdapter(), make_config()).run()

    assert result["total_intents"] == 1
    assert result["total_orders_blocked"] == 1
    assert result["fills"] == []


def test_metrics_computation_basic() -> None:
    metrics = compute_metrics(
        fills=[
            {
                "fill_price": Decimal("0.48"),
                "fill_qty": 10,
                "fee": Decimal("0.10"),
                "side": "yes",
                "model_prob": 0.53,
            },
            {
                "fill_price": Decimal("0.52"),
                "fill_qty": 5,
                "fee": Decimal("0.05"),
                "side": "yes",
                "model_prob": 0.56,
            },
        ],
        daily_pnl_series=[1.0, -0.5, 2.0],
    )

    assert isinstance(metrics, BacktestMetrics)
    assert metrics.total_trades == 2
    assert metrics.total_fees == pytest.approx(0.15)
    assert metrics.max_drawdown == pytest.approx(0.5)
    assert metrics.sharpe is not None
    assert not math.isnan(metrics.sharpe)
    assert metrics.hit_rate is not None
    assert metrics.brier_score is not None
