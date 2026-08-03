from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.execution.adapters import PaperAdapter, SimulationConfig
from core.execution.fees import compute_kalshi_fee
from core.risk.engine import OrderIntent
from core.schemas.market import MarketState, MarketStatus, OrderIntentStatus


def make_market(**kwargs) -> MarketState:
    now = datetime.now(timezone.utc)
    defaults = {
        "ticker": "KXTEST-26JAN-YES",
        "timestamp": now,
        "yes_bid": Decimal("0.47"),
        "yes_ask": Decimal("0.48"),
        "yes_bid_size": 100,
        "yes_ask_size": 100,
        "last_price": Decimal("0.48"),
        "volume_24h": 1000,
        "open_interest": 5000,
        "close_time": now + timedelta(hours=24),
        "status": MarketStatus.OPEN,
        "source": "rest_poll",
        "no_bid": Decimal("0.52"),
        "no_ask": Decimal("0.53"),
        "no_bid_size": 100,
        "no_ask_size": 100,
    }
    defaults.update(kwargs)
    return MarketState(**defaults)


def make_intent(**kwargs) -> OrderIntent:
    defaults = {
        "ticker": "KXTEST-26JAN-YES",
        "side": "yes",
        "price": Decimal("0.50"),
        "qty": 100,
        "estimated_edge": 0.08,
        "model_prob": 0.58,
        "run_id": "run-1",
        "signal_id": "signal-1",
    }
    defaults.update(kwargs)
    return OrderIntent(**defaults)


def test_fee_calculation_basic():
    fee = compute_kalshi_fee(Decimal("0.50"), 100)

    assert fee == Decimal("1.75")


def test_fee_calculation_extreme_price():
    low_fee = compute_kalshi_fee(Decimal("0.00"), 100)
    high_fee = compute_kalshi_fee(Decimal("1.00"), 100)

    assert low_fee == Decimal("0.00")
    assert high_fee == Decimal("0.00")


def test_fee_never_negative():
    fee = compute_kalshi_fee(Decimal("1.10"), 100)

    assert fee == Decimal("0")


def test_limit_order_fills_when_price_crossed():
    result = PaperAdapter().submit_order(
        "order-1",
        make_intent(price=Decimal("0.50")),
        "limit",
        make_market(yes_ask=Decimal("0.48")),
    )

    assert result.status == OrderIntentStatus.FILLED
    assert result.fill_price == Decimal("0.48")
    assert result.fill_qty == 100


def test_limit_order_does_not_fill_on_touch():
    result = PaperAdapter().submit_order(
        "order-1",
        make_intent(price=Decimal("0.48")),
        "limit",
        make_market(yes_ask=Decimal("0.48")),
    )

    assert result.status == OrderIntentStatus.CANCELLED
    assert result.fill_qty == 0


def test_limit_order_does_not_fill_when_price_not_crossed():
    result = PaperAdapter().submit_order(
        "order-1",
        make_intent(price=Decimal("0.45")),
        "limit",
        make_market(yes_ask=Decimal("0.48")),
    )

    assert result.status == OrderIntentStatus.CANCELLED
    assert result.fill_qty == 0


def test_market_order_always_fills_with_slippage():
    result = PaperAdapter().submit_order(
        "order-1",
        make_intent(price=Decimal("0.50")),
        "market",
        make_market(yes_ask=Decimal("0.48")),
    )

    assert result.status == OrderIntentStatus.FILLED
    assert result.fill_price == Decimal("0.49")
    assert result.fill_qty == 100


def test_partial_fill_when_book_thin():
    result = PaperAdapter().submit_order(
        "order-1",
        make_intent(qty=100),
        "limit",
        make_market(yes_ask=Decimal("0.48"), yes_ask_size=40),
    )

    assert result.status == OrderIntentStatus.PARTIALLY_FILLED
    assert result.fill_qty == 40


def test_full_fill_when_book_deep():
    result = PaperAdapter().submit_order(
        "order-1",
        make_intent(qty=100),
        "limit",
        make_market(yes_ask=Decimal("0.48"), yes_ask_size=150),
    )

    assert result.status == OrderIntentStatus.FILLED
    assert result.fill_qty == 100


def test_stale_quote_rejected():
    config = SimulationConfig(staleness_threshold_ms=5000)
    stale_market = make_market(timestamp=datetime.now(timezone.utc) - timedelta(seconds=10))

    result = PaperAdapter(config).submit_order(
        "order-1",
        make_intent(),
        "limit",
        stale_market,
    )

    assert result.status == OrderIntentStatus.CANCELLED
    assert result.fill_qty == 0


def test_zero_fill_qty_has_zero_fee():
    result = PaperAdapter().submit_order(
        "order-1",
        make_intent(qty=100),
        "limit",
        make_market(yes_ask=Decimal("0.48"), yes_ask_size=0),
    )

    assert result.status == OrderIntentStatus.CANCELLED
    assert result.fill_qty == 0
    assert result.fee == Decimal("0")


def test_no_side_limit_order_logic():
    result = PaperAdapter().submit_order(
        "order-1",
        make_intent(side="no", price=Decimal("0.55")),
        "limit",
        make_market(no_ask=Decimal("0.53")),
    )

    assert result.status == OrderIntentStatus.FILLED
    assert result.fill_price == Decimal("0.53")
    assert result.fill_qty == 100


def test_no_side_limit_order_does_not_fill_on_touch():
    result = PaperAdapter().submit_order(
        "order-1",
        make_intent(side="no", price=Decimal("0.53")),
        "limit",
        make_market(no_ask=Decimal("0.53")),
    )

    assert result.status == OrderIntentStatus.CANCELLED
    assert result.fill_qty == 0


def test_no_side_market_order_uses_no_ask_with_slippage():
    result = PaperAdapter().submit_order(
        "order-1",
        make_intent(side="no", price=Decimal("0.53")),
        "market",
        make_market(no_ask=Decimal("0.53")),
    )

    assert result.status == OrderIntentStatus.FILLED
    assert result.fill_price == Decimal("0.54")
    assert result.fill_qty == 100


def test_no_side_fill_uses_no_ask_size():
    result = PaperAdapter().submit_order(
        "order-1",
        make_intent(side="no", price=Decimal("0.55"), qty=100),
        "limit",
        make_market(no_ask=Decimal("0.53"), no_ask_size=25, yes_bid_size=100),
    )

    assert result.status == OrderIntentStatus.PARTIALLY_FILLED
    assert result.fill_qty == 25


def test_configurable_fee_per_contract():
    result = PaperAdapter(
        SimulationConfig(
            fee_per_contract=Decimal("0.02"),
            staleness_threshold_ms=5000,
        )
    ).submit_order(
        "order-1",
        make_intent(qty=10),
        "limit",
        make_market(yes_ask=Decimal("0.48")),
    )

    assert result.fee == Decimal("0.05")
