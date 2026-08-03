from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import math
from enum import Enum

from core.risk.engine import OrderIntent
from core.schemas.market import FeatureVector, MarketState

LOGGER = logging.getLogger(__name__)


class ProbabilityEstimator(ABC):
    """
    Interface for pure, pluggable YES-probability forecasting models.
    """

    @abstractmethod
    def estimate(self, market: MarketState, features: FeatureVector) -> float | None:
        """
        Return a YES probability estimate in [0, 1], or None if unavailable.

        Implementations must be pure functions of their inputs: no I/O, no side
        effects, and no internal state mutation, so backtests and live trading
        behave identically for identical inputs.
        """


class NaiveMidpointDriftEstimator(ProbabilityEstimator):
    """
    Placeholder model for testing strategy mechanics only.

    This is not a real forecasting model and should be replaced before using
    real capital or drawing serious backtest conclusions. It estimates YES
    probability as current midpoint plus a small momentum-direction nudge.
    """

    def __init__(self, momentum_weight: float = 0.1):
        self.momentum_weight = momentum_weight

    def estimate(self, market: MarketState, features: FeatureVector) -> float | None:
        mid = features.mid_price
        if mid is None:
            return None

        momentum = features.price_momentum_1h or 0.0
        estimate = mid + (momentum * self.momentum_weight)
        return min(max(estimate, 0.01), 0.99)

class ProperScoringRule(Enum):
    BRIER = "brier"          # Quadratic / Linear margin
    LOGARITHMIC = "log"      # Log-odds / LMSR (Recommended for calibrated models)
    SPHERICAL = "spherical"  # Spherical scoring rule

# def compute_brier_linear_size(
#     model_prob: float,
#     market_price: float,
#     max_qty: int,
#     scale_factor: float = 1.0,
# ) -> int:
#     """
#     Position size proportional to the forecast-market margin.

#     For the Brier/quadratic scoring rule, proper bet size scales linearly with
#     the margin rather than Kelly-sizing as if the forecast were ground truth.
#     """
#     if max_qty <= 0:
#         return 0
#     if scale_factor <= 0:
#         raise ValueError("scale_factor must be positive")

#     margin = abs(model_prob - market_price)
#     qty = round(min(margin * max_qty / scale_factor, max_qty))
#     return max(qty, 0)
def compute_proper_betting_size(
    model_prob: float,
    market_price: float,
    max_qty: int,
    scoring_rule: ProperScoringRule = ProperScoringRule.LOGARITHMIC,
    scale_factor: float = 1.0,
) -> int:
    """
    Computes position size s(p, q) = grad(G)(p) - grad(G)(q) based on 
    Gu et al. (2026) "When do prophets profit in prediction markets?"
    """
    if max_qty <= 0:
        return 0
    if scale_factor <= 0:
        raise ValueError("scale_factor must be positive")

    # Clamp probabilities to avoid numerical overflow/undef in logit
    p = max(min(model_prob, 0.999), 0.001)
    q = max(min(market_price, 0.999), 0.001)

    if scoring_rule == ProperScoringRule.BRIER:
        # G(p) = p^2 -> grad(G)(p) = 2p
        raw_size = (p - q)

    elif scoring_rule == ProperScoringRule.LOGARITHMIC:
        # G(p) = p*ln(p) + (1-p)*ln(1-p) -> grad(G)(p) = logit(p)
        logit_p = math.log(p / (1.0 - p))
        logit_q = math.log(q / (1.0 - q))
        raw_size = logit_p - logit_q

    elif scoring_rule == ProperScoringRule.SPHERICAL:
        # G(p) = sqrt(p^2 + (1-p)^2)
        norm_p = math.sqrt(p**2 + (1.0 - p)**2)
        norm_q = math.sqrt(q**2 + (1.0 - q)**2)
        raw_size = (p / norm_p) - (q / norm_q)

    # Scale and clamp to max_qty
    abs_qty = abs(raw_size) * (max_qty / scale_factor)
    qty = round(min(abs_qty, float(max_qty)))

    return max(qty, 0)


def is_within_no_bet_zone(
    model_prob: float,
    yes_bid: Decimal,
    yes_ask: Decimal,
) -> bool:
    """
    Return True when the model probability sits inside the YES bid/ask spread.

    Trade YES when model_prob clears the ask. Trade NO when model_prob is below
    the bid. Inside [yes_bid, yes_ask], neither side clears the spread.
    """
    return float(yes_bid) <= model_prob <= float(yes_ask)


@dataclass
class CalibrationMispricingPosition:
    ticker: str
    side: str
    entry_price: Decimal
    entry_model_prob: float
    qty: int
    opened_at: datetime


