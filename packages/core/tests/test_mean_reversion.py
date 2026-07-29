from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.schemas.market import FeatureVector, MarketState, MarketStatus
from core.strategies.mean_reversion import MeanReversionPosition, MeanReversionStrategy


BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def make_market(**kwargs) -> MarketState:
    defaults = {
        "ticker": "KXTEST-26JAN-YES",
        "timestamp": BASE_TIME,
        "yes_bid": Decimal("0.49"),
        "yes_ask": Decimal("0.51"),
        "yes_bid_size": 100,
        "yes_ask_size": 100,
        "no_bid": Decimal("0.49"),  # ADDED
        "no_ask": Decimal("0.51"),  # ADDED
        "no_bid_size": 100,         # ADDED
        "no_ask_size": 100,         # ADDED
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
        "bid_ask_imbalance": 0.0,
        "time_to_close_hours": 24.0,
        "implied_probability": 0.50,
        "liquidity_score": 100.0,
        "price_momentum_1h": 0.05,
        "price_momentum_24h": None,
        "volume_zscore": 0.0,
        "open_interest_delta": None,
    }
    defaults.update(kwargs)
    return FeatureVector(**defaults)


def make_position(**kwargs) -> MeanReversionPosition:
    defaults = {
        "ticker": "KXTEST-26JAN-YES",
        "side": "no",
        "entry_price": Decimal("0.49"),
        "entry_mid_price": Decimal("0.50"),
        "entry_spread_ticks": Decimal("0.02"),
        "qty": 10,
        "opened_at": BASE_TIME,
    }
    defaults.update(kwargs)
    return MeanReversionPosition(**defaults)


def test_no_entry_below_momentum_threshold() -> None:
    intent = MeanReversionStrategy().evaluate_entry(
        make_market(),
        make_features(price_momentum_1h=0.01),
        run_id="run-1",
    )

    assert intent is None


def test_no_entry_when_imbalance_confirms_upward_momentum() -> None:
    intent = MeanReversionStrategy().evaluate_entry(
        make_market(),
        make_features(price_momentum_1h=0.05, bid_ask_imbalance=0.5),
        run_id="run-1",
    )

    assert intent is None


def test_no_entry_on_high_volume_zscore() -> None:
    intent = MeanReversionStrategy().evaluate_entry(
        make_market(),
        make_features(volume_zscore=3.0),
        run_id="run-1",
    )

    assert intent is None


def test_no_entry_insufficient_time() -> None:
    intent = MeanReversionStrategy().evaluate_entry(
        make_market(),
        make_features(time_to_close_hours=0.1),
        run_id="run-1",
    )

    assert intent is None


def test_fade_upward_momentum_sells_no_side() -> None:
    intent = MeanReversionStrategy().evaluate_entry(
        make_market(),
        make_features(price_momentum_1h=0.05, bid_ask_imbalance=0.0),
        run_id="run-1",
    )

    assert intent is not None
    assert intent.side == "no"
    assert intent.price == Decimal("0.51")
    assert intent.estimated_edge == 0.05


def test_fade_downward_momentum_buys_yes_side() -> None:
    intent = MeanReversionStrategy().evaluate_entry(
        make_market(),
        make_features(price_momentum_1h=-0.05, bid_ask_imbalance=0.0),
        run_id="run-1",
    )

    assert intent is not None
    assert intent.side == "yes"
    assert intent.price == Decimal("0.51")
    assert intent.estimated_edge == 0.05


def test_exit_on_reversion_achieved_no_side() -> None:
    intent = MeanReversionStrategy().evaluate_exit(
        make_position(side="no", entry_mid_price=Decimal("0.50")),
        make_market(yes_bid=Decimal("0.47"), yes_ask=Decimal("0.49"),
            no_bid=Decimal("0.53"), no_ask=Decimal("0.55")),
        as_of=BASE_TIME + timedelta(minutes=5),
    )

    assert intent is not None
    assert intent.is_closing_order is True


def test_exit_on_stop_loss_no_side() -> None:
    intent = MeanReversionStrategy().evaluate_exit(
        make_position(
            side="no",
            entry_mid_price=Decimal("0.50"),
            entry_spread_ticks=Decimal("0.02"),
        ),
        make_market(yes_bid=Decimal("0.54"), yes_ask=Decimal("0.56"),
            no_bid=Decimal("0.44"), no_ask=Decimal("0.46")),
        as_of=BASE_TIME + timedelta(minutes=5),
    )

    assert intent is not None


def test_no_exit_when_neither_condition_met() -> None:
    intent = MeanReversionStrategy().evaluate_exit(
        make_position(side="no", entry_mid_price=Decimal("0.50")),
        make_market(
            yes_bid=Decimal("0.49"),
            yes_ask=Decimal("0.51"),
            no_bid=Decimal("0.49"),
            no_ask=Decimal("0.51"),
        ),
        as_of=BASE_TIME + timedelta(minutes=5),
    )

    assert intent is None


def test_no_exit_when_mid_reverts_but_executable_price_does_not() -> None:
    """Under direct position sale, only the NO order book determines the exit signal — YES-side volatility alone shouldn't trigger an exit"""
    intent = MeanReversionStrategy().evaluate_exit(
        make_position(side="no", entry_mid_price=Decimal("0.50")),
        make_market(yes_bid=Decimal("0.40"), yes_ask=Decimal("0.56"),
            no_bid=Decimal("0.49"), no_ask=Decimal("0.51")),  # NO side unchanged from entry,
        as_of=BASE_TIME + timedelta(minutes=5),
    )

    assert intent is None


def test_exit_on_stop_loss_when_executable_price_spikes() -> None:
    intent = MeanReversionStrategy().evaluate_exit(
        make_position(
            side="no",
            entry_mid_price=Decimal("0.45"),
            entry_spread_ticks=Decimal("0.02"),
        ),
        make_market(yes_bid=Decimal("0.10"), yes_ask=Decimal("0.80"),
            no_bid=Decimal("0.10"), no_ask=Decimal("0.15")),
        as_of=BASE_TIME + timedelta(minutes=5),
    )

    assert intent is not None
    assert intent.price == Decimal("0.10")


def test_closing_intent_has_same_side() -> None:
    strategy = MeanReversionStrategy()
    no_exit = strategy.evaluate_exit(
        make_position(side="no", entry_mid_price=Decimal("0.50")),
        make_market(
            yes_bid=Decimal("0.47"), yes_ask=Decimal("0.49"),
            no_bid=Decimal("0.53"), no_ask=Decimal("0.55"),
        ),
        as_of=BASE_TIME + timedelta(minutes=5),
    )
    yes_exit = strategy.evaluate_exit(
        make_position(side="yes", entry_mid_price=Decimal("0.50")),
        make_market(yes_bid=Decimal("0.51"), yes_ask=Decimal("0.53")),
        as_of=BASE_TIME + timedelta(minutes=5),
    )

    assert no_exit is not None
    assert no_exit.side == "no"
    assert yes_exit is not None
    assert yes_exit.side == "yes"
