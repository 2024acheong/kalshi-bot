from decimal import Decimal

import pytest

from worker.kalshi.websocket import KalshiWebSocketClient
from worker.normalizer import normalize_ws_ticker_message


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class RecordingWebSocket:
    def __init__(self) -> None:
        self.sent_messages = []

    async def send(self, message: str) -> None:
        self.sent_messages.append(message)


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


def test_normalize_orderbook_snapshot_raw_sequence() -> None:
    market = normalize_ws_ticker_message(
        {
            "type": "orderbook_snapshot",
            "market_ticker": "KXBTC-26APR-B90000",
            "seq": 42,
            "msg": {
                "yes": [[45, 100]],
                "no": [[54, 80]],
            },
        }
    )

    assert market is not None
    assert market.raw_sequence == 42


@pytest.mark.anyio
async def test_websocket_subscribe_skips_empty_ticker_list() -> None:
    websocket = RecordingWebSocket()
    client = KalshiWebSocketClient(
        tickers=[],
        on_market_update=lambda message: None,
        on_disconnect=lambda: None,
        on_reconnect=lambda: None,
    )

    await client._subscribe(websocket)

    assert websocket.sent_messages == []