class CalibrationMispricingStrategy:
    def __init__(
        self,
        estimator: ProbabilityEstimator,
        max_qty: int = 20,
        scale_factor: float = 1.0,
        min_hours_to_close: float = 0.5,
        exit_edge_threshold: float = 0.01,
        hold_log_interval_seconds: float = 60.0,
    ):
        self.estimator = estimator
        self.max_qty = max_qty
        self.scale_factor = scale_factor
        self.min_hours_to_close = min_hours_to_close
        self.exit_edge_threshold = exit_edge_threshold
        self.hold_log_interval_seconds = hold_log_interval_seconds
        self._last_hold_log_at: dict[tuple[str, str], datetime] = {}

    def _log_hold(self, market: MarketState, reason: str, **metadata: Any) -> None:
        now = datetime.now(timezone.utc)
        key = (market.ticker, reason)
        last_logged_at = self._last_hold_log_at.get(key)
        if (
            last_logged_at is not None
            and (now - last_logged_at).total_seconds() < self.hold_log_interval_seconds
        ):
            return

        self._last_hold_log_at[key] = now
        LOGGER.info(
            "calibration_hold ticker=%s estimator=%s reason=%s metadata=%s",
            market.ticker,
            type(self.estimator).__name__,
            reason,
            metadata,
        )

    def evaluate(
        self,
        market: MarketState,
        features: FeatureVector,
        run_id: str,
    ) -> OrderIntent | None:
        return self.evaluate_entry(market, features, run_id)

    def evaluate_entry(
        self,
        market: MarketState,
        features: FeatureVector,
        run_id: str,
    ) -> OrderIntent | None:
        model_prob = self.estimator.estimate(market, features)
        if model_prob is None:
            self._log_hold(market, "model_prob_none")
            return None

        if market.yes_bid is None or market.yes_ask is None or market.no_bid is None or market.no_ask is None:
            self._log_hold(market, "missing_book", model_prob=model_prob)
            return None

        if (
            features.time_to_close_hours is None
            or features.time_to_close_hours < self.min_hours_to_close
        ):
            self._log_hold(
                market,
                "insufficient_time_to_close",
                model_prob=model_prob,
                time_to_close_hours=features.time_to_close_hours,
                min_hours_to_close=self.min_hours_to_close,
            )
            return None

        if is_within_no_bet_zone(model_prob, market.yes_bid, market.yes_ask):
            self._log_hold(
                market,
                "inside_spread",
                model_prob=model_prob,
                yes_bid=str(market.yes_bid),
                yes_ask=str(market.yes_ask),
            )
            return None
        
        ask_price = float(market.yes_ask)
        bid_price = float(market.yes_bid)

        # 1. Edge exceeds ask -> Buy YES
        if model_prob > ask_price:
            qty = compute_proper_betting_size(
                model_prob=model_prob,
                market_price=ask_price,
                max_qty=self.max_qty,
                scoring_rule=ProperScoringRule.LOGARITHMIC,  # Uses log-odds scaling
                scale_factor=self.scale_factor,
            )
            if qty > 0:
                return OrderIntent(
                    ticker=market.ticker,
                    side="yes",
                    price=market.yes_ask,
                    qty=qty,
                    estimated_edge=model_prob - ask_price,
                    model_prob=model_prob,
                    run_id=run_id,
                )

        # 2. Edge falls below bid -> Buy NO
        elif model_prob < bid_price:
            no_ask_price = float(market.no_ask) if market.no_ask is not None else (1.0 - bid_price)

            qty = compute_proper_betting_size(
                model_prob=1.0 - model_prob,
                market_price=no_ask_price,
                max_qty=self.max_qty,
                scoring_rule=ProperScoringRule.LOGARITHMIC,
                scale_factor=self.scale_factor,
            )
            if qty > 0:
                return OrderIntent(
                    ticker=market.ticker,
                    side="no",
                    price=market.no_ask,
                    qty=qty,
                    estimated_edge=(1.0 - model_prob) - no_ask_price,
                    model_prob=model_prob,
                    run_id=run_id,
                )

        return None

    def evaluate_exit(
        self,
        position: CalibrationMispricingPosition,
        market: MarketState,
        features: FeatureVector,
        as_of: datetime,
    ) -> OrderIntent | None:
        if market.yes_bid is None or market.yes_ask is None:
            return None

        current_prob = self.estimator.estimate(market, features)
        should_exit = current_prob is None

        if (
            features.time_to_close_hours is not None
            and features.time_to_close_hours < self.min_hours_to_close
        ):
            should_exit = True

        if current_prob is not None and position.side == "yes":
            should_exit = should_exit or (
                current_prob - float(market.yes_ask) < self.exit_edge_threshold
            )
        elif current_prob is not None and position.side == "no":
            should_exit = should_exit or (
                (1.0 - current_prob) - float(market.no_ask) < self.exit_edge_threshold
            )

        if not should_exit:
            return None

        closing_side = "yes" if position.side == "no" else "no"
        price = market.yes_ask if closing_side == "yes" else market.no_ask

        if price is None:
            return None

        return OrderIntent(
            ticker=position.ticker,
            side=closing_side,
            price=price,
            qty=position.qty,
            estimated_edge=0.01,
            model_prob=float(price),
            run_id="exit",
            is_closing_order=True,
        )
