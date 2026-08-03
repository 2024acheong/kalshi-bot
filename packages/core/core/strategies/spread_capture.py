from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
import logging

from core.execution.fees import (
    PAYOUT_CENTS_PER_CONTRACT,
    compute_kalshi_fee_cents,
    decimal_to_cents,
)
from core.risk.engine import OrderIntent
from core.schemas.market import FeatureVector, MarketState

logger = logging.getLogger(__name__)


@dataclass
class SpreadCaptureIntent:
    """
    A linked pair of order intents representing both sides of a spread capture attempt.

    yes_intent and no_intent must be tracked together. If one fills and the other
    does not within max_resting_seconds, the unfilled leg should be cancelled by
    the component managing open orders. This strategy only decides intent; it does
    not manage order lifecycle or cancellation.

    When max_resting_seconds=0, the runtime submits both legs as immediate market
    orders and skips the pair unless both sides can fully fill (fill-or-kill).
    """

    yes_intent: OrderIntent
    no_intent: OrderIntent
    pair_id: str
    max_resting_seconds: int
    require_atomic_fill: bool = True


def detect_implied_probability_arbitrage(
    market: MarketState,
    qty: int,
) -> tuple[bool, Decimal, int]:
    """
    Return whether buying YES ask and NO ask locks resolution profit after fees.

    One of the two contracts pays $1.00 at resolution, so the locked profit per
    contract is payout minus both ask prices minus the per-contract combined fees.
    """
    if market.no_bid is None or market.no_ask is None or market.yes_ask is None or market.yes_bid is None:
        return False, Decimal("0"), 0
    if qty <= 0:
        return False, Decimal("0"), 0

    # Aggregate payout, cost, and fees in integer cents so rounding matches
    # Kalshi's order-level fee rounding instead of dividing per-contract fees.
    yes_ask_cents = decimal_to_cents(market.yes_ask)
    no_ask_cents = decimal_to_cents(market.no_ask)
    cost_cents = (yes_ask_cents + no_ask_cents) * qty
    fee_cents = compute_kalshi_fee_cents(market.yes_ask, qty) + compute_kalshi_fee_cents(
        market.no_ask, qty
    )
    payout_cents = PAYOUT_CENTS_PER_CONTRACT * qty
    locked_profit_cents = payout_cents - cost_cents - fee_cents
    if locked_profit_cents <= 0:
        return False, Decimal("0"), 0

    locked_profit = Decimal(locked_profit_cents) / Decimal(PAYOUT_CENTS_PER_CONTRACT * qty)
    return True, locked_profit, locked_profit_cents


class SpreadCaptureStrategy:
    def __init__(
        self,
        min_profit_cents_total: int = 25,
        min_profit_per_contract: int = 2,
        min_spread_pct: float = 3.0,
        max_imbalance: float = 0.15,
        qty_per_leg: int = 10,
        max_resting_seconds: int = 30,
        min_hours_to_close: float = 0.5,
    ):
        self.min_profit_cents_total = min_profit_cents_total
        self.min_profit_per_contract = min_profit_per_contract
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
        is_arbitrage, locked_profit_per_contract, total_profit_cents = detect_implied_probability_arbitrage(
            market,
            qty,
        )
        if not is_arbitrage:
            logger.debug(
                "Skipping %s: no arbitrage",
                market.ticker,
            )
            return None

        profit_per_contract = total_profit_cents / qty
        if total_profit_cents < self.min_profit_cents_total:
            logger.debug(
                "Skipping %s: insufficient profit (%dc)",
                market.ticker,
                total_profit_cents,
            )
            return None

        if profit_per_contract < self.min_profit_per_contract:
            logger.debug(
                "Skipping %s: insufficient profit per contract (%dc)",
                market.ticker,
                profit_per_contract,
            )
            return None

        if (
            features.time_to_close_hours is not None
            and features.time_to_close_hours < self.min_hours_to_close
        ):
            logger.debug(
                "Skipping %s: too close to expiration",
                market.ticker,
            )
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
            model_prob=None,
            run_id=run_id,
            signal_id=pair_id,
        )
        no_intent = OrderIntent(
            ticker=market.ticker,
            side="no",
            price=market.no_ask,
            qty=qty,
            estimated_edge=estimated_edge,
            model_prob=None,
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
        Return only true YES/NO implied-probability arbitrage.

        The earlier passive spread-capture market-making path is intentionally
        disabled: it could fill one leg without the sibling and was not locked
        arbitrage. This strategy now trades only when buying YES ask and NO ask
        locks positive settlement value after fees.
        """
        return self.evaluate_arbitrage_entry(
            market,
            features,
            run_id,
            qty=self.qty_per_leg,
        )
