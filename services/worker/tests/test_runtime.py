from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from core.execution.adapters import FillResult
from core.features.compute import compute_features
from core.risk.engine import OrderIntent, RiskEngineResult, RiskGateResult
from core.schemas.market import (
    FeatureVector,
    MarketState,
    MarketStatus,
    OrderIntentStatus,
    RiskDecision,
)
from core.strategies.event_drift import EventDriftPosition, EventDriftStrategy
from core.strategies.mean_reversion import MeanReversionPosition, MeanReversionStrategy
from core.strategies.spread_capture import SpreadCaptureIntent
from worker.runtime import TradingRuntime
from worker.strategies.dummy import DummyStrategy


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make_market(**kwargs) -> MarketState:
    now = datetime.now(timezone.utc)
    defaults = {
        "ticker": "KXBTC-26APR-B90000",
        "timestamp": now,
        "yes_bid": Decimal("0.46"),
        "yes_ask": Decimal("0.48"),
        "yes_bid_size": 100,
        "yes_ask_size": 100,
        "last_price": Decimal("0.47"),
        "volume_24h": 1000,
        "open_interest": 5000,
        "close_time": now + timedelta(hours=24),
        "status": MarketStatus.OPEN,
        "source": "rest_poll",
    }
    defaults.update(kwargs)
    return MarketState(**defaults)


def make_intent(**kwargs) -> OrderIntent:
    defaults = {
        "ticker": "KXBTC-26APR-B90000",
        "side": "yes",
        "price": Decimal("0.48"),
        "qty": 10,
        "estimated_edge": 0.05,
        "model_prob": 0.53,
        "run_id": "run-1",
        "signal_id": None,
    }
    defaults.update(kwargs)
    return OrderIntent(**defaults)


def make_features(market: MarketState, **kwargs):
    features = compute_features(market)
    for key, value in kwargs.items():
        setattr(features, key, value)
    return features


def make_risk_result(intent: OrderIntent, decision: RiskDecision) -> RiskEngineResult:
    gate_result = RiskGateResult(
        gate="kelly",
        decision=decision,
        reason="test",
        metadata={},
    )
    return RiskEngineResult(
        intent=intent,
        decision=decision,
        approved_qty=intent.qty if decision == RiskDecision.ALLOW else 0,
        gate_results=[gate_result],
        blocked_by=None if decision == RiskDecision.ALLOW else "kelly",
    )


def make_runtime(strategy=None, risk_engine=None, paper_adapter=None, history_window: int = 20):
    return TradingRuntime(
        run_id="run-1",
        tickers=["KXBTC-26APR-B90000"],
        strategy=strategy or DummyStrategy(),
        risk_engine=risk_engine or MagicMock(),
        paper_adapter=paper_adapter or MagicMock(),
        history_window=history_window,
    )


def test_dummy_strategy_holds_on_wide_spread() -> None:
    market = make_market()
    features = make_features(market, spread_pct=20.0)

    intent = DummyStrategy().evaluate(market, features, run_id="run-1")

    assert intent is None


def test_dummy_strategy_generates_intent_on_good_conditions() -> None:
    market = make_market(yes_ask=Decimal("0.48"))
    features = make_features(market, spread_pct=4.0, time_to_close_hours=24.0)

    intent = DummyStrategy().evaluate(market, features, run_id="run-1")

    assert isinstance(intent, OrderIntent)
    assert intent.ticker == market.ticker
    assert intent.side == "yes"
    assert intent.price == Decimal("0.48")
    assert intent.qty == 10


def test_runtime_activate_kill_switch_sets_flags() -> None:
    runtime = make_runtime()

    runtime.activate_kill_switch()

    assert runtime._kill_switch_active is True
    assert runtime._global_kill_switch is True


def test_runtime_deactivate_kill_switch_clears_flags() -> None:
    runtime = make_runtime()
    runtime.activate_kill_switch()

    runtime.deactivate_kill_switch()

    assert runtime._kill_switch_active is False
    assert runtime._global_kill_switch is False


@pytest.mark.anyio
async def test_runtime_paused_does_not_evaluate() -> None:
    strategy = MagicMock()
    runtime = make_runtime(strategy=strategy)
    runtime.pause()

    await runtime.on_market_update(make_market())

    strategy.evaluate.assert_not_called()


@pytest.mark.anyio
async def test_runtime_kill_switch_does_not_evaluate() -> None:
    strategy = MagicMock()
    runtime = make_runtime(strategy=strategy)
    runtime._global_kill_switch = True

    await runtime.on_market_update(make_market())

    strategy.evaluate.assert_not_called()


