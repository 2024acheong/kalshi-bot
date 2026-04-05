from datetime import datetime, timezone
from decimal import Decimal

from core.schemas import MarketStatus
from worker.cache import serialize_market_state
from worker.normalization import market_catalog_row, market_snapshot_row, normalize_market


def test_normalize_market_maps_kalshi_payload() -> None:
    raw_market = {
        "ticker": "KXBTC-26APR-B90000",
        "title": "BTC above 90k by Apr 26?",
        "category": "crypto",
        "close_time": "2026-04-26T23:59:59Z",
        "status": "open",
        "yes_bid": "44",
        "yes_ask": "46",
        "yes_bid_size": 120,
        "yes_ask_size": 80,
        "last_price": "45",
        "last_price_ts": "2026-04-05T13:00:00Z",
        "volume": 5000,
        "open_interest": 1500,
        "sequence": 18,
    }

    market = normalize_market(raw_market, source="rest_poll")

    assert market.ticker == "KXBTC-26APR-B90000"
    assert market.timestamp == datetime(2026, 4, 5, 13, 0, tzinfo=timezone.utc)
    assert market.yes_bid == Decimal("0.44")
    assert market.yes_ask == Decimal("0.46")
    assert market.status == MarketStatus.OPEN
    assert market.raw_sequence == 18

    catalog_row = market_catalog_row(raw_market, market)
    snapshot_row = market_snapshot_row(market)

    assert catalog_row["ticker"] == market.ticker
    assert catalog_row["status"] == "open"
    assert snapshot_row["last_price"] == Decimal("0.45")
    assert snapshot_row["source"] == "rest_poll"


def test_normalize_market_accepts_decimal_price_inputs() -> None:
    market = normalize_market(
        {
            "ticker": "KXBTC-26APR-B95000",
            "status": "open",
            "yes_bid": "0.45",
            "yes_ask": "0.47",
            "last_price": "0.46",
            "last_price_ts": "2026-04-05T13:30:00Z",
        },
        source="rest_poll",
    )

    assert market.yes_bid == Decimal("0.45")
    assert market.yes_ask == Decimal("0.47")
    assert market.last_price == Decimal("0.46")


def test_serialize_market_state_for_redis() -> None:
    market = normalize_market(
        {
            "ticker": "KXETH-26APR-B2000",
            "status": "open",
            "yes_bid": "51",
            "yes_ask": "52",
            "last_price_ts": "2026-04-05T14:00:00Z",
        },
        source="rest_poll",
    )

    serialized = serialize_market_state(market)

    assert '"ticker": "KXETH-26APR-B2000"' in serialized
    assert '"yes_bid": "0.51"' in serialized
    assert '"source": "rest_poll"' in serialized
