from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from core.risk.engine import OrderIntent
from core.schemas.market import FeatureVector, MarketState


@dataclass
class MeanReversionPosition:
    """
    Tracks an open mean-reversion position so the strategy can decide when to exit.

    The strategy itself is stateless per call. Callers pass position state in
    explicitly rather than relying on hidden instance state across tickers.
    """

    ticker: str
    side: str
    entry_price: Decimal
    entry_mid_price: Decimal
    entry_spread_ticks: Decimal
    qty: int
    opened_at: datetime


class MeanReversionStrategy:
    def __init__(
        self,
        momentum_threshold: float = 0.03,
        max_confirming_imbalance: float = 0.10,
        max_volume_zscore: float = 2.0,
        qty: int = 10,
        stop_loss_spread_multiple: float = 1.5,
        take_profit_spread_multiple: float = 0.5,
        min_hours_to_close: float = 0.5,
    ):
        self.momentum_threshold = momentum_threshold
        self.max_confirming_imbalance = max_confirming_imbalance
        self.max_volume_zscore = max_volume_zscore
        self.qty = qty
        self.stop_loss_spread_multiple = stop_loss_spread_multiple
        self.take_profit_spread_multiple = take_profit_spread_multiple
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
        Return an aggressive entry order when momentum looks overextended.
        """
        momentum = features.price_momentum_1h
        if momentum is None or abs(momentum) < self.momentum_threshold:
            return None

        imbalance = features.bid_ask_imbalance
        if imbalance is None:
            return None

        if momentum > 0 and imbalance > self.max_confirming_imbalance:
            return None

        if momentum < 0 and imbalance < -self.max_confirming_imbalance:
            return None

        if features.volume_zscore is None or abs(features.volume_zscore) >= self.max_volume_zscore:
            return None

        # Skip markets too close to resolution; late moves are convergence, not noise.
        if (
            features.time_to_close_hours is None
            or features.time_to_close_hours < self.min_hours_to_close
        ):
            return None

        if market.yes_bid is None or market.yes_ask is None or market.no_bid is None or market.no_ask is None:
            return None

        if market.yes_bid <= 0 or market.yes_ask >= 1 or market.no_bid <= 0 or market.no_ask >= 1: #maybe change
            return None

        signal_strength = abs(momentum)
        estimated_edge = signal_strength
        if momentum > 0:
            return OrderIntent(
                ticker=market.ticker,
                side="no",
                price=market.no_ask,
                qty=self.qty,
                estimated_edge=estimated_edge,
                model_prob=float(market.no_ask) - estimated_edge, # FIX
                run_id=run_id,
            )

        return OrderIntent(
            ticker=market.ticker,
            side="yes",
            price=market.yes_ask,
            qty=self.qty,
            estimated_edge=estimated_edge,
            model_prob=float(market.yes_ask) + estimated_edge,
            run_id=run_id,
        )

    def evaluate_exit(
        self,
        position: MeanReversionPosition,
        market: MarketState,
        as_of: datetime,
    ) -> OrderIntent | None:
        """
        Return an aggressive closing order if reversion or stop-loss is triggered.
        """
        if (
            market.yes_bid is None
            or market.yes_ask is None
            or market.no_bid is None
            or market.no_ask is None
        ):
            return None

        held_time_hours = (
            as_of - position.opened_at
        ).total_seconds() / 3600

        stop_band = position.entry_spread_ticks * Decimal(str(self.stop_loss_spread_multiple))
        profit_band = (
            position.entry_spread_ticks
            * Decimal(str(self.take_profit_spread_multiple))
        )

        if position.side == "no":
            closing_side = "yes"
            price = market.yes_ask

        elif position.side == "yes":
            closing_side = "no"
            price = market.no_ask
        else:
            return None

        if price is None:
            return None

        if held_time_hours > 4:
            should_exit = True
        else:
            complementary_entry = Decimal("1") - position.entry_price
            profit_target = complementary_entry - profit_band
            stop_loss = complementary_entry + stop_band
            should_exit = price <= profit_target or price >= stop_loss

        if not should_exit:
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
