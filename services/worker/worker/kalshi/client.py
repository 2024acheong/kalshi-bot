from __future__ import annotations

import logging
from typing import Any

import httpx


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
                    f"Kalshi series payload dict did not contain a ticker; payload keys={sorted(payload.keys())}"
                )
        if not isinstance(series, list):
            raise ValueError(
                f"Kalshi series payload is not a list; payload keys={sorted(payload.keys())}"
            )
        next_cursor = payload.get("cursor") or payload.get("next_cursor")
        return series, str(next_cursor) if next_cursor else None
