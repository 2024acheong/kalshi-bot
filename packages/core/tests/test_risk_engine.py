from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.risk.engine import (
    OrderIntent,
    RiskConfig,
    RiskEngine,
    check_concentration,
    check_correlation,
    check_drawdown,
    check_kelly,
    check_liquidity,
)
from core.schemas.market import FeatureVector, MarketState, MarketStatus, RiskDecision


BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def make_intent(**kwargs) -> OrderIntent:
    defaults = {
        "ticker": "KXTEST-26JAN-YES",
        "side": "yes",
        "price": Decimal("0.50"),
        "qty": 20,
        "estimated_edge": 0.08,
        "model_prob": 0.58,
        "run_id": "run-1",
        "signal_id": "signal-1",
    }
    defaults.update(kwargs)
    return OrderIntent(**defaults)


def make_market(**kwargs) -> MarketState:
    defaults = {
        "ticker": "KXTEST-26JAN-YES",
        "timestamp": BASE_TIME,
        "yes_bid": Decimal("0.49"),
        "yes_ask": Decimal("0.51"),
        "yes_bid_size": 200,
        "yes_ask_size": 200,
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
        "liquidity_score": 10.0,
        "price_momentum_1h": None,
        "price_momentum_24h": None,
        "volume_zscore": None,
        "open_interest_delta": None,
    }
    defaults.update(kwargs)
    return FeatureVector(**defaults)


def make_context(**kwargs) -> dict:
    context = {
        "intent": make_intent(),
        "market": make_market(),
        "features": make_features(),
        "open_positions": [],
        "market_category": "crypto",
        "portfolio_value_usd": 1000.0,
        "current_exposure_usd": 100.0,
        "daily_realized_pnl_usd": 0.0,
        "kill_switch_active": False,
        "global_kill_switch": False,
    }
    context.update(kwargs)
    return context


def test_kelly_blocks_low_edge():
    result = check_kelly(
        make_intent(estimated_edge=0.01),
        make_market(),
        make_features(),
        RiskConfig(),
    )

    assert result.decision == RiskDecision.BLOCK
    assert result.gate == "kelly"


def test_kelly_gate_bypassed_for_closing_orders():
    result = check_kelly(
        make_intent(estimated_edge=0.01, is_closing_order=True),
        make_market(),
        make_features(),
        RiskConfig(),
    )

    assert result.decision == RiskDecision.ALLOW
    assert result.reason == "closing_order_kelly_bypassed"


def test_kelly_gate_still_blocks_non_closing_low_edge():
    result = check_kelly(
        make_intent(estimated_edge=0.01),
        make_market(),
        make_features(),
        RiskConfig(),
    )

    assert result.decision == RiskDecision.BLOCK
    assert result.reason == "edge_below_minimum"


def test_kelly_allows_sufficient_edge():
    result = check_kelly(
        make_intent(estimated_edge=0.08),
        make_market(),
        make_features(),
        RiskConfig(),
    )

    assert result.decision == RiskDecision.ALLOW
    assert result.metadata["kelly_qty"] > 0


def test_liquidity_blocks_wide_spread():
    result = check_liquidity(
        make_intent(),
        make_market(),
        make_features(spread_pct=20.0),
        RiskConfig(),
    )

    assert result.decision == RiskDecision.BLOCK
    assert result.reason == "spread_too_wide"


def test_liquidity_blocks_thin_book():
    result = check_liquidity(
        make_intent(qty=100),
        make_market(yes_bid_size=50),
        make_features(),
        RiskConfig(),
    )

    assert result.decision == RiskDecision.BLOCK
    assert result.reason == "order_exceeds_book_limit"


def test_liquidity_allows_normal():
    result = check_liquidity(make_intent(), make_market(), make_features(), RiskConfig())

    assert result.decision == RiskDecision.ALLOW


def test_correlation_blocks_too_many_positions():
    open_positions = [
        {"ticker": "A", "category": "crypto"},
        {"ticker": "B", "category": "crypto"},
        {"ticker": "C", "category": "crypto"},
    ]
    result = check_correlation(
        make_intent(),
        make_market(),
        make_features(),
        RiskConfig(),
        open_positions,
        "crypto",
    )

    assert result.decision == RiskDecision.BLOCK


