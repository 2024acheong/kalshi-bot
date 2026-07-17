from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from typing import Any

from core.schemas.market import FeatureVector, MarketState, RiskDecision


@dataclass
class OrderIntent:
    ticker: str
    side: str
    price: Decimal
    qty: int
    estimated_edge: float
    model_prob: float
    run_id: str
    signal_id: str | None = None


@dataclass
class RiskGateResult:
    gate: str
    decision: RiskDecision
    reason: str
    metadata: dict[str, Any]


@dataclass
class RiskEngineResult:
    intent: OrderIntent
    decision: RiskDecision
    approved_qty: int
    gate_results: list[RiskGateResult]
    blocked_by: str | None


@dataclass
class RiskConfig:
    # Kelly
    kelly_fraction: float = 0.5
    min_edge_to_trade: float = 0.03

    # Liquidity
    max_spread_pct: float = 15.0
    min_liquidity_score: float = 5.0
    max_order_pct_of_book: float = 0.25

    # Correlation
    max_positions_per_category: int = 3

    # Concentration
    max_position_pct: float = 0.10
    max_total_exposure_usd: float = 500.0

    # Drawdown
    daily_loss_limit_usd: float = 100.0
    expiry_buffer_minutes: float = 30.0


def check_kelly(
    intent: OrderIntent,
    market: MarketState,
    features: FeatureVector,
    config: RiskConfig,
) -> RiskGateResult:
    price = float(intent.price)
    denominator = price * (1 - price)
    kelly_qty = (
        int((intent.estimated_edge * config.kelly_fraction) / denominator * 100)
        if denominator > 0
        else 0
    )
    metadata = {
        "kelly_qty": kelly_qty,
        "estimated_edge": intent.estimated_edge,
        "min_edge_to_trade": config.min_edge_to_trade,
    }

    if intent.estimated_edge < config.min_edge_to_trade:
        return RiskGateResult(
            gate="kelly",
            decision=RiskDecision.BLOCK,
            reason="edge_below_minimum",
            metadata=metadata,
        )

    if kelly_qty <= 0:
        return RiskGateResult(
            gate="kelly",
            decision=RiskDecision.BLOCK,
            reason="kelly_size_non_positive",
            metadata=metadata,
        )

    return RiskGateResult(
        gate="kelly",
        decision=RiskDecision.ALLOW,
        reason="kelly_size_positive",
        metadata=metadata,
    )


def check_liquidity(
    intent: OrderIntent,
    market: MarketState,
    features: FeatureVector,
    config: RiskConfig,
) -> RiskGateResult:
    book_size = market.yes_bid_size if intent.side == "yes" else market.yes_ask_size
    max_order_qty = (
        int(book_size * config.max_order_pct_of_book) if book_size is not None else None
    )
    metadata = {
        "spread_pct": features.spread_pct,
        "liquidity_score": features.liquidity_score,
        "book_size": book_size,
        "max_order_qty": max_order_qty,
    }

    if features.spread_pct is not None and features.spread_pct > config.max_spread_pct:
        return RiskGateResult(
            gate="liquidity",
            decision=RiskDecision.BLOCK,
            reason="spread_too_wide",
            metadata=metadata,
        )

    if (
        features.liquidity_score is not None
        and features.liquidity_score < config.min_liquidity_score
    ):
        return RiskGateResult(
            gate="liquidity",
            decision=RiskDecision.BLOCK,
            reason="liquidity_score_too_low",
            metadata=metadata,
        )

    if book_size is not None and intent.qty > book_size * config.max_order_pct_of_book:
        return RiskGateResult(
            gate="liquidity",
            decision=RiskDecision.BLOCK,
            reason="order_exceeds_book_limit",
            metadata=metadata,
        )

    return RiskGateResult(
        gate="liquidity",
        decision=RiskDecision.ALLOW,
        reason="liquidity_ok",
        metadata=metadata,
    )


def check_correlation(
    intent: OrderIntent,
    market: MarketState,
    features: FeatureVector,
    config: RiskConfig,
    open_positions: list[dict],
    market_category: str | None,
) -> RiskGateResult:
    category_count = (
        sum(1 for position in open_positions if position.get("category") == market_category)
        if market_category is not None
        else 0
    )
    metadata = {
        "category": market_category,
        "category_count": category_count,
        "max_positions_per_category": config.max_positions_per_category,
    }

    if category_count >= config.max_positions_per_category:
        return RiskGateResult(
            gate="correlation",
            decision=RiskDecision.BLOCK,
            reason="category_position_limit_reached",
            metadata=metadata,
        )

    return RiskGateResult(
        gate="correlation",
        decision=RiskDecision.ALLOW,
        reason="correlation_ok",
        metadata=metadata,
    )


