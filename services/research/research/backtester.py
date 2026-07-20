from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from core.execution.adapters import FillResult, PaperAdapter
from core.execution.resting_orders import RestingOrderBook
from core.features.compute import compute_features
from core.risk.engine import OrderIntent, RiskEngine
from core.schemas.market import MarketState, OrderIntentStatus, RiskDecision
from core.strategies.spread_capture import SpreadCaptureIntent
from research import data_loader
from research.metrics import BacktestMetrics, compute_metrics


@dataclass
class BacktestConfig:
    tickers: list[str]
    date_from: datetime
    date_to: datetime
    history_window: int = 20
    starting_portfolio_usd: float = 1000.0


class Backtester:
    def __init__(
        self,
        strategy: Any,
        risk_engine: RiskEngine,
        paper_adapter: PaperAdapter,
        config: BacktestConfig,
    ) -> None:
        self.strategy = strategy
        self.risk_engine = risk_engine
        self.paper_adapter = paper_adapter
        self.config = config
        self._make_adapter_replay_safe()
        self.resting_orders = RestingOrderBook(paper_adapter=self.paper_adapter)

    def run(self) -> dict[str, Any]:
        snapshots_by_ticker = data_loader.load_snapshots(
            self.config.tickers,
            self.config.date_from,
            self.config.date_to,
        )
        close_times = data_loader.get_close_times(self.config.tickers)
        timeline = self._build_timeline(snapshots_by_ticker)
        histories: dict[str, list[MarketState]] = defaultdict(list)
        current_exposure_usd = 0.0
        daily_realized_pnl_usd = 0.0
        total_intents = 0
        total_orders_allowed = 0
        total_orders_blocked = 0
        fills: list[dict[str, Any]] = []
        blocked_orders: list[dict[str, Any]] = []
        daily_pnl_by_date: dict[str, float] = defaultdict(float)

        for index, market in enumerate(timeline, start=1):
            close_time = close_times.get(market.ticker, market.close_time)
            replay_market = replace(market, close_time=close_time)
            history = histories[replay_market.ticker]
            history.insert(0, replay_market)
            del history[self.config.history_window :]

            features = compute_features(replay_market, history=history[: self.config.history_window])
            resting_events = self.resting_orders.check_tick(
                replay_market,
                as_of=replay_market.timestamp,
            )
            for order, fill_result in resting_events:
                fill = self._serialize_fill(
                    fill_result,
                    order.intent.model_prob,
                    order.intent.side,
                )
                fills.append(fill)
                if (
                    fill_result.status != OrderIntentStatus.CANCELLED
                    and fill_result.fill_qty > 0
                ):
                    current_exposure_usd, daily_realized_pnl_usd = self._apply_fill_pnl(
                        fill_result,
                        replay_market,
                        current_exposure_usd,
                        daily_realized_pnl_usd,
                        daily_pnl_by_date,
                    )
                if order.pair_id and fill_result.status == OrderIntentStatus.FILLED:
                    self.resting_orders.cancel_pair(order.pair_id)

            intent = self.strategy.evaluate(replay_market, features, run_id="backtest")
            if intent is None:
                continue

            total_intents += 1
            if isinstance(intent, SpreadCaptureIntent):
                order_intents = self._order_intents(intent)
            else:
                order_intents = [intent]

            for leg_index, order_intent in enumerate(order_intents, start=1):
                risk_result = self.risk_engine.evaluate(
                    intent=order_intent,
                    market=replay_market,
                    features=features,
                    open_positions=[],
                    market_category=None,
                    portfolio_value_usd=self.config.starting_portfolio_usd,
                    current_exposure_usd=current_exposure_usd,
                    daily_realized_pnl_usd=daily_realized_pnl_usd,
                )
                if risk_result.decision == RiskDecision.BLOCK:
                    total_orders_blocked += 1
                    blocked_orders.append(
                        self._serialize_blocked_order(
                            index=index,
                            leg_index=leg_index,
                            market=replay_market,
                            intent=order_intent,
                            risk_result=risk_result,
                        )
                    )
                    continue

                total_orders_allowed += 1
                if isinstance(intent, SpreadCaptureIntent):
                    self.resting_orders.add_order(
                        intent=order_intent,
                        max_resting_seconds=intent.max_resting_seconds,
                        as_of=replay_market.timestamp,
                        pair_id=intent.pair_id,
                        order_id=f"backtest-order-{index:08d}-{leg_index}",
                    )
                    continue

                fill_result = self.paper_adapter.submit_order(
                    order_id=f"backtest-order-{index:08d}-{leg_index}",
                    intent=order_intent,
                    order_type="limit",
                    market=replay_market,
                )
                fill = self._serialize_fill(
                    fill_result, order_intent.model_prob, order_intent.side
                )
                fills.append(fill)

                if (
                    fill_result.status != OrderIntentStatus.CANCELLED
                    and fill_result.fill_qty > 0
                ):
                    current_exposure_usd, daily_realized_pnl_usd = self._apply_fill_pnl(
                        fill_result,
                        replay_market,
                        current_exposure_usd,
                        daily_realized_pnl_usd,
                        daily_pnl_by_date,
                    )

        daily_pnl_series = [daily_pnl_by_date[date] for date in sorted(daily_pnl_by_date)]
        metrics = compute_metrics(fills, daily_pnl_series)
        return {
            "config": self._serialize_config(),
            "total_events": len(timeline),
            "total_intents": total_intents,
            "total_orders_allowed": total_orders_allowed,
            "total_orders_blocked": total_orders_blocked,
            "fills": fills,
            "blocked_orders": blocked_orders,
            "metrics": metrics,
        }

    def _make_adapter_replay_safe(self) -> None:
        if hasattr(self.paper_adapter, "config") and hasattr(
            self.paper_adapter.config, "staleness_threshold_ms"
        ):
            self.paper_adapter.config.staleness_threshold_ms = 10**18

    def _build_timeline(
        self,
        snapshots_by_ticker: dict[str, list[MarketState]],
    ) -> list[MarketState]:
        timeline = [
            snapshot
            for ticker in self.config.tickers
            for snapshot in snapshots_by_ticker.get(ticker, [])
        ]
        return sorted(timeline, key=lambda market: (market.timestamp, market.ticker))

    def _order_intents(
        self,
        intent: OrderIntent | SpreadCaptureIntent,
    ) -> list[OrderIntent]:
        if isinstance(intent, SpreadCaptureIntent):
            return [intent.yes_intent, intent.no_intent]
        return [intent]

    def _serialize_config(self) -> dict[str, Any]:
        config = asdict(self.config)
        config["date_from"] = self.config.date_from.isoformat()
        config["date_to"] = self.config.date_to.isoformat()
        return config

    def _serialize_fill(
        self,
        fill_result: FillResult,
        model_prob: float,
        side: str,
    ) -> dict[str, Any]:
        return {
            "order_id": fill_result.order_id,
            "fill_price": fill_result.fill_price,
            "fill_qty": fill_result.fill_qty,
            "fee": fill_result.fee,
            "fill_latency_ms": fill_result.fill_latency_ms,
            "fill_type": fill_result.fill_type,
            "status": fill_result.status.value,
            "side": side,
            "model_prob": model_prob,
        }

    def _apply_fill_pnl(
        self,
        fill_result: FillResult,
        market: MarketState,
        current_exposure_usd: float,
        daily_realized_pnl_usd: float,
        daily_pnl_by_date: dict[str, float],
    ) -> tuple[float, float]:
        notional = fill_result.fill_qty * float(fill_result.fill_price)
        fee = float(fill_result.fee)
        current_exposure_usd += notional
        pnl_proxy = notional - fee
        daily_realized_pnl_usd += pnl_proxy
        daily_pnl_by_date[market.timestamp.date().isoformat()] += pnl_proxy
        return current_exposure_usd, daily_realized_pnl_usd

    def _serialize_blocked_order(
        self,
        index: int,
        leg_index: int,
        market: MarketState,
        intent: OrderIntent,
        risk_result: Any,
    ) -> dict[str, Any]:
        blocking_gate = next(
            (
                gate
                for gate in risk_result.gate_results
                if gate.gate == risk_result.blocked_by
            ),
            None,
        )
        return {
            "order_id": f"backtest-order-{index:08d}-{leg_index}",
            "ticker": market.ticker,
            "timestamp": market.timestamp.isoformat(),
            "side": intent.side,
            "price": intent.price,
            "qty": intent.qty,
            "estimated_edge": intent.estimated_edge,
            "model_prob": intent.model_prob,
            "blocked_by": risk_result.blocked_by,
            "reason": blocking_gate.reason if blocking_gate else None,
            "metadata": blocking_gate.metadata if blocking_gate else {},
        }


def metrics_to_dict(metrics: BacktestMetrics) -> dict[str, Any]:
    return asdict(metrics)
