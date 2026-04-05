from datetime import datetime, timezone
from decimal import Decimal
from core.schemas import MarketState, MarketStatus


def test_market_state_instantiation():
    ms = MarketState(
        ticker="KXBTC-25APR-B90000",
        timestamp=datetime.now(timezone.utc),
        yes_bid=Decimal("0.45"),
        yes_ask=Decimal("0.47"),
        yes_bid_size=100,
        yes_ask_size=80,
        last_price=Decimal("0.46"),
        volume_24h=5000,
        open_interest=1200,
        close_time=None,
        status=MarketStatus.OPEN,
        source="rest_poll",
    )
    assert ms.ticker == "KXBTC-25APR-B90000"
    assert ms.yes_bid == Decimal("0.45")