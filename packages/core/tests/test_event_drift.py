from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.schemas.market import FeatureVector, MarketState, MarketStatus
from core.strategies.event_drift import EventDriftPosition, EventDriftStrategy


BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def make_market(**kwargs) -> MarketState:
    defaults = {
        "ticker": "KXTEST-26JAN-YES",
        "timestamp": BASE_TIME,
        "yes_bid": Decimal("0.49"),
        "yes_ask": Decimal("0.51"),
        "yes_bid_size": 100,
        "yes_ask_size": 100,
        "last_price": Decimal("0.50"),
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
        "mid_price": 0.50,
        "spread_pct": 4.0,
        "spread_ticks": 0.02,
        "bid_ask_imbalance": 0.3,
        "time_to_close_hours": 24.0,
        "implied_probability": 0.50,
        "liquidity_score": 100.0,
        "price_momentum_1h": 0.05,
        "price_momentum_24h": None,
        "volume_zscore": 2.0,
        "open_interest_delta": None,
    }
    defaults.update(kwargs)
    return FeatureVector(**defaults)


def make_position(**kwargs) -> EventDriftPosition:
    defaults = {
        "ticker": "KXTEST-26JAN-YES",
        "side": "yes",
        "entry_price": Decimal("0.51"),
        "entry_mid_price": Decimal("0.50"),
        "entry_momentum": 0.05,
        "qty": 10,
        "opened_at": BASE_TIME,
    }
    defaults.update(kwargs)
    return EventDriftPosition(**defaults)


def test_no_entry_below_momentum_threshold() -> None:
    intent = EventDriftStrategy().evaluate_entry(
        make_market(),
        make_features(price_momentum_1h=0.01),
        run_id="run-1",
    )

    assert intent is None


def test_no_entry_when_imbalance_does_not_confirm() -> None:
    intent = EventDriftStrategy().evaluate_entry(
        make_market(),
        make_features(price_momentum_1h=0.05, bid_ask_imbalance=0.05),
        run_id="run-1",
    )

    assert intent is None


def test_no_entry_on_low_volume() -> None:
    intent = EventDriftStrategy().evaluate_entry(
        make_market(),
        make_features(volume_zscore=0.5),
        run_id="run-1",
    )

    assert intent is None


def test_no_entry_insufficient_time() -> None:
    intent = EventDriftStrategy().evaluate_entry(
        make_market(),
        make_features(time_to_close_hours=0.1),
        run_id="run-1",
    )

    assert intent is None


def test_follows_upward_momentum_buys_yes_side() -> None:
    intent = EventDriftStrategy().evaluate_entry(
        make_market(),
        make_features(
            price_momentum_1h=0.05,
            bid_ask_imbalance=0.3,
            volume_zscore=2.0,
        ),
        run_id="run-1",
    )

    assert intent is not None
    assert intent.side == "yes"
    assert intent.price == Decimal("0.51")
    assert intent.estimated_edge == 0.05


def test_follows_downward_momentum_sells_no_side() -> None:
    intent = EventDriftStrategy().evaluate_entry(
        make_market(),
        make_features(
            price_momentum_1h=-0.05,
            bid_ask_imbalance=-0.3,
            volume_zscore=2.0,
        ),
        run_id="run-1",
    )

    assert intent is not None
    assert intent.side == "no"
    assert intent.price == Decimal("0.49")
    assert intent.estimated_edge == 0.05


def test_exit_on_momentum_exhaustion_yes_side() -> None:
    intent = EventDriftStrategy().evaluate_exit(
        make_position(side="yes", entry_momentum=0.05),
        make_market(),
        make_features(price_momentum_1h=0.01),
        as_of=BASE_TIME + timedelta(minutes=5),
    )

    assert intent is not None
    assert intent.side == "no"


def test_exit_on_momentum_exhaustion_no_side() -> None:
    strategy = EventDriftStrategy()
    threshold = -0.05 * strategy.exhaustion_threshold_pct
    assert -0.01 > threshold

    intent = strategy.evaluate_exit(
        make_position(side="no", entry_momentum=-0.05),
        make_market(),
        make_features(price_momentum_1h=-0.01),
        as_of=BASE_TIME + timedelta(minutes=5),
    )

    assert intent is not None
    assert intent.side == "yes"


def test_exit_on_approaching_close() -> None:
    intent = EventDriftStrategy().evaluate_exit(
        make_position(side="yes", entry_momentum=0.05),
        make_market(),
        make_features(price_momentum_1h=0.05, time_to_close_hours=0.2),
        as_of=BASE_TIME + timedelta(minutes=5),
    )

    assert intent is not None


def test_no_exit_when_momentum_still_strong() -> None:
    intent = EventDriftStrategy().evaluate_exit(
        make_position(side="yes", entry_momentum=0.05),
        make_market(),
        make_features(price_momentum_1h=0.04),
        as_of=BASE_TIME + timedelta(minutes=5),
    )

    assert intent is None


def test_closing_intent_has_is_closing_order_true() -> None:
    intent = EventDriftStrategy().evaluate_exit(
        make_position(side="yes", entry_momentum=0.05),
        make_market(),
        make_features(price_momentum_1h=0.01),
        as_of=BASE_TIME + timedelta(minutes=5),
    )

    assert intent is not None
    assert intent.is_closing_order is True
