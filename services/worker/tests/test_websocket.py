from decimal import Decimal

from worker.normalizer import normalize_ws_ticker_message


def test_normalize_ws_ticker() -> None:
    market = normalize_ws_ticker_message(
        {
            "type": "ticker",
            "market_ticker": "KXBTC-26APR-B90000",
            "seq": 42,
            "msg": {
                "yes_bid": 45,
                "yes_ask": 47,
                "yes_bid_size": 100,
                "yes_ask_size": 80,
                "last_price": 46,
                "volume": 5000,
            },
        }
    )

    assert market is not None
    assert market.ticker == "KXBTC-26APR-B90000"
    assert market.source == "websocket"
    assert market.raw_sequence == 42


def test_normalize_ws_missing_msg() -> None:
    assert (
        normalize_ws_ticker_message(
            {
                "type": "ticker",
                "market_ticker": "KXBTC-26APR-B90000",
                "seq": 42,
            }
        )
        is None
    )


def test_normalize_ws_price_conversion() -> None:
    market = normalize_ws_ticker_message(
        {
            "type": "ticker",
            "market_ticker": "KXBTC-26APR-B90000",
            "seq": 42,
            "msg": {
                "yes_bid": 45,
            },
        }
    )

    assert market is not None
    assert market.yes_bid == Decimal("0.45")
