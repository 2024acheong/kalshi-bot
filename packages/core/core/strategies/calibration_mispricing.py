from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

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


def compute_brier_linear_size(
    model_prob: float,
    market_price: float,
    max_qty: int,
    scale_factor: float = 1.0,
) -> int:
    """
    Position size proportional to the forecast-market margin.

    For the Brier/quadratic scoring rule, proper bet size scales linearly with
    the margin rather than Kelly-sizing as if the forecast were ground truth.
    """
    if max_qty <= 0:
        return 0
    if scale_factor <= 0:
        raise ValueError("scale_factor must be positive")

    margin = abs(model_prob - market_price)
    qty = round(min(margin * max_qty / scale_factor, max_qty))
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

        if market.yes_bid is None or market.yes_ask is None:
            self._log_hold(
                market,
                "missing_book",
                model_prob=model_prob,
                yes_bid=str(market.yes_bid) if market.yes_bid is not None else None,
                yes_ask=str(market.yes_ask) if market.yes_ask is not None else None,
            )
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

        if model_prob > float(market.yes_ask):
            price = market.yes_ask
            edge = model_prob - float(price)
            qty = compute_brier_linear_size(
                model_prob,
                float(price),
                self.max_qty,
                self.scale_factor,
            )
            if qty <= 0:
                self._log_hold(
                    market,
                    "qty_zero",
                    side="yes",
                    model_prob=model_prob,
                    price=str(price),
                    edge=edge,
                    max_qty=self.max_qty,
                    scale_factor=self.scale_factor,
                )
                return None
            return OrderIntent(
                ticker=market.ticker,
                side="yes",
                price=price,
                qty=qty,
                estimated_edge=edge,
                model_prob=model_prob,
                run_id=run_id,
            )

        if model_prob < float(market.yes_bid):
            price = market.yes_bid
            edge = float(price) - model_prob
            qty = compute_brier_linear_size(
                model_prob,
                float(price),
                self.max_qty,
                self.scale_factor,
            )
            if qty <= 0:
                self._log_hold(
                    market,
                    "qty_zero",
                    side="no",
                    model_prob=model_prob,
                    price=str(price),
                    edge=edge,
                    max_qty=self.max_qty,
                    scale_factor=self.scale_factor,
                )
                return None
            return OrderIntent(
                ticker=market.ticker,
                side="no",
                price=price,
                qty=qty,
                estimated_edge=edge,
                model_prob=model_prob,
                run_id=run_id,
            )

        self._log_hold(
            market,
            "no_direction",
            model_prob=model_prob,
            yes_bid=str(market.yes_bid),
            yes_ask=str(market.yes_ask),
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
                float(market.yes_bid) - current_prob < self.exit_edge_threshold
            )

        if not should_exit:
            return None

        closing_side = "yes" if position.side == "no" else "no"
        price = market.yes_ask if closing_side == "yes" else market.yes_bid
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
