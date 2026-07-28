from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from dataclasses import replace

from core.execution.adapters import FillResult, PaperAdapter
from core.execution.resting_orders import RestingOrder, RestingOrderBook
from core.features.compute import compute_features
from core.risk.engine import OrderIntent, RiskEngine
from core.schemas.market import FeatureVector, MarketState, OrderIntentStatus, RiskDecision
from core.strategies.calibration_mispricing import (
    CalibrationMispricingPosition,
    CalibrationMispricingStrategy,
)
from core.strategies.event_drift import EventDriftPosition, EventDriftStrategy
from core.strategies.mean_reversion import MeanReversionPosition, MeanReversionStrategy
from core.strategies.spread_capture import SpreadCaptureIntent, SpreadCaptureStrategy
from worker.execution_repository import (
    PAPER_BUYING_POWER_BLOCK,
    close_position,
    credit_paper_realized_value,
    estimate_order_cash,
    get_or_create_paper_account,
    load_open_positions,
    persist_fill,
    persist_open_position,
    persist_order,
    persist_signal,
    record_paper_fill_accounting,
    release_paper_order_cash,
    reserve_paper_order_cash,
    resting_order_metadata,
    update_order_metadata,
    update_order_status,
    update_resting_order_state,
)
from worker.monitoring import emit_alert


logger = logging.getLogger(__name__)
PositionState = MeanReversionPosition | EventDriftPosition | CalibrationMispricingPosition
PositionKey = tuple[str, str]


