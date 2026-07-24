from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "services" / "worker"))

from worker.kalshi.client import KalshiRestClient  # noqa: E402


async def main() -> None:
    base_url = os.getenv(
        "KALSHI_BASE_URL",
        "https://api.elections.kalshi.com/trade-api/v2",
    )
    ticker = os.getenv("KALSHI_MARKET_TICKER")
    async with httpx.AsyncClient(timeout=10.0) as http_client:
        client = KalshiRestClient(http_client=http_client, base_url=base_url)
        if ticker:
            markets = await client.get_markets([ticker])
        else:
            markets, _ = await client.list_markets(status="open", limit=1)

    if not markets:
        raise SystemExit("No market returned by Kalshi")
    print(json.dumps(markets[0], indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
