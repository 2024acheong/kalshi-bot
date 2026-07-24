from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

from core.schemas.market import MarketState, MarketStatus

load_dotenv()


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY are required")
    return create_client(url, key)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError(f"Unsupported datetime value: {value!r}")


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _parse_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _row_to_market_state(row: dict[str, Any], close_time: datetime | None = None) -> MarketState:
    return MarketState(
        ticker=str(row["ticker"]),
        timestamp=_parse_datetime(row["timestamp"]),
        yes_bid=_parse_decimal(row.get("yes_bid")),
        yes_ask=_parse_decimal(row.get("yes_ask")),
        yes_bid_size=_parse_int(row.get("yes_bid_size")),
        yes_ask_size=_parse_int(row.get("yes_ask_size")),
        last_price=_parse_decimal(row.get("last_price")),
        volume_24h=_parse_int(row.get("volume_24h")),
        open_interest=_parse_int(row.get("open_interest")),
        close_time=close_time,
        status=MarketStatus(row.get("status", MarketStatus.OPEN.value)),
        source=str(row.get("source", "historical_snapshot")),
        raw_sequence=_parse_int(row.get("raw_sequence")),
    )


def get_close_times(tickers: list[str]) -> dict[str, datetime | None]:
    """
    Fetch close_time per ticker from market_catalog for replay-time feature computation.
    """
    if not tickers:
        return {}

    response = (
        get_supabase_client()
        .table("market_catalog")
        .select("ticker,close_time")
        .in_("ticker", tickers)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    close_times = {ticker: None for ticker in tickers}
    for row in rows:
        close_times[str(row["ticker"])] = _parse_datetime(row.get("close_time"))
    return close_times


def load_snapshots(
    tickers: list[str],
    date_from: datetime,
    date_to: datetime,
) -> dict[str, list[MarketState]]:
    """
    Query market_snapshots for tickers/date range.

    Returns ticker -> snapshots sorted OLDEST FIRST. This is intentionally the
    opposite of the live worker's newest-first history convention; the backtester
    converts to newest-first when computing features.
    """
    if not tickers:
        return {}

    close_times = get_close_times(tickers)
    response = (
        get_supabase_client()
        .table("market_snapshots")
        .select(
            "ticker,timestamp,yes_bid,yes_ask,yes_bid_size,yes_ask_size,"
            "last_price,volume_24h,open_interest,source,raw_sequence"
        )
        .in_("ticker", tickers)
        .gte("timestamp", date_from.isoformat())
        .lte("timestamp", date_to.isoformat())
        .order("timestamp", desc=False)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    snapshots = {ticker: [] for ticker in tickers}
    for row in rows:
        ticker = str(row["ticker"])
        snapshots.setdefault(ticker, []).append(
            _row_to_market_state(row, close_time=close_times.get(ticker))
        )

    for ticker in snapshots:
        snapshots[ticker].sort(key=lambda market: market.timestamp)
    return snapshots
