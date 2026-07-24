from __future__ import annotations

import httpx
import pytest

from worker.kalshi.client import KalshiRestClient, fetch_top_liquid_tickers


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_fetch_top_liquid_tickers_filters_and_sorts(monkeypatch) -> None:
    calls = []

    async def list_markets(
        self,
        *,
        cursor=None,
        status="open",
        limit=200,
        series_ticker=None,
    ):
        del self, status, limit, series_ticker
        calls.append(cursor)
        if cursor is None:
            return (
                [
                    {"ticker": "EMPTY", "volume": 10000, "yes_bid": None, "yes_ask": None},
                    {
                        "ticker": "EMPTY_ZERO",
                        "volume": 9000,
                        "yes_bid": 0,
                        "yes_ask": 0,
                    },
                    {
                        "ticker": "ZERO_SIZE",
                        "volume": 8000,
                        "yes_bid_dollars": "0.5000",
                        "yes_bid_size_fp": "0.00",
                    },
                    {"ticker": "LOWVOL", "volume": 0, "yes_bid": 45, "yes_ask": 47},
                ],
                "next-page",
            )
        return (
            [
                {"ticker": "SECOND", "volume": 300, "yes_bid": None, "yes_ask": 52},
                {"ticker": "FIRST", "volume": 500, "yes_bid": 48, "yes_ask": None},
                {
                    "ticker": "THIRD",
                    "volume_fp": "200.00",
                    "yes_bid_dollars": "0.4000",
                    "yes_ask_dollars": "0.4200",
                },
                {
                    "ticker": "FOURTH",
                    "volume_24h_fp": "25.00",
                    "yes_bid_dollars": "0.0100",
                    "yes_ask_dollars": "0.9900",
                },
            ],
            None,
        )

    monkeypatch.setattr(KalshiRestClient, "list_markets", list_markets)

    tickers = await fetch_top_liquid_tickers(limit=4)

    assert tickers == ["FIRST", "SECOND", "THIRD", "FOURTH"]
    assert calls == [None, "next-page"]


@pytest.mark.anyio
async def test_list_markets_retries_rate_limit(monkeypatch) -> None:
    request = httpx.Request("GET", "https://example.test/markets")
    responses = [
        httpx.Response(429, headers={"Retry-After": "0"}, request=request),
        httpx.Response(
            200,
            json={"markets": [{"ticker": "KXTEST"}], "cursor": None},
            request=request,
        ),
    ]
    sleeps = []

    class FakeHttpClient:
        async def get(self, url, params=None):
            del url, params
            return responses.pop(0)

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("worker.kalshi.client.asyncio.sleep", fake_sleep)
    client = KalshiRestClient(FakeHttpClient(), "https://example.test")

    markets, cursor = await client.list_markets()

    assert markets == [{"ticker": "KXTEST"}]
    assert cursor is None
    assert sleeps == [0.0]
