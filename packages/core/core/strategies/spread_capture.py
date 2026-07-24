from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from core.execution.fees import compute_kalshi_fee
from core.risk.engine import OrderIntent
from core.schemas.market import FeatureVector, MarketState
# KNOWN LIMITATION (as of feat/strategy-spread-capture):
# This strategy posts passive limit orders at the current bid/ask, which by
# construction never fill within the same tick against PaperAdapter's
# fill-if-crossed logic. Real fills require the paper broker / backtester to
# track resting orders across multiple ticks and check on each subsequent
# snapshot whether the market has moved to cross the resting price, or
# whether another participant traded through it. This is NOT implemented yet.
# See feat/resting-orders (or equivalent) for the follow-up work.
# Until that lands, backtests of this strategy will show orders "allowed"
# by the risk engine but zero fills — this is expected, not a bug.

@dataclass
class SpreadCaptureIntent:
    """
    A linked pair of order intents representing both sides of a spread capture attempt.

    yes_intent and no_intent must be tracked together. If one fills and the other
    does not within max_resting_seconds, the unfilled leg should be cancelled by
    the component managing open orders. This strategy only decides intent; it does
    not manage order lifecycle or cancellation.
    """

    yes_intent: OrderIntent
    no_intent: OrderIntent
    pair_id: str
    max_resting_seconds: int


def detect_implied_probability_arbitrage(
    market: MarketState,
    qty: int,
) -> tuple[bool, Decimal]:
    """
    Return whether buying YES ask and NO ask locks resolution profit after fees.

    One of the two contracts pays $1.00 at resolution, so the locked profit per
    contract is payout minus both ask prices minus the per-contract combined fees.
    """
    if market.no_bid is None or market.no_ask is None or market.yes_ask is None:
        return False, Decimal("0")
    if qty <= 0:
        return False, Decimal("0")

    total_cost = market.yes_ask + market.no_ask
    combined_fee = (
        compute_kalshi_fee(market.yes_ask, qty)
        + compute_kalshi_fee(market.no_ask, qty)
    ) / qty
    locked_profit = Decimal("1.00") - total_cost - combined_fee
    if locked_profit <= Decimal("0"):
        return False, Decimal("0")
    return True, locked_profit


class SpreadCaptureStrategy:
    def __init__(
        self,
        min_spread_pct: float = 3.0,
        max_imbalance: float = 0.15,
        qty_per_leg: int = 10,
        max_resting_seconds: int = 30,
        min_hours_to_close: float = 0.5,
    ):
        self.min_spread_pct = min_spread_pct
        self.max_imbalance = max_imbalance
        self.qty_per_leg = qty_per_leg
        self.max_resting_seconds = max_resting_seconds
        self.min_hours_to_close = min_hours_to_close

    def evaluate_arbitrage_entry(
        self,
        market: MarketState,
        features: FeatureVector,
        run_id: str,
        qty: int = 10,
    ) -> SpreadCaptureIntent | None:
        """
        Return an immediate YES/NO pair for true implied-probability arbitrage.

        This is separate from passive spread capture: max_resting_seconds=0 tells
        the runtime to bypass the resting book and submit both marketable legs
        directly through the paper adapter.
        """
        is_arbitrage, locked_profit_per_contract = detect_implied_probability_arbitrage(
            market,
            qty,
        )
        if not is_arbitrage:
            return None

        if (
            features.time_to_close_hours is not None
            and features.time_to_close_hours < self.min_hours_to_close
        ):
            return None

        if market.yes_ask is None or market.no_ask is None:
            return None

        pair_id = str(uuid.uuid4())
        estimated_edge = float(locked_profit_per_contract)
        yes_intent = OrderIntent(
            ticker=market.ticker,
            side="yes",
            price=market.yes_ask,
            qty=qty,
            estimated_edge=estimated_edge,
            model_prob=float(market.yes_ask),
            run_id=run_id,
            signal_id=pair_id,
        )
        no_intent = OrderIntent(
            ticker=market.ticker,
            side="no",
            price=market.no_ask,
            qty=qty,
            estimated_edge=estimated_edge,
            model_prob=float(market.no_ask),
            run_id=run_id,
            signal_id=pair_id,
        )
        return SpreadCaptureIntent(
            yes_intent=yes_intent,
            no_intent=no_intent,
            pair_id=pair_id,
            max_resting_seconds=0,
        )

    def evaluate(
        self,
        market: MarketState,
        features: FeatureVector,
        run_id: str,
    ) -> SpreadCaptureIntent | None:
        """
        Return a linked yes/no order pair when conditions favor spread capture.

        The strategy joins the current bid and ask rather than crossing the spread.
        It has no directional view; model_prob records the posted market-side price.
        """
        if features.spread_pct is None or features.spread_pct < self.min_spread_pct:
            return None

        if (
            features.bid_ask_imbalance is None
            or abs(features.bid_ask_imbalance) > self.max_imbalance
        ):
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

        pair_id = str(uuid.uuid4())
        estimated_edge = float(market.yes_ask - market.yes_bid) / 2
        yes_intent = OrderIntent(
            ticker=market.ticker,
            side="yes",
            price=market.yes_bid,
            qty=self.qty_per_leg,
            estimated_edge=estimated_edge,
            model_prob=float(market.yes_bid),
            run_id=run_id,
            signal_id=pair_id,
        )
        no_intent = OrderIntent(
            ticker=market.ticker,
            side="no",
            price=market.yes_ask,
            qty=self.qty_per_leg,
            estimated_edge=estimated_edge,
            model_prob=float(market.yes_ask),
            run_id=run_id,
            signal_id=pair_id,
        )
        return SpreadCaptureIntent(
            yes_intent=yes_intent,
            no_intent=no_intent,
            pair_id=pair_id,
            max_resting_seconds=self.max_resting_seconds,
        )
