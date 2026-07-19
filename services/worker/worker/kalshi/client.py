from __future__ import annotations

import logging
import os
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx


logger = logging.getLogger(__name__)


class KalshiRestClient:
    def __init__(self, http_client: httpx.AsyncClient, base_url: str) -> None:
        self._http_client = http_client
        self._base_url = base_url.rstrip("/")
        self._logger = logging.getLogger(__name__)

    async def get_markets(self, tickers: list[str]) -> list[dict[str, Any]]:
        if not tickers:
            return []
        response = await self._http_client.get(
            f"{self._base_url}/markets",
            params={
                "limit": len(tickers),
                "status": "open",
                "tickers": ",".join(tickers),
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                self._logger.warning(
                    "Kalshi returned 404 for watched tickers: %s",
                    ",".join(tickers),
                )
                return []
            raise
        payload = response.json()
        markets = payload.get("markets", [])
        if not isinstance(markets, list):
            raise ValueError("Kalshi markets payload is not a list")
        return markets

    async def list_markets(
        self,
        *,
        cursor: str | None = None,
        status: str = "open",
        limit: int = 200,
        series_ticker: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        params: dict[str, Any] = {
            "limit": limit,
            "status": status,
        }
        if cursor:
            params["cursor"] = cursor
        if series_ticker:
            params["series_ticker"] = series_ticker

        response = await self._http_client.get(
            f"{self._base_url}/markets",
            params=params,
        )
        response.raise_for_status()
        payload = response.json()
        markets = payload.get("markets", [])
        if not isinstance(markets, list):
            raise ValueError("Kalshi markets payload is not a list")
        next_cursor = payload.get("cursor") or payload.get("next_cursor")
        return markets, str(next_cursor) if next_cursor else None

    async def list_series(
        self,
        *,
        category: str | None = None,
        status: str | None = None,
        limit: int = 200,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        params: dict[str, Any] = {
            "limit": limit,
        }
        if category:
            params["category"] = category
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor

        response = await self._http_client.get(
            f"{self._base_url}/series",
            params=params,
        )
        response.raise_for_status()
        payload = response.json()
        series = (
            payload.get("series")
            or payload.get("market_series")
            or payload.get("data")
            or []
        )
        if isinstance(series, dict):
            if "ticker" in series:
                series = [series]
            else:
                raise ValueError(
                    "Kalshi series payload dict did not contain a ticker; "
                    f"payload keys={sorted(payload.keys())}"
                )
        if not isinstance(series, list):
            raise ValueError(
                f"Kalshi series payload is not a list; payload keys={sorted(payload.keys())}"
            )
        next_cursor = payload.get("cursor") or payload.get("next_cursor")
        return series, str(next_cursor) if next_cursor else None


async def fetch_top_liquid_tickers(
    limit: int = 5,
    min_volume: int = 1,
    max_pages: int = 10,
) -> list[str]:
    base_url = os.getenv("KALSHI_BASE_URL", "https://api.elections.kalshi.com/trade-api/v2")
    markets: list[dict[str, Any]] = []
    cursor: str | None = None
    async with httpx.AsyncClient(timeout=10.0) as http_client:
        client = KalshiRestClient(http_client=http_client, base_url=base_url)
        for _ in range(max_pages):
            page, cursor = await client.list_markets(
                status="open",
                limit=100,
                cursor=cursor,
            )
            markets.extend(page)
            if not cursor:
                break

    quote_filtered = [market for market in markets if _has_live_quote(market)]
    liquid_markets = [
        market
        for market in quote_filtered
        if _market_volume(market) >= min_volume
    ]
    logger.info(
        "Fetched %d open markets; %d have live quotes; %d meet min_volume=%d",
        len(markets),
        len(quote_filtered),
        len(liquid_markets),
        min_volume,
    )
    if not liquid_markets:
        logger.info("Sample raw Kalshi markets for ticker selection: %s", markets[:5])
    else:
        logger.debug("Sample raw Kalshi markets for ticker selection: %s", markets[:5])

    sorted_markets = sorted(
        liquid_markets,
        key=lambda market: _market_volume(market),
        reverse=True,
    )
    return [
        str(market["ticker"])
        for market in sorted_markets[:limit]
        if market.get("ticker") is not None
    ]


def _market_volume(market: dict[str, Any]) -> int:
    for field in (
        "volume",
        "volume_24h",
        "volume_fp",
        "volume_24h_fp",
        "volume_24h_contracts",
        "volume_contracts",
    ):
        parsed = _decimal_value(market.get(field))
        if parsed is not None:
            return int(parsed)
    return 0


def _has_live_quote(market: dict[str, Any]) -> bool:
    return _has_quote_with_size(
        market,
        ("yes_bid", "yes_bid_dollars"),
        ("yes_bid_size", "yes_bid_size_fp"),
    ) or _has_quote_with_size(
        market,
        ("yes_ask", "yes_ask_dollars"),
        ("yes_ask_size", "yes_ask_size_fp"),
    )


def _has_quote_with_size(
    market: dict[str, Any],
    quote_fields: tuple[str, ...],
    size_fields: tuple[str, ...],
) -> bool:
    quote = _first_positive_decimal(market, quote_fields)
    if quote is None:
        return False

    size_values = [
        _decimal_value(market.get(field))
        for field in size_fields
        if market.get(field) is not None
    ]
    if not size_values:
        return True
    return any(size is not None and size > 0 for size in size_values)


def _first_positive_decimal(
    market: dict[str, Any],
    fields: tuple[str, ...],
) -> Decimal | None:
    for field in fields:
        parsed = _decimal_value(market.get(field))
        if parsed is not None and parsed > 0:
            return parsed
    return None


def _decimal_value(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
