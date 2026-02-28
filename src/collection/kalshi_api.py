import os
import requests
from dotenv import load_dotenv

load_dotenv()

BASE = "https://api.kalshi.com/trade-api/v2"
API_KEY = os.getenv("KALSHI_API_KEY")

HEADERS = {
    "Authorization": f"Bearer {API_KEY}"
}


def get_orderbook(market_id: str):
    url = f"{BASE}/markets/{market_id}/orderbook"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()