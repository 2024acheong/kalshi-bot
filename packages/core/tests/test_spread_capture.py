from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.risk.engine import OrderIntent
from core.schemas.market import FeatureVector, MarketState, MarketStatus
from core.strategies.spread_capture import SpreadCaptureIntent, SpreadCaptureStrategy


BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def make_market(**kwargs) -> MarketState:
    defaults = {
        "ticker": "KXTEST-26JAN-YES",
        "timestamp": BASE_TIME,
        "yes_bid": Decimal("0.45"),
        "yes_ask": Decimal("0.55"),
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
        "spread_pct": 20.0,
        "spread_ticks": 0.10,
        "bid_ask_imbalance": 0.0,
        "time_to_close_hours": 24.0,
        "implied_probability": 0.50,
        "liquidity_score": 100.0,
        "price_momentum_1h": None,
        "price_momentum_24h": None,
        "volume_zscore": None,
        "open_interest_delta": None,
    }
    defaults.update(kwargs)
    return FeatureVector(**defaults)


def test_holds_on_narrow_spread() -> None:
    result = SpreadCaptureStrategy().evaluate(
        make_market(),
        make_features(spread_pct=1.0),
        run_id="run-1",
    )

    assert result is None


def test_holds_on_imbalanced_book() -> None:
    result = SpreadCaptureStrategy().evaluate(
        make_market(),
        make_features(bid_ask_imbalance=0.5),
        run_id="run-1",
    )

    assert result is None


def test_holds_on_insufficient_time() -> None:
    result = SpreadCaptureStrategy().evaluate(
        make_market(),
        make_features(time_to_close_hours=0.1),
        run_id="run-1",
    )

    assert result is None


def test_holds_on_degenerate_book() -> None:
    result = SpreadCaptureStrategy().evaluate(
        make_market(yes_bid=Decimal("0")),
        make_features(),
        run_id="run-1",
    )

    assert result is None


def test_enters_on_favorable_conditions() -> None:
    result = SpreadCaptureStrategy().evaluate(make_market(), make_features(), run_id="run-1")

    assert isinstance(result, SpreadCaptureIntent)
    assert isinstance(result.yes_intent, OrderIntent)
    assert isinstance(result.no_intent, OrderIntent)
    assert result.yes_intent.side == "yes"
    assert result.no_intent.side == "no"
    assert result.yes_intent.qty == 10
    assert result.no_intent.qty == 10
    assert result.yes_intent.run_id == "run-1"
    assert result.no_intent.run_id == "run-1"
    assert result.max_resting_seconds == 30
    assert result.yes_intent.signal_id == result.pair_id
    assert result.no_intent.signal_id == result.pair_id


def test_yes_intent_priced_at_bid() -> None:
    market = make_market(yes_bid=Decimal("0.42"))

    result = SpreadCaptureStrategy().evaluate(market, make_features(), run_id="run-1")

    assert result is not None
    assert result.yes_intent.price == market.yes_bid


def test_no_intent_priced_at_ask() -> None:
    market = make_market(yes_ask=Decimal("0.58"))

    result = SpreadCaptureStrategy().evaluate(market, make_features(), run_id="run-1")

    assert result is not None
    assert result.no_intent.price == market.yes_ask


def test_estimated_edge_is_half_spread() -> None:
    market = make_market(yes_bid=Decimal("0.40"), yes_ask=Decimal("0.52"))

    result = SpreadCaptureStrategy().evaluate(market, make_features(), run_id="run-1")

    assert result is not None
    assert result.yes_intent.estimated_edge == pytest.approx(0.06)
    assert result.no_intent.estimated_edge == pytest.approx(0.06)


def test_pair_id_is_unique() -> None:
    strategy = SpreadCaptureStrategy()
    first = strategy.evaluate(make_market(), make_features(), run_id="run-1")
    second = strategy.evaluate(make_market(), make_features(), run_id="run-1")

    assert first is not None
    assert second is not None
    assert first.pair_id != second.pair_id
