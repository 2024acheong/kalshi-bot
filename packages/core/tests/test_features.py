from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.features.compute import (
    compute_bid_ask_imbalance,
    compute_features,
    compute_mid_price,
    compute_open_interest_delta,
    compute_price_momentum,
    compute_spread_pct,
    compute_spread_ticks,
    compute_time_to_close_hours,
    compute_volume_zscore,
)
from core.schemas.market import MarketState, MarketStatus


BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def make_market(**kwargs) -> MarketState:
    defaults = {
        "ticker": "KXTEST-26JAN-YES",
        "timestamp": BASE_TIME,
        "yes_bid": Decimal("0.44"),
        "yes_ask": Decimal("0.48"),
        "yes_bid_size": 100,
        "yes_ask_size": 100,
        "last_price": Decimal("0.46"),
        "volume_24h": 100,
        "open_interest": 1000,
        "close_time": BASE_TIME + timedelta(hours=24),
        "status": MarketStatus.OPEN,
        "source": "rest_poll",
    }
    defaults.update(kwargs)
    return MarketState(**defaults)


def test_mid_price():
    assert compute_mid_price(make_market()) == pytest.approx(0.46)


def test_mid_price_missing_bid():
    assert compute_mid_price(make_market(yes_bid=None)) is None


def test_spread_ticks():
    assert compute_spread_ticks(make_market()) == pytest.approx(0.04)


def test_spread_pct():
    assert compute_spread_pct(make_market()) == pytest.approx(8.695, rel=1e-3)


def test_bid_ask_imbalance_balanced():
    assert compute_bid_ask_imbalance(make_market()) == pytest.approx(0.0)


def test_bid_ask_imbalance_bid_heavy():
    market = make_market(yes_bid_size=150, yes_ask_size=50)
    assert compute_bid_ask_imbalance(market) == pytest.approx(0.5)


def test_bid_ask_imbalance_missing():
    market = make_market(yes_bid_size=None, yes_ask_size=None)
    assert compute_bid_ask_imbalance(market) is None


def test_time_to_close():
    market = make_market(close_time=BASE_TIME + timedelta(hours=12))
    assert compute_time_to_close_hours(market) == pytest.approx(12.0)


def test_time_to_close_already_closed():
    market = make_market(close_time=BASE_TIME - timedelta(hours=1))
    assert compute_time_to_close_hours(market) == pytest.approx(0.0)


def test_time_to_close_none():
    assert compute_time_to_close_hours(make_market(close_time=None)) is None


def test_price_momentum_rising():
    history = [
        make_market(yes_bid=Decimal("0.54"), yes_ask=Decimal("0.56")),
        make_market(
            timestamp=BASE_TIME - timedelta(minutes=30),
            yes_bid=Decimal("0.44"),
            yes_ask=Decimal("0.46"),
        ),
    ]
    assert compute_price_momentum(history) > 0


def test_price_momentum_falling():
    history = [
        make_market(yes_bid=Decimal("0.34"), yes_ask=Decimal("0.36")),
        make_market(
            timestamp=BASE_TIME - timedelta(minutes=30),
            yes_bid=Decimal("0.44"),
            yes_ask=Decimal("0.46"),
        ),
    ]
    assert compute_price_momentum(history) < 0


def test_price_momentum_insufficient_history():
    assert compute_price_momentum([make_market()]) is None


def test_volume_zscore_spike():
    history = [make_market(volume_24h=1000)]
    history.extend(
        make_market(timestamp=BASE_TIME - timedelta(minutes=i), volume_24h=100)
        for i in range(1, 20)
    )
    assert compute_volume_zscore(history) > 2.0


def test_volume_zscore_insufficient():
    history = [
        make_market(volume_24h=100),
        make_market(timestamp=BASE_TIME - timedelta(minutes=1), volume_24h=100),
    ]
    assert compute_volume_zscore(history) is None


def test_compute_features_no_history():
    features = compute_features(make_market())
    assert features.mid_price == pytest.approx(0.46)
    assert features.price_momentum_1h is None
    assert features.price_momentum_24h is None
    assert features.volume_zscore is None


def test_compute_features_with_history():
    history = [make_market(volume_24h=140, open_interest=1020)]
    history.extend(
        make_market(
            timestamp=BASE_TIME - timedelta(minutes=i),
            yes_bid=Decimal("0.40"),
            yes_ask=Decimal("0.42"),
            volume_24h=100 + i,
            open_interest=1000,
        )
        for i in range(1, 4)
    )

    features = compute_features(history[0], history)

    assert features.price_momentum_1h is not None
    assert features.price_momentum_24h is not None
    assert features.volume_zscore is not None
    assert features.open_interest_delta == pytest.approx(20.0)


def test_compute_features_deterministic():
    history = [
        make_market(volume_24h=140, open_interest=1020),
        make_market(
            timestamp=BASE_TIME - timedelta(minutes=30),
            yes_bid=Decimal("0.40"),
            yes_ask=Decimal("0.42"),
            volume_24h=100,
            open_interest=1000,
        ),
        make_market(
            timestamp=BASE_TIME - timedelta(minutes=45),
            volume_24h=90,
            open_interest=990,
        ),
    ]

    assert compute_features(history[0], history) == compute_features(history[0], history)
