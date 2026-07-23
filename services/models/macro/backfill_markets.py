from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from services.models.shared.model_registry import get_supabase_client

LOGGER = logging.getLogger(__name__)
KALSHI_MARKETS_URL = "https://api.elections.kalshi.com/trade-api/v2/markets"
MACRO_SERIES_TICKERS = ["KXCPI", "KXCPIYOY", "KXGDP", "KXPAYROLLS", "KXU3", "KXFED"]
DEFAULT_STATUSES = ["open", "closed", "settled"]


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _catalog_status(raw_status: Any, result: Any = None) -> str:
    normalized = str(raw_status or "").strip().lower()
    if normalized in {"active", "open", "initialized"}:
        return "open"
    if normalized in {"closed", "close"}:
        return "closed"
    if normalized in {"settled", "resolved", "finalized", "final"}:
        return "resolved"
    if result not in (None, ""):
        return "resolved"
    return normalized or "open"


def catalog_row_from_kalshi_market(market: dict[str, Any]) -> dict[str, Any]:
    ticker = str(market["ticker"])
    synced_at = _parse_datetime(market.get("updated_time")) or datetime.now(timezone.utc)
    return {
        "ticker": ticker,
        "title": market.get("title") or ticker,
        "category": market.get("category") or "economics",
        "close_time": (
            _parse_datetime(market.get("close_time")).isoformat()
            if _parse_datetime(market.get("close_time")) is not None
            else None
        ),
        "status": _catalog_status(market.get("status"), market.get("result")),
        "synced_at": synced_at.isoformat(),
    }


def fetch_kalshi_markets(
    series_ticker: str,
    status: str,
    *,
    limit: int = 200,
    max_retries: int = 3,
) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    cursor: str | None = None
    with httpx.Client(timeout=30.0) as client:
        while True:
            params = {"series_ticker": series_ticker, "status": status, "limit": limit}
            if cursor:
                params["cursor"] = cursor
            for attempt in range(max_retries + 1):
                response = client.get(KALSHI_MARKETS_URL, params=params)
                if response.status_code != 429 or attempt >= max_retries:
                    break
                retry_after = response.headers.get("retry-after")
                sleep_seconds = float(retry_after) if retry_after else 2.0 * (attempt + 1)
                LOGGER.info(
                    "Rate limited by Kalshi for %s status=%s; sleeping %.1fs",
                    series_ticker,
                    status,
                    sleep_seconds,
                )
                time.sleep(sleep_seconds)
            response.raise_for_status()
            payload = response.json()
            page_markets = payload.get("markets") or []
            markets.extend(page_markets)
            cursor = payload.get("cursor") or None
            if not cursor:
                break
    return markets


def upsert_market_catalog_rows(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    client = get_supabase_client()
    upserted = 0
    for start in range(0, len(rows), 500):
        batch = rows[start : start + 500]
        response = client.table("market_catalog").upsert(batch, on_conflict="ticker").execute()
        upserted += len(getattr(response, "data", None) or batch)
    return upserted


def backfill_macro_markets(
    series_tickers: list[str] | None = None,
    statuses: list[str] | None = None,
) -> int:
    series_tickers = series_tickers or MACRO_SERIES_TICKERS
    statuses = statuses or DEFAULT_STATUSES

    rows_by_ticker: dict[str, dict[str, Any]] = {}
    for series_ticker in series_tickers:
        for status in statuses:
            try:
                markets = fetch_kalshi_markets(series_ticker, status)
            except Exception as exc:
                LOGGER.warning(
                    "Skipping %s status=%s after Kalshi API error: %s",
                    series_ticker,
                    status,
                    exc,
                )
                continue
            LOGGER.info(
                "Fetched %s Kalshi markets for %s status=%s",
                len(markets),
                series_ticker,
                status,
            )
            for market in markets:
                row = catalog_row_from_kalshi_market(market)
                rows_by_ticker[row["ticker"]] = row

    stored = upsert_market_catalog_rows(list(rows_by_ticker.values()))
    LOGGER.info("Upserted %s macro market_catalog rows", stored)
    return stored


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill Kalshi macro markets into market_catalog."
    )
    parser.add_argument(
        "--series",
        action="append",
        choices=MACRO_SERIES_TICKERS,
        help="Series ticker to backfill. Repeatable. Defaults to all supported macro series.",
    )
    parser.add_argument(
        "--status",
        action="append",
        help="Kalshi API status to request. Repeatable. Defaults to open, closed, settled.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    print(backfill_macro_markets(series_tickers=args.series, statuses=args.status))


if __name__ == "__main__":
    main()