class TradingRuntime:
    def __init__(
        self,
        run_id: str,
        tickers: list[str],
        strategy,
        risk_engine: RiskEngine,
        paper_adapter: PaperAdapter,
        history_window: int = 20,
        default_max_resting_seconds: int = 30,
        config_id: str | None = None,
        paper_account: dict | None = None,
    ):
        self.run_id = run_id
        self.config_id = config_id
        self.tickers = tickers
        self.strategy = strategy
        self.risk_engine = risk_engine
        self.paper_adapter = paper_adapter
        self.default_max_resting_seconds = default_max_resting_seconds
        self._resting_orders = RestingOrderBook(paper_adapter=self.paper_adapter)
        self._open_positions: dict[PositionKey, PositionState] = {}
        self.history_window = history_window
        self._history: dict[str, list[MarketState]] = {ticker: [] for ticker in tickers}
        self._running = False
        self._paused = False

        self._paper_account = (
            paper_account
            if paper_account is not None
            else get_or_create_paper_account(config_id)
            if config_id is not None
            else None
        )

        self._portfolio_value_usd = 1000.0
        self._current_exposure_usd = 0.0
        self._daily_realized_pnl_usd = 0.0
        self._refresh_paper_portfolio_state()
        self._kill_switch_active = False
        self._global_kill_switch = False

    def _refresh_paper_portfolio_state(self) -> None:
        if self._paper_account is None:
            return
        cash_balance = float(Decimal(str(self._paper_account.get("cash_balance", "0"))))
        reserved_cash = float(Decimal(str(self._paper_account.get("reserved_cash", "0"))))
        self._portfolio_value_usd = cash_balance
        self._current_exposure_usd = reserved_cash

    def _paper_account_has_cash(self, intent: OrderIntent) -> bool:
        if self._paper_account is None:
            return True
        cash_balance = Decimal(str(self._paper_account.get("cash_balance", "0")))
        reserved_cash = Decimal(str(self._paper_account.get("reserved_cash", "0")))
        return cash_balance - reserved_cash >= estimate_order_cash(intent)

    def restore_resting_orders(self, orders: list[RestingOrder]) -> None:
        for order in orders:
            self._resting_orders.restore_order(order)

    def restore_positions(self, rows: list[dict]) -> None:
        for row in rows:
            position = self._position_from_row(row)
            if position is None:
                continue
            self._set_position(position.ticker, position)

    def restore_positions_from_repository(self) -> None:
        self.restore_positions(load_open_positions(self.run_id))

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
        if isinstance(self.strategy, CalibrationMispricingStrategy):
            await self._process_calibration_mispricing_update(market, features, as_of)
            return

        if isinstance(self.strategy, EventDriftStrategy):
            await self._process_event_drift_update(market, features, as_of)
            return

        if isinstance(self.strategy, MeanReversionStrategy):
            await self._process_mean_reversion_update(market, features, as_of)
            return

        if isinstance(self.strategy, SpreadCaptureStrategy):
            arbitrage_intent = self.strategy.evaluate_arbitrage_entry(
                market,
                features,
                self.run_id,
                qty=self.strategy.qty_per_leg,
            )
            if arbitrage_intent is not None:
                await self._process_spread_capture_intent(
                    arbitrage_intent,
                    market,
                    features,
                )
                return

        intent = self.strategy.evaluate(market, features, self.run_id)
        if intent is None:
            return

        if isinstance(intent, SpreadCaptureIntent):
            await self._process_spread_capture_intent(intent, market, features)
            return

        await self._process_intent(intent, market, features)

    async def _process_mean_reversion_update(
        self,
        market: MarketState,
        features: FeatureVector,
        as_of: datetime,
    ) -> None:
        position = self._get_position(market.ticker)
        if position is not None:
            exit_intent = self.strategy.evaluate_exit(position, market, as_of=as_of)
            if exit_intent is None:
                return

            exit_intent.run_id = self.run_id
            fill_result = await self._process_intent(exit_intent, market, features)
            if fill_result is None or fill_result.fill_qty <= 0:
                return

            self._close_position(market.ticker, position)
            logger.info(
                "Mean reversion position closed ticker=%s side=%s entry_price=%s "
                "exit_price=%s qty=%s",
                market.ticker,
                position.side,
                position.entry_price,
                fill_result.fill_price,
                fill_result.fill_qty,
            )
            return

        entry_intent = self.strategy.evaluate_entry(market, features, self.run_id)
        if entry_intent is None:
            return

        fill_result = await self._process_intent(entry_intent, market, features)
        if fill_result is None or fill_result.fill_qty <= 0:
            return

        if market.yes_bid is None or market.yes_ask is None:
            return

        entry_mid_price = (market.yes_bid + market.yes_ask) / 2
        entry_spread_ticks = market.yes_ask - market.yes_bid
        position = MeanReversionPosition(
            ticker=market.ticker,
            side=entry_intent.side,
            entry_price=fill_result.fill_price,
            entry_mid_price=entry_mid_price,
            entry_spread_ticks=entry_spread_ticks,
            qty=fill_result.fill_qty,
            opened_at=as_of,
        )
        self._set_position(market.ticker, position)
        self._persist_position(position)
        logger.info(
            "Mean reversion position opened ticker=%s side=%s price=%s qty=%s",
            market.ticker,
            entry_intent.side,
            fill_result.fill_price,
            fill_result.fill_qty,
        )

    async def _process_event_drift_update(
        self,
        market: MarketState,
        features: FeatureVector,
        as_of: datetime,
    ) -> None:
        position = self._get_position(market.ticker)
        if position is not None:
            exit_intent = self.strategy.evaluate_exit(
                position,
                market,
                features,
                as_of=as_of,
            )
            if exit_intent is None:
                return

            exit_intent.run_id = self.run_id
            fill_result = await self._process_intent(exit_intent, market, features)
            if fill_result is None or fill_result.fill_qty <= 0:
                return

            self._close_position(market.ticker, position)
            logger.info(
                "Event drift position closed ticker=%s side=%s entry_price=%s "
                "exit_price=%s qty=%s",
                market.ticker,
                position.side,
                position.entry_price,
                fill_result.fill_price,
                fill_result.fill_qty,
            )
            return

        entry_intent = self.strategy.evaluate_entry(market, features, self.run_id)
        if entry_intent is None:
            return

        fill_result = await self._process_intent(entry_intent, market, features)
        if fill_result is None or fill_result.fill_qty <= 0:
            return

        if (
            market.yes_bid is None
            or market.yes_ask is None
            or features.price_momentum_1h is None
        ):
            return

        entry_mid_price = (market.yes_bid + market.yes_ask) / 2
        position = EventDriftPosition(
            ticker=market.ticker,
            side=entry_intent.side,
            entry_price=fill_result.fill_price,
            entry_mid_price=entry_mid_price,
            entry_momentum=features.price_momentum_1h,
            qty=fill_result.fill_qty,
            opened_at=as_of,
        )
        self._set_position(market.ticker, position)
        self._persist_position(position)
        logger.info(
            "Event drift position opened ticker=%s side=%s price=%s qty=%s",
            market.ticker,
            entry_intent.side,
            fill_result.fill_price,
            fill_result.fill_qty,
        )

    async def _process_calibration_mispricing_update(
        self,
        market: MarketState,
        features: FeatureVector,
        as_of: datetime,
    ) -> None:
        position = self._get_position(market.ticker)
        if position is not None:
            exit_intent = self.strategy.evaluate_exit(
                position,
                market,
                features,
                as_of=as_of,
            )
            if exit_intent is None:
                return

            exit_intent.run_id = self.run_id
            fill_result = await self._process_intent(exit_intent, market, features)
            if fill_result is None or fill_result.fill_qty <= 0:
                return

            self._close_position(market.ticker, position)
            logger.info(
                "Calibration mispricing position closed ticker=%s side=%s "
                "entry_price=%s exit_price=%s qty=%s",
                market.ticker,
                position.side,
                position.entry_price,
                fill_result.fill_price,
                fill_result.fill_qty,
            )
            return

        entry_intent = self.strategy.evaluate_entry(market, features, self.run_id)
        if entry_intent is None:
            return

        fill_result = await self._process_intent(entry_intent, market, features)
        if fill_result is None or fill_result.fill_qty <= 0:
            return

        position = CalibrationMispricingPosition(
            ticker=market.ticker,
            side=entry_intent.side,
            entry_price=fill_result.fill_price,
            entry_model_prob=entry_intent.model_prob,
            qty=fill_result.fill_qty,
            opened_at=as_of,
        )
        self._set_position(market.ticker, position)
        self._persist_position(position)
        logger.info(
            "Calibration mispricing position opened ticker=%s side=%s price=%s qty=%s",
            market.ticker,
            entry_intent.side,
            fill_result.fill_price,
            fill_result.fill_qty,
        )

    async def _process_spread_capture_intent(
        self,
        pair: SpreadCaptureIntent,
        market: MarketState,
        features: FeatureVector,
    ) -> None:
        if pair.max_resting_seconds == 0:
            await self._process_immediate_spread_capture_intent(pair, market, features)
            return

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

    async def _process_immediate_spread_capture_intent(
        self,
        pair: SpreadCaptureIntent,
        market: MarketState,
        features: FeatureVector,
    ) -> None:
        if pair.require_atomic_fill and not self._can_fill_arbitrage_pair(pair, market):
            logger.info(
                "Spread capture pair %s skipped: both legs must fully fill atomically",
                pair.pair_id,
            )
            return

        yes_fill = await self._process_intent(pair.yes_intent, market, features)
        no_fill = await self._process_intent(pair.no_intent, market, features)
        if (
            self._paper_account is not None
            and yes_fill is not None
            and no_fill is not None
            and yes_fill.fill_qty > 0
            and no_fill.fill_qty > 0
        ):
            locked_qty = min(yes_fill.fill_qty, no_fill.fill_qty)
            credit_paper_realized_value(
                account=self._paper_account,
                run_id=self.run_id,
                order_id=yes_fill.order_id,
                fill_id=None,
                ticker=pair.yes_intent.ticker,
                side="yes_no_pair",
                qty=locked_qty,
                reason="spread_capture_hedged_pair",
            )
            self._refresh_paper_portfolio_state()
        logger.info(
            "Immediate spread capture pair %s processed: yes_fill=%s no_fill=%s",
            pair.pair_id,
            yes_fill.status.value if yes_fill is not None else None,
            no_fill.status.value if no_fill is not None else None,
        )

    def _can_fill_arbitrage_pair(
        self,
        pair: SpreadCaptureIntent,
        market: MarketState,
    ) -> bool:
        qty = pair.yes_intent.qty
        if pair.yes_intent.qty != pair.no_intent.qty:
            return False
        if market.yes_ask is None or market.no_ask is None:
            return False

        yes_size = market.yes_ask_size if market.yes_ask_size is not None else 0
        no_size = market.no_ask_size if market.no_ask_size is not None else 0
        return yes_size >= qty and no_size >= qty

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
        blocked_by = result.blocked_by
        decision = result.decision
        if decision == RiskDecision.ALLOW and not self._paper_account_has_cash(intent):
            blocked_by = PAPER_BUYING_POWER_BLOCK
            decision = RiskDecision.BLOCK

        order_status = (
            OrderIntentStatus.SUBMITTED.value
            if decision == RiskDecision.ALLOW
            else "rejected"
        )
        signal_id = self._persist_signal_for_intent(intent, market, features, "resting_limit")
        order_id = persist_order(
            run_id=intent.run_id,
            ticker=intent.ticker,
            intent="enter",
            side=intent.side,
            price=intent.price,
            qty=intent.qty,
            risk_decision=decision.value,
            status=order_status,
            signal_id=signal_id,
            metadata={
                "blocked_by": blocked_by,
                "gates": [gate.gate for gate in result.gate_results],
                "estimated_edge": intent.estimated_edge,
                "model_prob": intent.model_prob,
                **resting_order_metadata(
                    pair_id=pair_id,
                    max_resting_seconds=max_resting_seconds,
                    created_at=as_of,
                    accumulated_fill_qty=0,
                    total_qty=intent.qty,
                ),
            },
        )

        if decision != RiskDecision.ALLOW:
            logger.info(
                "Resting order blocked ticker=%s decision=%s blocked_by=%s",
                intent.ticker,
                decision.value,
                blocked_by,
            )
            emit_alert(
                "order_blocked",
                {
                    "order_id": order_id,
                    "ticker": intent.ticker,
                    "decision": decision.value,
                    "blocked_by": blocked_by,
                },
            )
            return None

        if self._paper_account is not None and not reserve_paper_order_cash(
            account=self._paper_account,
            run_id=self.run_id,
            order_id=order_id,
            intent=intent,
        ):
            update_order_metadata(
                order_id,
                status="rejected",
                metadata={
                    "blocked_by": PAPER_BUYING_POWER_BLOCK,
                    "gates": [gate.gate for gate in result.gate_results],
                    "estimated_edge": intent.estimated_edge,
                    "model_prob": intent.model_prob,
                    **resting_order_metadata(
                        pair_id=pair_id,
                        max_resting_seconds=max_resting_seconds,
                        created_at=as_of,
                        accumulated_fill_qty=0,
                        total_qty=intent.qty,
                    ),
                },
            )
            emit_alert(
                "order_blocked",
                {
                    "order_id": order_id,
                    "ticker": intent.ticker,
                    "decision": RiskDecision.BLOCK.value,
                    "blocked_by": PAPER_BUYING_POWER_BLOCK,
                },
            )
            return None
        self._refresh_paper_portfolio_state()

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
            fill_id = persist_fill(order.order_id, fill_result)
            update_resting_order_state(order)
            if fill_result.fill_qty > 0:
                if self._paper_account is not None:
                    record_paper_fill_accounting(
                        account=self._paper_account,
                        run_id=self.run_id,
                        order_id=order.order_id,
                        fill_id=fill_id,
                        intent=order.intent,
                        fill_result=fill_result,
                        release_reserved_qty=fill_result.fill_qty,
                    )
                    if order.intent.is_closing_order:
                        credit_paper_realized_value(
                            account=self._paper_account,
                            run_id=self.run_id,
                            order_id=order.order_id,
                            fill_id=fill_id,
                            ticker=order.intent.ticker,
                            side=order.intent.side,
                            qty=fill_result.fill_qty,
                            reason="closing_order",
                        )
                    self._refresh_paper_portfolio_state()
                else:
                    self._current_exposure_usd += fill_result.fill_qty * float(
                        fill_result.fill_price
                    )
            elif fill_result.status == OrderIntentStatus.CANCELLED:
                if self._paper_account is not None:
                    release_paper_order_cash(
                        account=self._paper_account,
                        run_id=self.run_id,
                        order_id=order.order_id,
                        intent=order.intent,
                        qty=order.remaining_qty,
                        reason="cancelled",
                    )
                    self._refresh_paper_portfolio_state()

            if order.pair_id and fill_result.status == OrderIntentStatus.FILLED:
                for cancelled_order in self._resting_orders.cancel_pair(order.pair_id):
                    update_order_status(
                        cancelled_order.order_id,
                        OrderIntentStatus.CANCELLED.value,
                    )
                    update_resting_order_state(cancelled_order)
                    if self._paper_account is not None:
                        release_paper_order_cash(
                            account=self._paper_account,
                            run_id=self.run_id,
                            order_id=cancelled_order.order_id,
                            intent=cancelled_order.intent,
                            qty=cancelled_order.remaining_qty,
                            reason="sibling_cancelled",
                        )
                        self._refresh_paper_portfolio_state()
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
    ) -> FillResult | None:
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
        blocked_by = result.blocked_by
        decision = result.decision
        if decision == RiskDecision.ALLOW and not self._paper_account_has_cash(intent):
            blocked_by = PAPER_BUYING_POWER_BLOCK
            decision = RiskDecision.BLOCK

        order_status = "approved" if decision == RiskDecision.ALLOW or decision == RiskDecision.REDUCE_ONLY else "rejected"
        signal_id = self._persist_signal_for_intent(intent, market, features, "market")
        order_id = persist_order(
            run_id=intent.run_id,
            ticker=intent.ticker,
            intent="enter",
            side=intent.side,
            price=intent.price,
            qty=intent.qty,
            risk_decision=decision.value,
            status=order_status,
            signal_id=signal_id,
            metadata={
                "blocked_by": blocked_by,
                "gates": [gate.gate for gate in result.gate_results],
                "order_type": "market",
            },
        )

        if decision != RiskDecision.ALLOW:
            logger.info(
                "Order blocked ticker=%s decision=%s blocked_by=%s",
                intent.ticker,
                decision.value,
                blocked_by,
            )
            emit_alert(
                "order_blocked",
                {
                    "order_id": order_id,
                    "ticker": intent.ticker,
                    "decision": decision.value,
                    "blocked_by": blocked_by,
                },
            )
            return

        if result.decision == RiskDecision.REDUCE_ONLY:
            # construct a reduced-qty version of the intent before submitting
            intent = replace(intent, qty=result.approved_qty)
        fill_result = self.paper_adapter.submit_order(
            order_id=order_id,
            intent=intent,
            order_type="market",
            market=market,
        )
        fill_id = persist_fill(order_id, fill_result)

        if fill_result.fill_qty > 0:
            if self._paper_account is not None:
                record_paper_fill_accounting(
                    account=self._paper_account,
                    run_id=self.run_id,
                    order_id=order_id,
                    fill_id=fill_id,
                    intent=intent,
                    fill_result=fill_result,
                )
                if intent.is_closing_order:
                    credit_paper_realized_value(
                        account=self._paper_account,
                        run_id=self.run_id,
                        order_id=order_id,
                        fill_id=fill_id,
                        ticker=intent.ticker,
                        side=intent.side,
                        qty=fill_result.fill_qty,
                        reason="closing_order",
                    )
                self._refresh_paper_portfolio_state()
            else:
                self._current_exposure_usd += fill_result.fill_qty * float(
                    fill_result.fill_price
                )
        if fill_result.status == OrderIntentStatus.PARTIALLY_FILLED:
            self._rest_partially_filled_order(
                order_id=order_id,
                intent=intent,
                filled_qty=fill_result.fill_qty,
            )

        logger.info(
            "Order processed ticker=%s decision=%s fill_status=%s fill_qty=%s fill_price=%s",
            intent.ticker,
            decision.value,
            fill_result.status.value,
            fill_result.fill_qty,
            fill_result.fill_price,
        )
        return fill_result

    def _rest_partially_filled_order(
        self,
        *,
        order_id: str,
        intent: OrderIntent,
        filled_qty: int,
    ) -> None:
        if filled_qty >= intent.qty:
            return
        remaining_qty = intent.qty - filled_qty
        order = RestingOrder(
            order_id=order_id,
            intent=intent,
            order_type="limit",
            created_at=datetime.now(timezone.utc),
            max_resting_seconds=self.default_max_resting_seconds,
            pair_id=None,
            status=OrderIntentStatus.PARTIALLY_FILLED,
            accumulated_fill_qty=filled_qty,
        )
        if self._paper_account is not None and not reserve_paper_order_cash(
            account=self._paper_account,
            run_id=self.run_id,
            order_id=order_id,
            intent=OrderIntent(
                ticker=intent.ticker,
                side=intent.side,
                price=intent.price,
                qty=remaining_qty,
                estimated_edge=intent.estimated_edge,
                model_prob=intent.model_prob,
                run_id=intent.run_id,
                signal_id=intent.signal_id,
                is_closing_order=intent.is_closing_order,
            ),
        ):
            update_order_status(order_id, OrderIntentStatus.CANCELLED.value)
            return
        self._refresh_paper_portfolio_state()
        self._resting_orders.restore_order(order)
        update_resting_order_state(order)

    def _persist_signal_for_intent(
        self,
        intent: OrderIntent,
        market: MarketState,
        features: FeatureVector,
        order_type: str,
    ) -> str:
        return persist_signal(
            run_id=intent.run_id,
            ticker=intent.ticker,
            timestamp=features.timestamp,
            prob_estimate=intent.model_prob,
            edge=intent.estimated_edge,
            signal_id=intent.signal_id,
            payload={
                "strategy_runtime_run_id": self.run_id,
                "side": intent.side,
                "price": str(intent.price),
                "qty": intent.qty,
                "order_type": order_type,
                "is_closing_order": intent.is_closing_order,
                "market": {
                    "yes_bid": str(market.yes_bid) if market.yes_bid is not None else None,
                    "yes_ask": str(market.yes_ask) if market.yes_ask is not None else None,
                    "no_bid": str(market.no_bid) if market.no_bid is not None else None,
                    "no_ask": str(market.no_ask) if market.no_ask is not None else None,
                    "last_price": str(market.last_price) if market.last_price is not None else None,
                },
            },
        )

    def _position_key(self, ticker: str) -> PositionKey:
        return (self.run_id, ticker)

    def _get_position(self, ticker: str) -> PositionState | None:
        return self._open_positions.get(self._position_key(ticker))

    def _set_position(self, ticker: str, position: PositionState) -> None:
        self._open_positions[self._position_key(ticker)] = position

    def _close_position(self, ticker: str, position: PositionState) -> None:
        close_position(self.run_id, ticker, position.side)
        self._open_positions.pop(self._position_key(ticker), None)

    def _persist_position(self, position: PositionState) -> None:
        persist_open_position(
            run_id=self.run_id,
            ticker=position.ticker,
            side=position.side,
            qty=position.qty,
            avg_entry=position.entry_price,
            opened_at=position.opened_at,
            metadata=self._position_metadata(position),
        )

    def _position_metadata(self, position: PositionState) -> dict:
        if isinstance(position, MeanReversionPosition):
            return {
                "strategy_position_type": "mean_reversion",
                "entry_mid_price": str(position.entry_mid_price),
                "entry_spread_ticks": str(position.entry_spread_ticks),
            }
        if isinstance(position, EventDriftPosition):
            return {
                "strategy_position_type": "event_drift",
                "entry_mid_price": str(position.entry_mid_price),
                "entry_momentum": position.entry_momentum,
            }
        if isinstance(position, CalibrationMispricingPosition):
            return {
                "strategy_position_type": "calibration_mispricing",
                "entry_model_prob": position.entry_model_prob,
            }
        return {}

    def _position_from_row(self, row: dict) -> PositionState | None:
        metadata = row.get("metadata_json") or {}
        position_type = metadata.get("strategy_position_type")
        ticker = str(row["ticker"])
        side = str(row["side"])
        qty = int(row["qty"])
        entry_price = self._decimal(row["avg_entry"])
        opened_at = self._datetime(row["opened_at"])

        if position_type == "mean_reversion":
            return MeanReversionPosition(
                ticker=ticker,
                side=side,
                entry_price=entry_price,
                entry_mid_price=self._decimal(metadata["entry_mid_price"]),
                entry_spread_ticks=self._decimal(metadata["entry_spread_ticks"]),
                qty=qty,
                opened_at=opened_at,
            )
        if position_type == "event_drift":
            return EventDriftPosition(
                ticker=ticker,
                side=side,
                entry_price=entry_price,
                entry_mid_price=self._decimal(metadata["entry_mid_price"]),
                entry_momentum=float(metadata["entry_momentum"]),
                qty=qty,
                opened_at=opened_at,
            )
        if position_type == "calibration_mispricing":
            return CalibrationMispricingPosition(
                ticker=ticker,
                side=side,
                entry_price=entry_price,
                entry_model_prob=float(metadata["entry_model_prob"]),
                qty=qty,
                opened_at=opened_at,
            )
        return None

    def _decimal(self, value) -> Decimal:
        return Decimal(str(value))

    def _datetime(self, value) -> datetime:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        text = str(value)
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        return datetime.fromisoformat(text).astimezone(timezone.utc)

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
