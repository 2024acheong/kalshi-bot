from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.schemas.market import FeatureVector, MarketState, MarketStatus
from core.strategies.spread_capture import (
    SpreadCaptureIntent,
    SpreadCaptureStrategy,
    detect_implied_probability_arbitrage,
)


BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def make_market(**kwargs) -> MarketState:
    defaults = {
        "ticker": "KXTEST-26JAN-YES",
        "timestamp": BASE_TIME,
        "yes_bid": Decimal("0.40"),
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
        "spread_pct": 4.0,
        "spread_ticks": 0.02,
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


def test_detect_arbitrage_uses_integer_cents_for_fee_rounding() -> None:
    market = make_market(yes_ask=Decimal("0.40"), no_ask=Decimal("0.45"))

    is_arbitrage, locked_profit, total_profit = detect_implied_probability_arbitrage(market, qty=10)

    assert is_arbitrage is True
    # 1000c payout - 850c cost - 140c fees = 10c locked profit total -> 0.01/contract
    assert locked_profit == Decimal("0.01")


def test_detect_arbitrage_when_prices_sum_below_one() -> None:
    market = make_market(yes_ask=Decimal("0.40"), no_ask=Decimal("0.45"))

    is_arbitrage, locked_profit, total_profit = detect_implied_probability_arbitrage(market, qty=10)

    assert is_arbitrage is True
    assert locked_profit > Decimal("0")
    assert total_profit > 0


def test_no_arbitrage_when_prices_sum_at_or_above_one() -> None:
    market = make_market(yes_ask=Decimal("0.52"), no_ask=Decimal("0.50"))

    is_arbitrage, locked_profit, total_profit = detect_implied_probability_arbitrage(market, qty=10)

    assert is_arbitrage is False
    assert locked_profit == Decimal("0")


def test_no_arbitrage_when_fees_erase_thin_margin() -> None:
    market = make_market(yes_ask=Decimal("0.48"), no_ask=Decimal("0.45"))

    is_arbitrage, locked_profit, total_profit = detect_implied_probability_arbitrage(market, qty=10)

    assert is_arbitrage is False
    assert locked_profit == Decimal("0")


def test_detect_arbitrage_returns_false_on_missing_no_data() -> None:
    market = make_market(no_ask=None)

    assert detect_implied_probability_arbitrage(market, qty=10) == (
        False,
        Decimal("0"),
        0,
    )


def test_evaluate_arbitrage_entry_returns_none_when_no_arbitrage() -> None:
    result = SpreadCaptureStrategy(
        min_profit_cents_total=0,
        min_profit_per_contract=0,
    ).evaluate_arbitrage_entry(
        make_market(yes_ask=Decimal("0.52"), no_ask=Decimal("0.50")),
        make_features(),
        run_id="run-1",
    )

    assert result is None


def test_evaluate_arbitrage_entry_returns_immediate_intents() -> None:
    market = make_market(yes_ask=Decimal("0.40"), no_ask=Decimal("0.45"))

    result = SpreadCaptureStrategy(
        min_profit_cents_total=0,
        min_profit_per_contract=0,
    ).evaluate_arbitrage_entry(
        market,
        make_features(),
        run_id="run-1",
        qty=7,
    )

    assert isinstance(result, SpreadCaptureIntent)
    assert result.max_resting_seconds == 0
    assert result.yes_intent.side == "yes"
    assert result.no_intent.side == "no"
    assert result.yes_intent.price == market.yes_ask
    assert result.no_intent.price == market.no_ask
    assert result.yes_intent.qty == 7
    assert result.no_intent.qty == 7
    assert result.yes_intent.signal_id == result.pair_id
    assert result.no_intent.signal_id == result.pair_id


def test_evaluate_arbitrage_entry_respects_time_to_close() -> None:
    result = SpreadCaptureStrategy(
        min_hours_to_close=1.0,
        min_profit_cents_total=0,
        min_profit_per_contract=0,
    ).evaluate_arbitrage_entry(
        make_market(yes_ask=Decimal("0.40"), no_ask=Decimal("0.45")),
        make_features(time_to_close_hours=0.25),
        run_id="run-1",
    )

    assert result is None
