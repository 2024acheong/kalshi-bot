from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv

try:
    from supabase import create_client
except ImportError:  # pragma: no cover
    create_client = None  # type: ignore[assignment]

load_dotenv()


@lru_cache(maxsize=1)
def get_supabase_client() -> Any:
    if create_client is None:
        raise RuntimeError("supabase>=2.4 is required to inspect tickers")
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SECRET_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY or SUPABASE_SECRET_KEY are required"
        )
    return create_client(url, key)


def main() -> None:
    client = get_supabase_client()
    patterns = [
        "category.ilike.%econom%",
        "category.ilike.%macro%",
        "title.ilike.%econom%",
        "title.ilike.%CPI%",
        "title.ilike.%GDP%",
        "title.ilike.%unemployment%",
        "title.ilike.%Federal Reserve%",
        "ticker.ilike.KXCPI%",
        "ticker.ilike.KXGDP%",
        "ticker.ilike.KXU3%",
        "ticker.ilike.KXPAYROLLS%",
        "ticker.ilike.KXFED%",
        "ticker.ilike.KXFOMC%",
    ]
    response = (
        client.table("market_catalog")
        .select("ticker,title,category")
        .or_(",".join(patterns))
        .limit(20)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    if not rows:
        print(
            "No macro-like rows found in market_catalog. Estimator parsing must use "
            "the documented/public Kalshi economics ticker convention as an unverified fallback."
        )
        return

    for row in rows:
        print(f"{row.get('ticker')} | {row.get('category')} | {row.get('title')}")


if __name__ == "__main__":
    main()
