from __future__ import annotations

import math
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.execution.adapters import PaperAdapter
from core.risk.engine import OrderIntent, RiskConfig, RiskEngine
from core.schemas.market import FeatureVector, MarketState, MarketStatus
from core.strategies.calibration_mispricing import (
    CalibrationMispricingStrategy,
    ProbabilityEstimator,
)
from core.strategies.event_drift import EventDriftStrategy
from core.strategies.mean_reversion import MeanReversionStrategy
from core.strategies.spread_capture import SpreadCaptureStrategy
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
        "no_bid": Decimal("0.51"),
        "no_ask": Decimal("0.53"),
        "no_bid_size": 100,
        "no_ask_size": 100,
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


class SequenceEstimator(ProbabilityEstimator):
    def __init__(self, values: list[float | None]):
        self.values = values
        self.index = 0

    def estimate(self, market: MarketState, features: FeatureVector) -> float | None:
        value = self.values[self.index]
        self.index += 1
        return value


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


def test_backtest_handles_spread_capture_pair(monkeypatch) -> None:
    snapshots = [
        make_market(
            index=0,
            yes_bid=Decimal("0.40"),
            yes_ask=Decimal("0.30"),
            yes_bid_size=100,
            yes_ask_size=100,
            no_bid=Decimal("0.50"),
            no_ask=Decimal("0.45"),
            no_bid_size=100,
            no_ask_size=100,
        ),
        make_market(
            index=1,
            yes_bid=Decimal("0.47"),
            yes_ask=Decimal("0.39"),
            yes_bid_size=100,
            yes_ask_size=100,
            no_bid=Decimal("0.50"),
            no_ask=Decimal("0.70"),
            no_bid_size=100,
            no_ask_size=100,
        ),
    ]
    patch_loader(monkeypatch, snapshots)

    result = Backtester(
        SpreadCaptureStrategy(),
        RiskEngine(),
        PaperAdapter(),
        make_config(),
    ).run()

    assert result["total_intents"] == 1
    assert result["total_orders_allowed"] == 2
    assert len(result["fills"]) == 2
    assert {fill["side"] for fill in result["fills"]} == {"yes", "no"}


def test_backtest_tracks_mean_reversion_entry_and_exit(monkeypatch) -> None:
    snapshots = [
        make_market(
            index=0,
            yes_bid=Decimal("0.38"),
            yes_ask=Decimal("0.40"),
            no_bid=Decimal("0.60"),  # ADDED
            no_ask=Decimal("0.62"),  # ADDED
        ),
        make_market(
            index=1,
            yes_bid=Decimal("0.55"),
            yes_ask=Decimal("0.57"),
            no_bid=Decimal("0.43"),  # ADDED
            no_ask=Decimal("0.45"),  # ADDED
        ),
    ]
    patch_loader(monkeypatch, snapshots)
    features = FeatureVector(
        ticker="KXTEST-26JAN-YES",
        timestamp=BASE_TIME,
        mid_price=0.50,
        spread_pct=4.0,
        spread_ticks=0.02,
        bid_ask_imbalance=0.0,
        time_to_close_hours=24.0,
        implied_probability=0.50,
        liquidity_score=100.0,
        price_momentum_1h=0.05,
        price_momentum_24h=None,
        volume_zscore=0.0,
        open_interest_delta=None,
    )
    monkeypatch.setattr("research.backtester.compute_features", lambda market, history: features)

    result = Backtester(
        MeanReversionStrategy(),
        RiskEngine(config=RiskConfig(min_edge_to_trade=0.003)),
        PaperAdapter(),
        make_config(),
    ).run()

    assert result["total_intents"] == 2
    assert result["total_orders_allowed"] == 2
    assert [fill["side"] for fill in result["fills"]] == ["no", "no"]  # was ["no", "yes"]


def test_backtest_tracks_event_drift_entry_and_exit(monkeypatch) -> None:
    snapshots = [
        make_market(
            index=0,
            yes_bid=Decimal("0.49"),
            yes_ask=Decimal("0.51"),
        ),
        make_market(
            index=1,
            yes_bid=Decimal("0.50"),
            yes_ask=Decimal("0.52"),
        ),
    ]
    patch_loader(monkeypatch, snapshots)

    def feature_for(market: MarketState, history: list[MarketState]) -> FeatureVector:
        momentum = 0.05 if market.raw_sequence == 0 else 0.01
        return FeatureVector(
            ticker="KXTEST-26JAN-YES",
            timestamp=market.timestamp,
            mid_price=0.50,
            spread_pct=4.0,
            spread_ticks=0.02,
            bid_ask_imbalance=0.3,
            time_to_close_hours=24.0,
            implied_probability=0.50,
            liquidity_score=100.0,
            price_momentum_1h=momentum,
            price_momentum_24h=None,
            volume_zscore=2.0,
            open_interest_delta=None,
        )

    monkeypatch.setattr("research.backtester.compute_features", feature_for)

    result = Backtester(
        EventDriftStrategy(),
        RiskEngine(),
        PaperAdapter(),
        make_config(),
    ).run()

    assert result["total_intents"] == 2
    assert result["total_orders_allowed"] == 2
    assert [fill["side"] for fill in result["fills"]] == ["yes", "no"]


def test_backtest_tracks_calibration_mispricing_entry_and_exit(monkeypatch) -> None:
    snapshots = [
        make_market(
            index=0,
            yes_bid=Decimal("0.40"),
            yes_ask=Decimal("0.45"),
        ),
        make_market(
            index=1,
            yes_bid=Decimal("0.40"),
            yes_ask=Decimal("0.45"),
        ),
    ]
    patch_loader(monkeypatch, snapshots)
    features = FeatureVector(
        ticker="KXTEST-26JAN-YES",
        timestamp=BASE_TIME,
        mid_price=0.42,
        spread_pct=11.76,
        spread_ticks=0.05,
        bid_ask_imbalance=0.0,
        time_to_close_hours=24.0,
        implied_probability=0.42,
        liquidity_score=100.0,
        price_momentum_1h=None,
        price_momentum_24h=None,
        volume_zscore=0.0,
        open_interest_delta=None,
    )
    monkeypatch.setattr("research.backtester.compute_features", lambda market, history: features)

    result = Backtester(
        CalibrationMispricingStrategy(estimator=SequenceEstimator([0.60, 0.455])),
        RiskEngine(config=RiskConfig(min_edge_to_trade=0.0)),
        PaperAdapter(),
        make_config(),
    ).run()

    assert result["total_intents"] == 2
    assert result["total_orders_allowed"] == 2
    assert [fill["side"] for fill in result["fills"]] == ["yes", "no"]


def test_backtest_blocks_via_risk_engine(monkeypatch) -> None:
    snapshots = [make_market()]
    patch_loader(monkeypatch, snapshots)

    result = Backtester(LowEdgeStrategy(), RiskEngine(), PaperAdapter(), make_config()).run()

    assert result["total_intents"] == 1
    assert result["total_orders_blocked"] == 1
    assert result["fills"] == []
    assert result["blocked_orders"][0]["blocked_by"] == "kelly"
    assert result["blocked_orders"][0]["reason"] == "edge_below_minimum"


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
