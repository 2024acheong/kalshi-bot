from __future__ import annotations

import pytest
import httpx

from worker.ticker_discovery import TickerDiscoveryConfig, discover_live_tickers


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class StubClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def list_markets(
        self,
        *,
        cursor: str | None = None,
        status: str = "open",
        limit: int = 200,
        series_ticker: str | None = None,
    ):
        self.calls.append(
            {
                "cursor": cursor,
                "status": status,
                "limit": limit,
                "series_ticker": series_ticker,
            }
        )
        if series_ticker:
            return (
                [
                    {
                        "ticker": f"{series_ticker}-26JAN-T1",
                        "status": "open",
                        "volume": 50,
                        "open_interest": 100,
                        "yes_bid": 45,
                        "yes_ask": 47,
                        "yes_bid_size": 10,
                        "yes_ask_size": 10,
                    }
                ],
                None,
            )
        if cursor is None:
            return (
                [
                    {
                        "ticker": "NO_ASK",
                        "status": "open",
                        "volume": 999,
                        "yes_bid": 45,
                        "yes_bid_size": 10,
                    },
                    {
                        "ticker": "CLOSED",
                        "status": "closed",
                        "volume": 999,
                        "yes_bid": 45,
                        "yes_ask": 47,
                        "yes_bid_size": 10,
                        "yes_ask_size": 10,
                    },
                    {
                        "ticker": "LOWVOL",
                        "status": "open",
                        "volume": 0,
                        "yes_bid": 45,
                        "yes_ask": 47,
                        "yes_bid_size": 10,
                        "yes_ask_size": 10,
                    },
                    {
                        "ticker": "SECOND",
                        "status": "open",
                        "volume": 100,
                        "open_interest": 50,
                        "yes_bid": 45,
                        "yes_ask": 47,
                        "yes_bid_size": 10,
                        "yes_ask_size": 10,
                    },
                ],
                "next",
            )
        return (
            [
                {
                    "ticker": "FIRST",
                    "status": "open",
                    "volume": 200,
                    "open_interest": 100,
                    "yes_bid": 48,
                    "yes_ask": 49,
                    "yes_bid_size": 10,
                    "yes_ask_size": 10,
                },
                {
                    "ticker": "THIRD",
                    "status": "open",
                    "volume": 50,
                    "open_interest": 0,
                    "yes_bid_dollars": "0.40",
                    "yes_ask_dollars": "0.42",
                    "yes_bid_size_fp": "5",
                    "yes_ask_size_fp": "5",
                },
            ],
            None,
        )


class RateLimitedSeriesClient:
    async def list_markets(
        self,
        *,
        cursor: str | None = None,
        status: str = "open",
        limit: int = 200,
        series_ticker: str | None = None,
    ):
        del cursor, status, limit
        if series_ticker == "RATE_LIMITED":
            request = httpx.Request("GET", "https://example.test/markets")
            response = httpx.Response(429, request=request)
            raise httpx.HTTPStatusError(
                "rate limited",
                request=request,
                response=response,
            )
        return (
            [
                {
                    "ticker": f"{series_ticker}-26JAN-T1",
                    "status": "open",
                    "volume": 50,
                    "yes_bid": 45,
                    "yes_ask": 47,
                    "yes_bid_size": 10,
                    "yes_ask_size": 10,
                }
            ],
            None,
        )


@pytest.mark.anyio
async def test_discover_live_tickers_filters_and_ranks_microstructure_markets() -> None:
    client = StubClient()

    tickers = await discover_live_tickers(
        client,
        TickerDiscoveryConfig(
            strategy_name="custom_strategy",
            limit=3,
            min_volume=1,
            max_pages=3,
            series_tickers=(),
        ),
    )

    assert tickers == ["FIRST", "SECOND", "THIRD"]
    assert [call["cursor"] for call in client.calls] == [None, "next"]
    assert {call["series_ticker"] for call in client.calls} == {None}


@pytest.mark.anyio
async def test_spread_capture_discovery_uses_preferred_liquid_series() -> None:
    client = StubClient()

    tickers = await discover_live_tickers(
        client,
        TickerDiscoveryConfig(
            strategy_name="spread_capture",
            limit=2,
            min_volume=0,
            series_tickers=("KXBTCD", "KXNASDAQ100"),
        ),
    )

    assert tickers == ["KXBTCD-26JAN-T1", "KXNASDAQ100-26JAN-T1"]
    assert [call["series_ticker"] for call in client.calls] == ["KXBTCD", "KXNASDAQ100"]


@pytest.mark.anyio
async def test_discover_live_tickers_uses_macro_series_for_macro_strategy() -> None:
    client = StubClient()

    tickers = await discover_live_tickers(
        client,
        TickerDiscoveryConfig(
            strategy_name="calibration_mispricing_macro",
            limit=2,
            series_tickers=("KXCPI", "KXGDP"),
        ),
    )

    assert tickers == ["KXCPI-26JAN-T1", "KXGDP-26JAN-T1"]
    assert [call["series_ticker"] for call in client.calls] == ["KXCPI", "KXGDP"]


@pytest.mark.anyio
async def test_discover_live_tickers_skips_rate_limited_series() -> None:
    tickers = await discover_live_tickers(
        RateLimitedSeriesClient(),
        TickerDiscoveryConfig(
            strategy_name="calibration_mispricing_weather",
            limit=3,
            series_tickers=("RATE_LIMITED", "KXHIGHNY"),
        ),
    )

    assert tickers == ["KXHIGHNY-26JAN-T1"]