@pytest.mark.anyio
async def test_runtime_processes_allowed_intent(monkeypatch) -> None:
    market = make_market()
    intent = make_intent()
    strategy = MagicMock()
    strategy.evaluate.return_value = intent
    risk_engine = MagicMock()
    risk_engine.evaluate.return_value = make_risk_result(intent, RiskDecision.ALLOW)
    fill_result = FillResult(
        order_id="order-1",
        fill_price=Decimal("0.48"),
        fill_qty=10,
        fee=Decimal("0.17"),
        fill_latency_ms=200,
        fill_type="paper",
        status=OrderIntentStatus.FILLED,
    )
    paper_adapter = MagicMock()
    paper_adapter.submit_order.return_value = fill_result
    persist_order = MagicMock(return_value="order-1")
    persist_fill = MagicMock(return_value="fill-1")
    monkeypatch.setattr("worker.runtime.persist_order", persist_order)
    monkeypatch.setattr("worker.runtime.persist_fill", persist_fill)

    runtime = make_runtime(
        strategy=strategy,
        risk_engine=risk_engine,
        paper_adapter=paper_adapter,
    )
    await runtime.on_market_update(market)

    persist_order.assert_called_once()
    persist_fill.assert_called_once_with("order-1", fill_result)
    paper_adapter.submit_order.assert_called_once()
    assert runtime._current_exposure_usd == 4.8


@pytest.mark.anyio
async def test_runtime_processes_spread_capture_pair(monkeypatch) -> None:
    market = make_market()
    yes_intent = make_intent(side="yes", price=Decimal("0.46"))
    no_intent = make_intent(side="no", price=Decimal("0.48"))
    pair = SpreadCaptureIntent(
        yes_intent=yes_intent,
        no_intent=no_intent,
        pair_id="pair-1",
        max_resting_seconds=30,
    )
    strategy = MagicMock()
    strategy.evaluate.return_value = pair
    risk_engine = MagicMock()
    risk_engine.evaluate.side_effect = lambda intent, **kwargs: make_risk_result(
        intent, RiskDecision.ALLOW
    )
    persist_order = MagicMock(side_effect=["yes-order", "no-order"])
    persist_fill = MagicMock()
    monkeypatch.setattr("worker.runtime.persist_order", persist_order)
    monkeypatch.setattr("worker.runtime.persist_fill", persist_fill)

    runtime = make_runtime(strategy=strategy, risk_engine=risk_engine)
    await runtime.on_market_update(market)

    assert persist_order.call_count == 2
    persist_fill.assert_not_called()
    open_orders = runtime._resting_orders.get_open_orders()
    assert [order.order_id for order in open_orders] == ["yes-order", "no-order"]
    assert {order.pair_id for order in open_orders} == {"pair-1"}


@pytest.mark.anyio
async def test_runtime_opens_mean_reversion_position(monkeypatch) -> None:
    market = make_market(yes_bid=Decimal("0.49"), yes_ask=Decimal("0.51"))
    features = FeatureVector(
        ticker=market.ticker,
        timestamp=market.timestamp,
        mid_price=0.50,
        spread_pct=4.0,
        spread_ticks=0.02,
        bid_ask_imbalance=0.0,
        time_to_close_hours=24.0,
        implied_probability=0.50,
        liquidity_score=100.0,
        price_momentum_1h=0.05,
        price_momentum_24h=None,
        volume_zscore=0.0,
        open_interest_delta=None,
    )
    monkeypatch.setattr("worker.runtime.compute_features", lambda market, history: features)
    risk_engine = MagicMock()
    risk_engine.evaluate.side_effect = lambda intent, **kwargs: make_risk_result(
        intent, RiskDecision.ALLOW
    )
    fill_result = FillResult(
        order_id="order-1",
        fill_price=Decimal("0.49"),
        fill_qty=10,
        fee=Decimal("0.17"),
        fill_latency_ms=200,
        fill_type="paper",
        status=OrderIntentStatus.FILLED,
    )
    paper_adapter = MagicMock()
    paper_adapter.submit_order.return_value = fill_result
    monkeypatch.setattr("worker.runtime.persist_order", MagicMock(return_value="order-1"))
    monkeypatch.setattr("worker.runtime.persist_fill", MagicMock())

    runtime = make_runtime(
        strategy=MeanReversionStrategy(),
        risk_engine=risk_engine,
        paper_adapter=paper_adapter,
    )
    await runtime.on_market_update(market)

    position = runtime._open_positions[market.ticker]
    assert position.side == "no"
    assert position.entry_price == Decimal("0.49")
    assert position.qty == 10


@pytest.mark.anyio
async def test_runtime_closes_mean_reversion_position(monkeypatch) -> None:
    market = make_market(yes_bid=Decimal("0.47"), yes_ask=Decimal("0.49"))
    risk_engine = MagicMock()
    risk_engine.evaluate.side_effect = lambda intent, **kwargs: make_risk_result(
        intent, RiskDecision.ALLOW
    )
    fill_result = FillResult(
        order_id="order-2",
        fill_price=Decimal("0.49"),
        fill_qty=10,
        fee=Decimal("0.17"),
        fill_latency_ms=200,
        fill_type="paper",
        status=OrderIntentStatus.FILLED,
    )
    paper_adapter = MagicMock()
    paper_adapter.submit_order.return_value = fill_result
    monkeypatch.setattr("worker.runtime.persist_order", MagicMock(return_value="order-2"))
    monkeypatch.setattr("worker.runtime.persist_fill", MagicMock())

    runtime = make_runtime(
        strategy=MeanReversionStrategy(),
        risk_engine=risk_engine,
        paper_adapter=paper_adapter,
    )
    runtime._open_positions[market.ticker] = MeanReversionPosition(
        ticker=market.ticker,
        side="no",
        entry_price=Decimal("0.49"),
        entry_mid_price=Decimal("0.50"),
        entry_spread_ticks=Decimal("0.02"),
        qty=10,
        opened_at=market.timestamp,
    )
    await runtime.on_market_update(market)

    assert market.ticker not in runtime._open_positions


