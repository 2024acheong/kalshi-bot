import asyncio
import os
from dotenv import load_dotenv
from supabase import create_client
import httpx

load_dotenv()

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"]
)

async def fetch_and_store_market():
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.elections.kalshi.com/trade-api/v2/markets",
            params={"limit": 1, "status": "open"},
        )
        resp.raise_for_status()
        data = resp.json()

    market = data["markets"][0]
    ticker = market["ticker"]

    # Write to market_catalog
    supabase.table("market_catalog").upsert({
        "ticker": ticker,
        "title": market["title"],
        "category": market.get("category"),
        "close_time": market.get("close_time"),
        "status": market.get("status", "open"),
    }).execute()

    # Write a snapshot
    supabase.table("market_snapshots").insert({
        "ticker": ticker,
        "timestamp": market.get("last_price_ts") or "now()",
        "yes_bid": market.get("yes_bid"),
        "yes_ask": market.get("yes_ask"),
        "yes_bid_size": market.get("yes_bid_size"),
        "yes_ask_size": market.get("yes_ask_size"),
        "last_price": market.get("last_price"),
        "volume_24h": market.get("volume"),
        "open_interest": market.get("open_interest"),
        "source": "rest_poll",
    }).execute()

    print(f"✓ Wrote market: {ticker}")
    print(f"  Title: {market['title']}")
    print(f"  Yes bid: {market.get('yes_bid')} | Yes ask: {market.get('yes_ask')}")
    return ticker

if __name__ == "__main__":
    asyncio.run(fetch_and_store_market())