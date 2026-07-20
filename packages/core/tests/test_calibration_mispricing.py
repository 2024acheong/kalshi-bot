from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.schemas.market import FeatureVector, MarketState, MarketStatus
from core.strategies.calibration_mispricing import (
    CalibrationMispricingPosition,
    CalibrationMispricingStrategy,
    NaiveMidpointDriftEstimator,
    ProbabilityEstimator,
    compute_brier_linear_size,
    is_within_no_bet_zone,
)


BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


class FixedEstimator(ProbabilityEstimator):
    def __init__(self, value: float | None):
        self.value = value

    def estimate(self, market: MarketState, features: FeatureVector) -> float | None:
        return self.value


def make_market(**kwargs) -> MarketState:
    defaults = {
        "ticker": "KXTEST-26JAN-YES",
        "timestamp": BASE_TIME,
        "yes_bid": Decimal("0.40"),
        "yes_ask": Decimal("0.45"),
        "yes_bid_size": 100,
        "yes_ask_size": 100,
        "last_price": Decimal("0.42"),
        "volume_24h": 1000,
        "open_interest": 5000,
        "close_time": BASE_TIME + timedelta(hours=24),
        "status": MarketStatus.OPEN,
        "source": "rest_poll",
    }
    defaults.update(kwargs)
    return MarketState(**defaults)


def make_features(**kwargs) -> FeatureVector:
    defaults = {
        "ticker": "KXTEST-26JAN-YES",
        "timestamp": BASE_TIME,
        "mid_price": 0.42,
        "spread_pct": 11.76,
        "spread_ticks": 0.05,
        "bid_ask_imbalance": 0.0,
        "time_to_close_hours": 24.0,
        "implied_probability": 0.42,
        "liquidity_score": 100.0,
        "price_momentum_1h": None,
        "price_momentum_24h": None,
        "volume_zscore": 0.0,
        "open_interest_delta": None,
    }
    defaults.update(kwargs)
    return FeatureVector(**defaults)


def make_position(**kwargs) -> CalibrationMispricingPosition:
    defaults = {
        "ticker": "KXTEST-26JAN-YES",
        "side": "yes",
        "entry_price": Decimal("0.45"),
        "entry_model_prob": 0.55,
        "qty": 10,
        "opened_at": BASE_TIME,
    }
    defaults.update(kwargs)
    return CalibrationMispricingPosition(**defaults)


def test_naive_estimator_returns_mid_price_when_no_momentum() -> None:
    estimate = NaiveMidpointDriftEstimator().estimate(make_market(), make_features())

    assert estimate == 0.42


def test_naive_estimator_nudges_with_momentum() -> None:
    estimate = NaiveMidpointDriftEstimator(momentum_weight=0.1).estimate(
        make_market(),
        make_features(mid_price=0.50, price_momentum_1h=0.20),
    )

    assert estimate == 0.52


def test_naive_estimator_clips_to_valid_range() -> None:
    estimator = NaiveMidpointDriftEstimator(momentum_weight=0.1)

    assert estimator.estimate(
        make_market(),
        make_features(mid_price=0.98, price_momentum_1h=0.50),
    ) == 0.99
    assert estimator.estimate(
        make_market(),
        make_features(mid_price=0.02, price_momentum_1h=-0.50),
    ) == 0.01


def test_brier_linear_size_scales_with_margin() -> None:
    small = compute_brier_linear_size(0.50, 0.45, max_qty=20)
    large = compute_brier_linear_size(0.70, 0.45, max_qty=20)

    assert small < large


def test_brier_linear_size_zero_margin_returns_zero() -> None:
    assert compute_brier_linear_size(0.45, 0.45, max_qty=20) == 0


def test_brier_linear_size_never_negative() -> None:
    assert compute_brier_linear_size(0.10, 0.90, max_qty=20) >= 0


def test_no_bet_zone_true_within_spread() -> None:
    assert is_within_no_bet_zone(0.42, Decimal("0.40"), Decimal("0.45")) is True


def test_no_bet_zone_false_above_ask() -> None:
    assert is_within_no_bet_zone(0.50, Decimal("0.40"), Decimal("0.45")) is False


def test_no_bet_zone_false_below_bid() -> None:
    assert is_within_no_bet_zone(0.35, Decimal("0.40"), Decimal("0.45")) is False


def test_entry_buys_yes_when_prob_above_ask() -> None:
    intent = CalibrationMispricingStrategy(
        estimator=FixedEstimator(0.60),
        max_qty=20,
    ).evaluate_entry(make_market(), make_features(), run_id="run-1")

    assert intent is not None
    assert intent.side == "yes"
    assert intent.price == Decimal("0.45")
    assert intent.qty == 3
    assert intent.estimated_edge == pytest.approx(0.15)
    assert intent.model_prob == 0.60


def test_entry_buys_no_when_prob_below_bid() -> None:
    intent = CalibrationMispricingStrategy(
        estimator=FixedEstimator(0.25),
        max_qty=20,
    ).evaluate_entry(make_market(), make_features(), run_id="run-1")

    assert intent is not None
    assert intent.side == "no"
    assert intent.price == Decimal("0.40")
    assert intent.qty == 3
    assert intent.estimated_edge == pytest.approx(0.15)
    assert intent.model_prob == 0.25


def test_no_entry_within_no_bet_zone() -> None:
    intent = CalibrationMispricingStrategy(
        estimator=FixedEstimator(0.42),
    ).evaluate_entry(make_market(), make_features(), run_id="run-1")

    assert intent is None


def test_no_entry_insufficient_time() -> None:
    intent = CalibrationMispricingStrategy(
        estimator=FixedEstimator(0.60),
    ).evaluate_entry(
        make_market(),
        make_features(time_to_close_hours=0.1),
        run_id="run-1",
    )

    assert intent is None


def test_exit_when_edge_shrinks() -> None:
    intent = CalibrationMispricingStrategy(
        estimator=FixedEstimator(0.455),
        exit_edge_threshold=0.01,
    ).evaluate_exit(
        make_position(side="yes"),
        make_market(),
        make_features(),
        as_of=BASE_TIME + timedelta(minutes=5),
    )

    assert intent is not None
    assert intent.side == "no"


def test_exit_intent_has_is_closing_order_true() -> None:
    intent = CalibrationMispricingStrategy(
        estimator=FixedEstimator(0.455),
    ).evaluate_exit(
        make_position(side="yes"),
        make_market(),
        make_features(),
        as_of=BASE_TIME + timedelta(minutes=5),
    )

    assert intent is not None
    assert intent.is_closing_order is True


def test_exit_when_estimator_returns_none() -> None:
    intent = CalibrationMispricingStrategy(
        estimator=FixedEstimator(None),
    ).evaluate_exit(
        make_position(side="yes"),
        make_market(),
        make_features(),
        as_of=BASE_TIME + timedelta(minutes=5),
    )

    assert intent is not None
