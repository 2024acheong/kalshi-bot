from __future__ import annotations

from core.risk.engine import OrderIntent
from core.schemas.market import FeatureVector, MarketState


class DummyStrategy:
    """
    Minimal strategy to prove the end-to-end pipeline works.
    Buys YES if spread_pct < threshold and time_to_close_hours > minimum.
    Not a real strategy - placeholder until feat/strategies.
    """

    def __init__(self, max_spread_pct: float = 10.0, min_hours_to_close: float = 1.0):
        self.max_spread_pct = max_spread_pct
        self.min_hours_to_close = min_hours_to_close

    def evaluate(
        self,
        market: MarketState,
        features: FeatureVector,
        run_id: str,
    ) -> OrderIntent | None:
        """
        Returns an OrderIntent if conditions are met, else None.

        Uses market.yes_ask as intent price, fixed qty of 10, estimated_edge as a
        placeholder fixed at 0.05, and model_prob as ask price plus 0.05.
        """
        if features.spread_pct is None or features.spread_pct > self.max_spread_pct:
            return None

        if (
            features.time_to_close_hours is None
            or features.time_to_close_hours < self.min_hours_to_close
        ):
            return None

        if market.yes_ask is None:
            return None

        return OrderIntent(
            ticker=market.ticker,
            side="yes",
            price=market.yes_ask,
            qty=10,
            estimated_edge=0.05,
            model_prob=float(market.yes_ask) + 0.05,
            run_id=run_id,
        )
