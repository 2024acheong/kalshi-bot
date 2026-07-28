from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from core.risk.engine import OrderIntent
from core.schemas.market import FeatureVector, MarketState


@dataclass
class EventDriftPosition:
    """Tracks an open momentum-following position."""

    ticker: str
    side: str
    entry_price: Decimal
    entry_mid_price: Decimal
    entry_momentum: float
    qty: int
    opened_at: datetime


class EventDriftStrategy:
    def __init__(
        self,
        momentum_threshold: float = 0.06,
        min_confirming_imbalance: float = 0.15,
        min_volume_zscore: float = 1.5,
        volume_surge_zscore: float = 3.0,
        qty: int = 10,
        exhaustion_threshold_pct: float = 0.5,
        min_hours_to_close: float = 0.5,
    ):
        # momentum_threshold is a relative move (e.g. 0.06 = 6% of reference price).
        self.momentum_threshold = momentum_threshold
        self.min_confirming_imbalance = min_confirming_imbalance
        self.min_volume_zscore = min_volume_zscore
        self.volume_surge_zscore = volume_surge_zscore
        self.qty = qty
        self.exhaustion_threshold_pct = exhaustion_threshold_pct
        self.min_hours_to_close = min_hours_to_close

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
        """
        Follow confirmed momentum when book pressure and volume support the move.
        """
        momentum = features.price_momentum_1h
        if momentum is None:
            return None

        reference_price = features.mid_price
        if reference_price is None or reference_price <= 0:
            return None

        relative_momentum = abs(momentum) / reference_price
        if relative_momentum < self.momentum_threshold:
            return None

        imbalance = features.bid_ask_imbalance
        if imbalance is None:
            return None

        volume_surge = (
            features.volume_zscore is not None
            and features.volume_zscore >= self.volume_surge_zscore
        )
        if not volume_surge:
            if momentum > 0 and imbalance < self.min_confirming_imbalance:
                return None

            if momentum < 0 and imbalance > -self.min_confirming_imbalance:
                return None

        # Unlike mean reversion, event drift wants elevated volume as evidence
        # that the move is informed activity rather than noise.
        if features.volume_zscore is None or features.volume_zscore < self.min_volume_zscore:
            return None

        if (
            features.time_to_close_hours is None
            or features.time_to_close_hours < self.min_hours_to_close
        ):
            return None

        if market.yes_bid is None or market.yes_ask is None:
            return None

        if market.yes_bid <= 0 or market.yes_ask >= 1:
            return None

        estimated_edge = abs(momentum)
        if momentum > 0:
            return OrderIntent(
                ticker=market.ticker,
                side="yes",
                price=market.yes_ask,
                qty=self.qty,
                estimated_edge=estimated_edge,
                model_prob=float(market.yes_ask) + estimated_edge,
                run_id=run_id,
            )

        return OrderIntent(
            ticker=market.ticker,
            side="no",
            price=market.yes_bid,
            qty=self.qty,
            estimated_edge=estimated_edge,
            model_prob=float(market.yes_bid) - estimated_edge,
            run_id=run_id,
        )

    def evaluate_exit(
        self,
        position: EventDriftPosition,
        market: MarketState,
        features: FeatureVector,
        as_of: datetime,
    ) -> OrderIntent | None:
        """
        Exit when momentum exhausts or the market is approaching close.
        """
        if market.yes_bid is None or market.yes_ask is None:
            return None

        should_exit = False
        if (
            features.time_to_close_hours is not None
            and features.time_to_close_hours < self.min_hours_to_close
        ):
            should_exit = True

        momentum = features.price_momentum_1h
        if momentum is None:
            should_exit = True
        elif position.side == "yes":
            should_exit = should_exit or (
                momentum
                < position.entry_momentum * self.exhaustion_threshold_pct
            )
        elif position.side == "no":
            should_exit = should_exit or (
                momentum
                > position.entry_momentum * self.exhaustion_threshold_pct
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