def test_correlation_allows_different_category():
    open_positions = [
        {"ticker": "A", "category": "weather"},
        {"ticker": "B", "category": "sports"},
        {"ticker": "C", "category": "politics"},
    ]
    result = check_correlation(
        make_intent(),
        make_market(),
        make_features(),
        RiskConfig(),
        open_positions,
        "crypto",
    )

    assert result.decision == RiskDecision.ALLOW
    assert result.metadata["category_count"] == 0


def test_concentration_blocks_oversized():
    result = check_concentration(
        make_intent(qty=300, price=Decimal("0.50")),
        make_market(),
        make_features(),
        RiskConfig(),
        portfolio_value_usd=1000.0,
        current_exposure_usd=0.0,
    )

    assert result.decision == RiskDecision.BLOCK
    assert result.reason == "position_limit_exceeded"


def test_concentration_blocks_total_exposure():
    result = check_concentration(
        make_intent(qty=20, price=Decimal("0.50")),
        make_market(),
        make_features(),
        RiskConfig(),
        portfolio_value_usd=1000.0,
        current_exposure_usd=495.0,
    )

    assert result.decision == RiskDecision.BLOCK
    assert result.reason == "total_exposure_limit_exceeded"


def test_drawdown_blocks_kill_switch():
    result = check_drawdown(
        make_intent(),
        make_market(),
        make_features(),
        RiskConfig(),
        daily_realized_pnl_usd=0.0,
        kill_switch_active=True,
        global_kill_switch=False,
    )

    assert result.decision == RiskDecision.BLOCK
    assert result.reason == "kill_switch_active"


def test_drawdown_blocks_daily_loss():
    result = check_drawdown(
        make_intent(),
        make_market(),
        make_features(),
        RiskConfig(),
        daily_realized_pnl_usd=-110.0,
        kill_switch_active=False,
        global_kill_switch=False,
    )

    assert result.decision == RiskDecision.BLOCK
    assert result.reason == "daily_loss_limit_breached"


def test_drawdown_blocks_expiry():
    result = check_drawdown(
        make_intent(),
        make_market(),
        make_features(time_to_close_hours=0.4),
        RiskConfig(),
        daily_realized_pnl_usd=0.0,
        kill_switch_active=False,
        global_kill_switch=False,
    )

    assert result.decision == RiskDecision.BLOCK
    assert result.reason == "expiry_buffer"


def test_full_engine_all_gates_pass():
    result = RiskEngine().evaluate(**make_context())

    assert result.decision == RiskDecision.ALLOW
    assert result.approved_qty == result.intent.qty
    assert result.blocked_by is None
    assert [gate.gate for gate in result.gate_results] == [
        "kelly",
        "liquidity",
        "correlation",
        "concentration",
        "drawdown",
    ]


def test_full_engine_blocks_at_kelly():
    result = RiskEngine().evaluate(**make_context(intent=make_intent(estimated_edge=0.01)))

    assert result.decision == RiskDecision.BLOCK
    assert result.blocked_by == "kelly"
    assert len(result.gate_results) == 1


def test_market_making_edge_needs_strategy_specific_kelly_floor():
    context = make_context(
        intent=make_intent(estimated_edge=0.005),
        current_exposure_usd=0.0,
    )

    default_result = RiskEngine().evaluate(**context)
    override_result = RiskEngine(
        config=RiskConfig(min_edge_to_trade=0.003)
    ).evaluate(**context)

    assert default_result.decision == RiskDecision.BLOCK
    assert default_result.blocked_by == "kelly"
    assert override_result.decision == RiskDecision.ALLOW
    assert override_result.blocked_by is None


def test_full_engine_blocks_at_drawdown():
    result = RiskEngine().evaluate(**make_context(kill_switch_active=True))

    assert result.decision == RiskDecision.BLOCK
    assert result.blocked_by == "drawdown"
    assert len(result.gate_results) == 5
