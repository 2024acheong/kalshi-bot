from __future__ import annotations

from typing import Any

import httpx
import logging


class KalshiRestClient:
    def __init__(self, http_client: httpx.AsyncClient, base_url: str) -> None:
        self._http_client = http_client
        self._base_url = base_url.rstrip("/")
        self._logger = logging.getLogger(__name__)

    async def get_markets(self, tickers: list[str]) -> list[dict[str, Any]]:
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
