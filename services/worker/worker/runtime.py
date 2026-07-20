from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.execution.adapters import FillResult, PaperAdapter
from core.execution.resting_orders import RestingOrder, RestingOrderBook
from core.features.compute import compute_features
from core.risk.engine import OrderIntent, RiskEngine
from core.schemas.market import FeatureVector, MarketState, OrderIntentStatus, RiskDecision
from core.strategies.spread_capture import SpreadCaptureIntent
from worker.execution_repository import persist_fill, persist_order, update_order_status
from worker.monitoring import emit_alert


logger = logging.getLogger(__name__)


class TradingRuntime:
    def __init__(
        self,
        run_id: str,
        tickers: list[str],
        strategy,
        risk_engine: RiskEngine,
        paper_adapter: PaperAdapter,
        history_window: int = 20,
    ):
        self.run_id = run_id
        self.tickers = tickers
        self.strategy = strategy
        self.risk_engine = risk_engine
        self.paper_adapter = paper_adapter
        self._resting_orders = RestingOrderBook(paper_adapter=self.paper_adapter)
        self.history_window = history_window
        self._history: dict[str, list[MarketState]] = {ticker: [] for ticker in tickers}
        self._running = False
        self._paused = False

        # Fake portfolio state for now - real state comes from positions table later.
        self._portfolio_value_usd = 1000.0
        self._current_exposure_usd = 0.0
        self._daily_realized_pnl_usd = 0.0
        self._kill_switch_active = False
        self._global_kill_switch = False

    async def on_market_update(self, market: MarketState) -> None:
        as_of = datetime.now(timezone.utc)
        self._process_resting_order_events(
            self._resting_orders.check_tick(market, as_of=as_of)
        )

        history = self._history.setdefault(market.ticker, [])
        history.insert(0, market)
        del history[self.history_window :]

        if self._paused or self._global_kill_switch:
            return

        features = compute_features(market, history=history)
        intent = self.strategy.evaluate(market, features, self.run_id)
        if intent is None:
            return

        if isinstance(intent, SpreadCaptureIntent):
            await self._process_spread_capture_intent(intent, market, features)
            return

        await self._process_intent(intent, market, features)

    async def _process_spread_capture_intent(
        self,
        pair: SpreadCaptureIntent,
        market: MarketState,
        features: FeatureVector,
    ) -> None:
        as_of = datetime.now(timezone.utc)
        yes_order_id = self._submit_resting_order_if_allowed(
            pair.yes_intent,
            market,
            features,
            max_resting_seconds=pair.max_resting_seconds,
            as_of=as_of,
            pair_id=pair.pair_id,
        )
        no_order_id = self._submit_resting_order_if_allowed(
            pair.no_intent,
            market,
            features,
            max_resting_seconds=pair.max_resting_seconds,
            as_of=as_of,
            pair_id=pair.pair_id,
        )
        logger.info(
            "Spread capture pair %s: yes_order=%s no_order=%s submitted to resting book",
            pair.pair_id,
            yes_order_id,
            no_order_id,
        )

    def _submit_resting_order_if_allowed(
        self,
        intent: OrderIntent,
        market: MarketState,
        features: FeatureVector,
        max_resting_seconds: int,
        as_of: datetime,
        pair_id: str | None,
    ) -> str | None:
        result = self.risk_engine.evaluate(
            intent=intent,
            market=market,
            features=features,
            open_positions=[],
            market_category=None,
            portfolio_value_usd=self._portfolio_value_usd,
            current_exposure_usd=self._current_exposure_usd,
            daily_realized_pnl_usd=self._daily_realized_pnl_usd,
            kill_switch_active=self._kill_switch_active,
            global_kill_switch=self._global_kill_switch,
        )
        order_status = (
            OrderIntentStatus.SUBMITTED.value
            if result.decision == RiskDecision.ALLOW
            else "rejected"
        )
        order_id = persist_order(
            run_id=intent.run_id,
            ticker=intent.ticker,
            intent="enter",
            side=intent.side,
            price=intent.price,
            qty=intent.qty,
            risk_decision=result.decision.value,
            status=order_status,
            signal_id=intent.signal_id,
            metadata={
                "blocked_by": result.blocked_by,
                "gates": [gate.gate for gate in result.gate_results],
                "pair_id": pair_id,
            },
        )

        if result.decision != RiskDecision.ALLOW:
            logger.info(
                "Resting order blocked ticker=%s decision=%s blocked_by=%s",
                intent.ticker,
                result.decision.value,
                result.blocked_by,
            )
            emit_alert(
                "order_blocked",
                {
                    "order_id": order_id,
                    "ticker": intent.ticker,
                    "decision": result.decision.value,
                    "blocked_by": result.blocked_by,
                },
            )
            return None

        return self._resting_orders.add_order(
            intent=intent,
            max_resting_seconds=max_resting_seconds,
            as_of=as_of,
            pair_id=pair_id,
            order_id=order_id,
        )

    def _process_resting_order_events(
        self,
        events: list[tuple[RestingOrder, FillResult]],
    ) -> None:
        for order, fill_result in events:
            persist_fill(order.order_id, fill_result)
            if fill_result.fill_qty > 0:
                self._current_exposure_usd += fill_result.fill_qty * float(
                    fill_result.fill_price
                )

            if order.pair_id and fill_result.status == OrderIntentStatus.FILLED:
                for cancelled_order in self._resting_orders.cancel_pair(order.pair_id):
                    update_order_status(
                        cancelled_order.order_id,
                        OrderIntentStatus.CANCELLED.value,
                    )
                    logger.info(
                        "Cancelled spread capture sibling order=%s pair_id=%s",
                        cancelled_order.order_id,
                        order.pair_id,
                    )

    async def _process_intent(
        self,
        intent: OrderIntent,
        market: MarketState,
        features: FeatureVector,
    ) -> None:
        result = self.risk_engine.evaluate(
            intent=intent,
            market=market,
            features=features,
            open_positions=[],
            market_category=None,
            portfolio_value_usd=self._portfolio_value_usd,
            current_exposure_usd=self._current_exposure_usd,
            daily_realized_pnl_usd=self._daily_realized_pnl_usd,
            kill_switch_active=self._kill_switch_active,
            global_kill_switch=self._global_kill_switch,
        )
        order_status = "approved" if result.decision == RiskDecision.ALLOW else "rejected"
        order_id = persist_order(
            run_id=intent.run_id,
            ticker=intent.ticker,
            intent="enter",
            side=intent.side,
            price=intent.price,
            qty=intent.qty,
            risk_decision=result.decision.value,
            status=order_status,
            signal_id=intent.signal_id,
            metadata={
                "blocked_by": result.blocked_by,
                "gates": [gate.gate for gate in result.gate_results],
            },
        )

        if result.decision != RiskDecision.ALLOW:
            logger.info(
                "Order blocked ticker=%s decision=%s blocked_by=%s",
                intent.ticker,
                result.decision.value,
                result.blocked_by,
            )
            emit_alert(
                "order_blocked",
                {
                    "order_id": order_id,
                    "ticker": intent.ticker,
                    "decision": result.decision.value,
                    "blocked_by": result.blocked_by,
                },
            )
            return

        fill_result = self.paper_adapter.submit_order(
            order_id=order_id,
            intent=intent,
            order_type="limit",
            market=market,
        )
        persist_fill(order_id, fill_result)

        if fill_result.fill_qty > 0:
            self._current_exposure_usd += fill_result.fill_qty * float(fill_result.fill_price)

        logger.info(
            "Order processed ticker=%s decision=%s fill_status=%s fill_qty=%s fill_price=%s",
            intent.ticker,
            result.decision.value,
            fill_result.status.value,
            fill_result.fill_qty,
            fill_result.fill_price,
        )

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def stop(self) -> None:
        self._running = False

    def activate_kill_switch(self) -> None:
        self._kill_switch_active = True
        self._global_kill_switch = True

    def deactivate_kill_switch(self) -> None:
        self._global_kill_switch = False
        self._kill_switch_active = False