def check_concentration(
    intent: OrderIntent,
    market: MarketState,
    features: FeatureVector,
    config: RiskConfig,
    portfolio_value_usd: float,
    current_exposure_usd: float,
) -> RiskGateResult:
    order_exposure_usd = intent.qty * float(intent.price)
    max_position_usd = portfolio_value_usd * config.max_position_pct
    total_exposure_usd = current_exposure_usd + order_exposure_usd
    metadata = {
        "order_exposure_usd": order_exposure_usd,
        "max_position_usd": max_position_usd,
        "current_exposure_usd": current_exposure_usd,
        "total_exposure_usd": total_exposure_usd,
        "max_total_exposure_usd": config.max_total_exposure_usd,
    }

    if order_exposure_usd > max_position_usd:
        return RiskGateResult(
            gate="concentration",
            decision=RiskDecision.BLOCK,
            reason="position_limit_exceeded",
            metadata=metadata,
        )

    if total_exposure_usd > config.max_total_exposure_usd:
        return RiskGateResult(
            gate="concentration",
            decision=RiskDecision.BLOCK,
            reason="total_exposure_limit_exceeded",
            metadata=metadata,
        )

    return RiskGateResult(
        gate="concentration",
        decision=RiskDecision.ALLOW,
        reason="concentration_ok",
        metadata=metadata,
    )


def check_drawdown(
    intent: OrderIntent,
    market: MarketState,
    features: FeatureVector,
    config: RiskConfig,
    daily_realized_pnl_usd: float,
    kill_switch_active: bool,
    global_kill_switch: bool,
) -> RiskGateResult:
    metadata = {
        "daily_realized_pnl_usd": daily_realized_pnl_usd,
        "daily_loss_limit_usd": config.daily_loss_limit_usd,
        "time_to_close_hours": features.time_to_close_hours,
        "expiry_buffer_minutes": config.expiry_buffer_minutes,
        "kill_switch_active": kill_switch_active,
        "global_kill_switch": global_kill_switch,
    }

    if kill_switch_active or global_kill_switch:
        return RiskGateResult(
            gate="drawdown",
            decision=RiskDecision.BLOCK,
            reason="kill_switch_active",
            metadata=metadata,
        )

    if daily_realized_pnl_usd <= -config.daily_loss_limit_usd:
        return RiskGateResult(
            gate="drawdown",
            decision=RiskDecision.BLOCK,
            reason="daily_loss_limit_breached",
            metadata=metadata,
        )

    if (
        features.time_to_close_hours is not None
        and features.time_to_close_hours * 60 < config.expiry_buffer_minutes
    ):
        return RiskGateResult(
            gate="drawdown",
            decision=RiskDecision.BLOCK,
            reason="expiry_buffer",
            metadata=metadata,
        )

    return RiskGateResult(
        gate="drawdown",
        decision=RiskDecision.ALLOW,
        reason="drawdown_ok",
        metadata=metadata,
    )


class RiskEngine:
    def __init__(self, config: RiskConfig | None = None):
        self.config = config or RiskConfig()

    def evaluate(
        self,
        intent: OrderIntent,
        market: MarketState,
        features: FeatureVector,
        open_positions: list[dict],
        market_category: str | None,
        portfolio_value_usd: float,
        current_exposure_usd: float,
        daily_realized_pnl_usd: float,
        kill_switch_active: bool = False,
        global_kill_switch: bool = False,
    ) -> RiskEngineResult:
        gate_results: list[RiskGateResult] = []
        final_decision = RiskDecision.ALLOW
        approved_qty = intent.qty

        gate_checks = [
            partial(check_kelly, intent, market, features, self.config),
            partial(check_liquidity, intent, market, features, self.config),
            partial(
                check_correlation,
                intent,
                market,
                features,
                self.config,
                open_positions,
                market_category,
            ),
            partial(
                check_concentration,
                intent,
                market,
                features,
                self.config,
                portfolio_value_usd,
                current_exposure_usd,
            ),
            partial(
                check_drawdown,
                intent,
                market,
                features,
                self.config,
                daily_realized_pnl_usd,
                kill_switch_active,
                global_kill_switch,
            ),
        ]

        for check_gate in gate_checks:
            result = check_gate()
            gate_results.append(result)

            if result.decision == RiskDecision.BLOCK:
                return RiskEngineResult(
                    intent=intent,
                    decision=RiskDecision.BLOCK,
                    approved_qty=0,
                    gate_results=gate_results,
                    blocked_by=result.gate,
                )

            if result.decision == RiskDecision.REDUCE_ONLY:
                final_decision = RiskDecision.REDUCE_ONLY
                approved_qty = int(result.metadata.get("approved_qty", approved_qty))

        return RiskEngineResult(
            intent=intent,
            decision=final_decision,
            approved_qty=approved_qty,
            gate_results=gate_results,
            blocked_by=None,
        )
