from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from core.execution.adapters import FillResult, PaperAdapter, check_limit_crossed
from core.risk.engine import OrderIntent
from core.schemas.market import MarketState, OrderIntentStatus


@dataclass
class RestingOrder:
    """
    A limit order that persists across ticks until filled, cancelled, or expired.
    """

    order_id: str
    intent: OrderIntent
    order_type: str
    created_at: datetime
    max_resting_seconds: int
    pair_id: str | None = None
    status: OrderIntentStatus = OrderIntentStatus.SUBMITTED
    accumulated_fill_qty: int = 0


class RestingOrderBook:
    """
    Tracks open resting orders and re-evaluates them against each new market tick.
    """

    def __init__(self, paper_adapter: PaperAdapter):
        self.paper_adapter = paper_adapter
        self._open_orders: dict[str, RestingOrder] = {}
        self._next_order_number = 1

    def add_order(
        self,
        intent: OrderIntent,
        max_resting_seconds: int,
        as_of: datetime,
        pair_id: str | None = None,
        order_id: str | None = None,
    ) -> str:
        """
        Register a new resting order without attempting an immediate fill check.
        """
        order_id = order_id or self._generate_order_id()
        self._open_orders[order_id] = RestingOrder(
            order_id=order_id,
            intent=intent,
            order_type="limit",
            created_at=as_of,
            max_resting_seconds=max_resting_seconds,
            pair_id=pair_id,
        )
        return order_id

    def check_tick(
        self,
        market: MarketState,
        as_of: datetime,
    ) -> list[tuple[RestingOrder, FillResult]]:
        """
        Check open orders for this ticker against a new market tick.

        Determinism note: this method never reads wall-clock time. Callers pass
        as_of explicitly; live workers can pass real current time, while
        backtests should pass the snapshot timestamp.
        """
        results: list[tuple[RestingOrder, FillResult]] = []
        for order in list(self._open_orders.values()):
            if order.intent.ticker != market.ticker:
                continue

            if (as_of - order.created_at).total_seconds() > order.max_resting_seconds:
                order.status = OrderIntentStatus.CANCELLED
                self._open_orders.pop(order.order_id, None)
                results.append((replace(order), self._cancelled_fill(order)))
                continue

            if not check_limit_crossed(order.intent, market):
                continue

            remaining_qty = order.intent.qty - order.accumulated_fill_qty
            if remaining_qty <= 0:
                order.status = OrderIntentStatus.FILLED
                self._open_orders.pop(order.order_id, None)
                continue

            remaining_intent = replace(order.intent, qty=remaining_qty)
            fill_result = self.paper_adapter.submit_order(
                order.order_id,
                remaining_intent,
                order.order_type,
                market,
            )
            if fill_result.fill_qty <= 0:
                continue

            order.accumulated_fill_qty += fill_result.fill_qty
            order.status = (
                OrderIntentStatus.FILLED
                if order.accumulated_fill_qty >= order.intent.qty
                else OrderIntentStatus.PARTIALLY_FILLED
            )
            if order.status == OrderIntentStatus.FILLED:
                self._open_orders.pop(order.order_id, None)

            results.append((replace(order), fill_result))

        return results

    def cancel_order(self, order_id: str) -> RestingOrder | None:
        order = self._open_orders.pop(order_id, None)
        if order is None:
            return None
        order.status = OrderIntentStatus.CANCELLED
        return order

    def cancel_pair(self, pair_id: str) -> list[RestingOrder]:
        cancelled = []
        for order in list(self._open_orders.values()):
            if order.pair_id != pair_id:
                continue
            self._open_orders.pop(order.order_id, None)
            order.status = OrderIntentStatus.CANCELLED
            cancelled.append(order)
        return cancelled

    def get_open_orders(self, ticker: str | None = None) -> list[RestingOrder]:
        orders = list(self._open_orders.values())
        if ticker is None:
            return orders
        return [order for order in orders if order.intent.ticker == ticker]

    def _generate_order_id(self) -> str:
        order_id = f"resting-order-{self._next_order_number:08d}"
        self._next_order_number += 1
        return order_id

    def _cancelled_fill(self, order: RestingOrder) -> FillResult:
        return FillResult(
            order_id=order.order_id,
            fill_price=order.intent.price,
            fill_qty=0,
            fee=Decimal("0"),
            fill_latency_ms=self.paper_adapter.config.fill_latency_ms,
            fill_type="paper",
            status=OrderIntentStatus.CANCELLED,
        )
