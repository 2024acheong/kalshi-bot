from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from core.execution.fees import compute_kalshi_fee
from core.risk.engine import OrderIntent
from core.schemas.market import MarketState, OrderIntentStatus


@dataclass
class FillResult:
    order_id: str
    fill_price: Decimal
    fill_qty: int
    fee: Decimal
    fill_latency_ms: int
    fill_type: str
    status: OrderIntentStatus


@dataclass
class SimulationConfig:
    fill_latency_ms: int = 200
    slippage_ticks: Decimal = Decimal("0.01")
    staleness_threshold_ms: int = 5000
    fee_per_contract: Decimal = Decimal("0.07")


def check_limit_traded_through(intent: OrderIntent, market: MarketState) -> bool:
    """Return True only when the market trades through this limit price."""
    if intent.side == "yes":
        return market.yes_ask is not None and market.yes_ask < intent.price

    if intent.side == "no":
        return market.yes_bid is not None and market.yes_bid > intent.price

    return False


def check_limit_crossed(intent: OrderIntent, market: MarketState) -> bool:
    """Backward-compatible alias for through-price paper fill checks."""
    return check_limit_traded_through(intent, market)


class BaseExecutionAdapter(ABC):
    @abstractmethod
    def submit_order(
        self,
        order_id: str,
        intent: OrderIntent,
        order_type: str,
        market: MarketState,
    ) -> FillResult:
        ...


class PaperAdapter(BaseExecutionAdapter):
    def __init__(self, config: SimulationConfig | None = None):
        self.config = config or SimulationConfig()

    def submit_order(
        self,
        order_id: str,
        intent: OrderIntent,
        order_type: str,
        market: MarketState,
    ) -> FillResult:
        age_ms = (datetime.now(timezone.utc) - market.timestamp).total_seconds() * 1000
        if age_ms > self.config.staleness_threshold_ms:
            return self._cancelled(order_id, intent)

        fill_price = self._fill_price(intent, order_type, market)
        if fill_price is None:
            return self._cancelled(order_id, intent)

        fill_qty = self._fill_qty(intent, market)
        if fill_qty == 0:
            return self._cancelled(order_id, intent)

        status = (
            OrderIntentStatus.PARTIALLY_FILLED
            if fill_qty < intent.qty
            else OrderIntentStatus.FILLED
        )
        fee = compute_kalshi_fee(
            fill_price,
            fill_qty,
            fee_per_contract=self.config.fee_per_contract,
        )

        return FillResult(
            order_id=order_id,
            fill_price=fill_price,
            fill_qty=fill_qty,
            fee=fee,
            fill_latency_ms=self.config.fill_latency_ms,
            fill_type="paper",
            status=status,
        )

    def _fill_price(
        self,
        intent: OrderIntent,
        order_type: str,
        market: MarketState,
    ) -> Decimal | None:
        if order_type == "limit":
            return self._limit_fill_price(intent, market)

        if order_type == "market":
            return self._market_fill_price(intent, market)

        return None

    def _limit_fill_price(self, intent: OrderIntent, market: MarketState) -> Decimal | None:
        if not check_limit_traded_through(intent, market):
            return None

        if intent.side == "yes":
            return market.yes_ask

        if intent.side == "no":
            return market.yes_bid

        return None

    def _market_fill_price(self, intent: OrderIntent, market: MarketState) -> Decimal | None:
        if intent.side == "yes":
            if market.yes_ask is None:
                return None
            return market.yes_ask + self.config.slippage_ticks

        if intent.side == "no":
            if market.yes_bid is None:
                return None
            return max(market.yes_bid - self.config.slippage_ticks, Decimal("0.01"))

        return None

    def _fill_qty(self, intent: OrderIntent, market: MarketState) -> int:
        available_size = market.yes_ask_size if intent.side == "yes" else market.yes_bid_size
        return min(intent.qty, available_size) if available_size is not None else intent.qty

    def _cancelled(self, order_id: str, intent: OrderIntent) -> FillResult:
        return FillResult(
            order_id=order_id,
            fill_price=intent.price,
            fill_qty=0,
            fee=Decimal("0"),
            fill_latency_ms=self.config.fill_latency_ms,
            fill_type="paper",
            status=OrderIntentStatus.CANCELLED,
        )