@pytest.mark.anyio
async def test_runtime_opens_and_closes_event_drift_position(monkeypatch) -> None:
    market = make_market(yes_bid=Decimal("0.49"), yes_ask=Decimal("0.51"))
    entry_features = FeatureVector(
        ticker=market.ticker,
        timestamp=market.timestamp,
        mid_price=0.50,
        spread_pct=4.0,
        spread_ticks=0.02,
        bid_ask_imbalance=0.3,
        time_to_close_hours=24.0,
        implied_probability=0.50,
        liquidity_score=100.0,
        price_momentum_1h=0.05,
        price_momentum_24h=None,
        volume_zscore=2.0,
        open_interest_delta=None,
    )
    exit_features = FeatureVector(
        ticker=market.ticker,
        timestamp=market.timestamp,
        mid_price=0.50,
        spread_pct=4.0,
        spread_ticks=0.02,
        bid_ask_imbalance=0.3,
        time_to_close_hours=24.0,
        implied_probability=0.50,
        liquidity_score=100.0,
        price_momentum_1h=0.01,
        price_momentum_24h=None,
        volume_zscore=2.0,
        open_interest_delta=None,
    )
    compute_features_mock = MagicMock(side_effect=[entry_features, exit_features])
    monkeypatch.setattr("worker.runtime.compute_features", compute_features_mock)
    risk_engine = MagicMock()
    risk_engine.evaluate.side_effect = lambda intent, **kwargs: make_risk_result(
        intent, RiskDecision.ALLOW
    )
    paper_adapter = MagicMock()
    paper_adapter.submit_order.side_effect = [
        FillResult(
            order_id="order-1",
            fill_price=Decimal("0.51"),
            fill_qty=10,
            fee=Decimal("0.17"),
            fill_latency_ms=200,
            fill_type="paper",
            status=OrderIntentStatus.FILLED,
        ),
        FillResult(
            order_id="order-2",
            fill_price=Decimal("0.49"),
            fill_qty=10,
            fee=Decimal("0.17"),
            fill_latency_ms=200,
            fill_type="paper",
            status=OrderIntentStatus.FILLED,
        ),
    ]
    monkeypatch.setattr(
        "worker.runtime.persist_order",
        MagicMock(side_effect=["order-1", "order-2"]),
    )
    monkeypatch.setattr("worker.runtime.persist_fill", MagicMock())

    runtime = make_runtime(
        strategy=EventDriftStrategy(),
        risk_engine=risk_engine,
        paper_adapter=paper_adapter,
    )
    await runtime.on_market_update(market)

    position = runtime._open_positions[market.ticker]
    assert isinstance(position, EventDriftPosition)
    assert position.side == "yes"
    assert position.entry_momentum == 0.05

    await runtime.on_market_update(market)

    assert market.ticker not in runtime._open_positions


@pytest.mark.anyio
async def test_runtime_skips_paper_adapter_when_blocked(monkeypatch) -> None:
    market = make_market()
    intent = make_intent()
    strategy = MagicMock()
    strategy.evaluate.return_value = intent
    risk_engine = MagicMock()
    risk_engine.evaluate.return_value = make_risk_result(intent, RiskDecision.BLOCK)
    paper_adapter = MagicMock()
    persist_order = MagicMock(return_value="order-1")
    persist_fill = MagicMock(return_value="fill-1")
    emit_alert = MagicMock()
    monkeypatch.setattr("worker.runtime.persist_order", persist_order)
    monkeypatch.setattr("worker.runtime.persist_fill", persist_fill)
    monkeypatch.setattr("worker.runtime.emit_alert", emit_alert)

    runtime = make_runtime(
        strategy=strategy,
        risk_engine=risk_engine,
        paper_adapter=paper_adapter,
    )
    await runtime.on_market_update(market)

    persist_order.assert_called_once()
    persist_fill.assert_not_called()
    paper_adapter.submit_order.assert_not_called()
    emit_alert.assert_called_once()


@pytest.mark.anyio
async def test_runtime_history_window_truncates() -> None:
    strategy = MagicMock()
    strategy.evaluate.return_value = None
    runtime = make_runtime(strategy=strategy, history_window=3)

    for index in range(5):
        await runtime.on_market_update(
            make_market(timestamp=datetime.now(timezone.utc) + timedelta(seconds=index))
        )

    assert len(runtime._history["KXBTC-26APR-B90000"]) == 3
