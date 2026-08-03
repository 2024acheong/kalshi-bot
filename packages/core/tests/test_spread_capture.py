from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.schemas.market import FeatureVector, MarketState, MarketStatus
from core.strategies.spread_capture import SpreadCaptureIntent, SpreadCaptureStrategy


BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def make_market(**kwargs) -> MarketState:
    defaults = {
        "ticker": "KXTEST-26JAN-YES",
        "timestamp": BASE_TIME,
        "yes_bid": Decimal("0.45"),
        "yes_ask": Decimal("0.40"),
        "yes_bid_size": 100,
        "yes_ask_size": 100,
        "last_price": Decimal("0.50"),
        "volume_24h": 1000,
        "open_interest": 5000,
        "close_time": BASE_TIME + timedelta(hours=24),
        "status": MarketStatus.OPEN,
        "source": "rest_poll",
        "no_bid": Decimal("0.50"),
        "no_ask": Decimal("0.45"),
        "no_bid_size": 100,
        "no_ask_size": 100,
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


def test_evaluate_ignores_passive_spread_conditions_without_arbitrage() -> None:
    result = SpreadCaptureStrategy(
        min_profit_cents_total=0,
        min_profit_per_contract=0,
    ).evaluate(
        make_market(
            yes_bid=Decimal("0.40"),
            yes_ask=Decimal("0.60"),
            no_bid=Decimal("0.39"),
            no_ask=Decimal("0.42"),
        ),
        make_features(spread_pct=50.0, bid_ask_imbalance=0.0),
        run_id="run-1",
    )

    assert result is None


def test_holds_on_insufficient_time() -> None:
    result = SpreadCaptureStrategy(
        min_profit_cents_total=0,
        min_profit_per_contract=0,
    ).evaluate(
        make_market(),
        make_features(time_to_close_hours=0.1),
        run_id="run-1",
    )

    assert result is None


def test_holds_when_fees_erase_arbitrage() -> None:
    result = SpreadCaptureStrategy(
        min_profit_cents_total=0,
        min_profit_per_contract=0,
    ).evaluate(
        make_market(yes_ask=Decimal("0.48"), no_ask=Decimal("0.45")),
        make_features(),
        run_id="run-1",
    )

    assert result is None


def test_enters_only_on_true_arbitrage() -> None:
    result = SpreadCaptureStrategy(
        min_profit_cents_total=0,
        min_profit_per_contract=0,
    ).evaluate(make_market(), make_features(), run_id="run-1")

    assert isinstance(result, SpreadCaptureIntent)
    assert result.yes_intent.side == "yes"
    assert result.no_intent.side == "no"
    assert result.yes_intent.qty == 10
    assert result.no_intent.qty == 10
    assert result.yes_intent.run_id == "run-1"
    assert result.no_intent.run_id == "run-1"
    assert result.max_resting_seconds == 0
    assert result.yes_intent.signal_id == result.pair_id
    assert result.no_intent.signal_id == result.pair_id


def test_arbitrage_intents_cross_yes_and_no_asks() -> None:
    market = make_market(yes_ask=Decimal("0.40"), no_ask=Decimal("0.45"))

    result = SpreadCaptureStrategy(
        min_profit_cents_total=0,
        min_profit_per_contract=0,
    ).evaluate(market, make_features(), run_id="run-1")

    assert result is not None
    assert result.yes_intent.price == market.yes_ask
    assert result.no_intent.price == market.no_ask


def test_pair_id_is_unique() -> None:
    strategy = SpreadCaptureStrategy(
        min_profit_cents_total=0,
        min_profit_per_contract=0,
    )
    first = strategy.evaluate(make_market(), make_features(), run_id="run-1")
    second = strategy.evaluate(make_market(), make_features(), run_id="run-1")

    assert first is not None
    assert second is not None
    assert first.pair_id != second.pair_id
