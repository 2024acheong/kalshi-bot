from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.execution.adapters import PaperAdapter, SimulationConfig, check_limit_crossed
from core.execution.resting_orders import RestingOrder, RestingOrderBook
from core.risk.engine import OrderIntent
from core.schemas.market import MarketState, MarketStatus, OrderIntentStatus


BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def make_market(**kwargs) -> MarketState:
    defaults = {
        "ticker": "KXTEST-26JAN-YES",
        "timestamp": BASE_TIME,
        "yes_bid": Decimal("0.40"),
        "yes_ask": Decimal("0.50"),
        "yes_bid_size": 100,
        "yes_ask_size": 100,
        "last_price": Decimal("0.45"),
        "volume_24h": 1000,
        "open_interest": 5000,
        "close_time": BASE_TIME + timedelta(hours=24),
        "status": MarketStatus.OPEN,
        "source": "rest_poll",
        "no_bid": Decimal("0.50"),
        "no_ask": Decimal("0.55"),
        "no_bid_size": 100,
        "no_ask_size": 100,
    }
    defaults.update(kwargs)
    return MarketState(**defaults)


def make_intent(**kwargs) -> OrderIntent:
    defaults = {
        "ticker": "KXTEST-26JAN-YES",
        "side": "yes",
        "price": Decimal("0.45"),
        "qty": 20,
        "estimated_edge": 0.01,
        "model_prob": 0.45,
        "run_id": "run-1",
        "signal_id": "signal-1",
    }
    defaults.update(kwargs)
    return OrderIntent(**defaults)


def make_book() -> RestingOrderBook:
    return RestingOrderBook(
        PaperAdapter(SimulationConfig(staleness_threshold_ms=10**18))
    )


def test_order_does_not_fill_when_not_crossed() -> None:
    book = make_book()
    order_id = book.add_order(make_intent(), 30, BASE_TIME)

    results = book.check_tick(make_market(yes_ask=Decimal("0.50")), BASE_TIME)

    assert results == []
    assert book.get_open_orders()[0].order_id == order_id


def test_order_fills_on_touch() -> None:
    book = make_book()
    book.add_order(make_intent(price=Decimal("0.45")), 30, BASE_TIME)

    results = book.check_tick(make_market(yes_ask=Decimal("0.45")), BASE_TIME)

    assert len(results) == 1
    assert results[0][1].status == OrderIntentStatus.FILLED
    assert results[0][1].fill_qty == 20
    assert book.get_open_orders() == []


def test_order_fills_when_market_crosses() -> None:
    book = make_book()
    order_id = book.add_order(make_intent(), 30, BASE_TIME)

    results = book.check_tick(make_market(yes_ask=Decimal("0.44")), BASE_TIME)

    assert len(results) == 1
    order, fill = results[0]
    assert order.order_id == order_id
    assert fill.status == OrderIntentStatus.FILLED
    assert fill.fill_qty == 20
    assert book.get_open_orders() == []


def test_order_cancelled_after_max_resting_seconds() -> None:
    book = make_book()
    book.add_order(make_intent(), 10, BASE_TIME)

    results = book.check_tick(
        make_market(yes_ask=Decimal("0.50")),
        BASE_TIME + timedelta(seconds=11),
    )

    assert len(results) == 1
    order, fill = results[0]
    assert order.status == OrderIntentStatus.CANCELLED
    assert fill.status == OrderIntentStatus.CANCELLED
    assert fill.fill_qty == 0
    assert book.get_open_orders() == []


def test_partial_fill_across_multiple_ticks() -> None:
    book = make_book()
    book.add_order(make_intent(qty=20), 30, BASE_TIME)

    first = book.check_tick(
        make_market(yes_ask=Decimal("0.44"), yes_ask_size=10),
        BASE_TIME + timedelta(seconds=1),
    )
    open_order = book.get_open_orders()[0]
    assert first[0][1].status == OrderIntentStatus.PARTIALLY_FILLED
    assert open_order.status == OrderIntentStatus.PARTIALLY_FILLED
    assert open_order.accumulated_fill_qty == 10

    second = book.check_tick(
        make_market(yes_ask=Decimal("0.43"), yes_ask_size=10),
        BASE_TIME + timedelta(seconds=2),
    )

    assert second[0][1].status == OrderIntentStatus.FILLED
    assert second[0][0].status == OrderIntentStatus.FILLED
    assert book.get_open_orders() == []


def test_restore_open_partially_filled_order() -> None:
    book = make_book()
    order = RestingOrder(
        order_id="order-1",
        intent=make_intent(qty=20),
        order_type="limit",
        created_at=BASE_TIME,
        max_resting_seconds=30,
        pair_id="pair-1",
        status=OrderIntentStatus.PARTIALLY_FILLED,
        accumulated_fill_qty=5,
    )

    book.restore_order(order)
    restored = book.get_open_orders()[0]

    assert restored.order_id == "order-1"
    assert restored.remaining_qty == 15
    assert restored.pair_id == "pair-1"


def test_cancel_pair_cancels_both_legs() -> None:
    book = make_book()
    book.add_order(make_intent(side="yes"), 30, BASE_TIME, pair_id="pair-1")
    book.add_order(make_intent(side="no"), 30, BASE_TIME, pair_id="pair-1")

    cancelled = book.cancel_pair("pair-1")

    assert len(cancelled) == 2
    assert {order.status for order in cancelled} == {OrderIntentStatus.CANCELLED}
    assert book.get_open_orders() == []


def test_determinism() -> None:
    def run_sequence():
        book = make_book()
        book.add_order(make_intent(qty=20), 30, BASE_TIME)
        first = book.check_tick(
            make_market(yes_ask=Decimal("0.44"), yes_ask_size=10),
            BASE_TIME + timedelta(seconds=1),
        )
        second = book.check_tick(
            make_market(yes_ask=Decimal("0.43"), yes_ask_size=10),
            BASE_TIME + timedelta(seconds=2),
        )
        return [
            (order.order_id, order.status.value, fill.fill_qty, fill.status.value)
            for order, fill in first + second
        ]

    assert run_sequence() == run_sequence()


def test_check_limit_crossed_yes_side() -> None:
    assert check_limit_crossed(
        make_intent(side="yes", price=Decimal("0.45")),
        make_market(yes_ask=Decimal("0.44")),
    )
    assert check_limit_crossed(
        make_intent(side="yes", price=Decimal("0.45")),
        make_market(yes_ask=Decimal("0.45")),
    )
    assert not check_limit_crossed(
        make_intent(side="yes", price=Decimal("0.45")),
        make_market(yes_ask=Decimal("0.46")),
    )


def test_check_limit_crossed_no_side() -> None:
    assert check_limit_crossed(
        make_intent(side="no", price=Decimal("0.55")),
        make_market(no_ask=Decimal("0.54")),
    )
    assert check_limit_crossed(
        make_intent(side="no", price=Decimal("0.55")),
        make_market(no_ask=Decimal("0.55")),
    )
    assert not check_limit_crossed(
        make_intent(side="no", price=Decimal("0.55")),
        make_market(no_ask=Decimal("0.56")),
    )
