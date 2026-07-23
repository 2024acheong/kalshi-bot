from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from services.models.macro.estimator import MacroEstimator
from services.models.macro.features import (
    clean_observations,
    derive_metric_observations,
    find_metric_value_for_target,
    normalize_threshold_for_metric,
    parse_date,
)
from services.models.shared.model_registry import get_supabase_client

LOGGER = logging.getLogger(__name__)
SUPPORTED_MARKET_FILTER = (
    "ticker.ilike.KXCPI%,"
    "ticker.ilike.KXGDP%,"
    "ticker.ilike.KXPAYROLLS%,"
    "ticker.ilike.KXU3%,"
    "ticker.ilike.KXFED%"
)


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


def parse_macro_market_ticker(ticker: str) -> dict[str, Any] | None:
    parser = MacroEstimator.__new__(MacroEstimator)
    return parser._parse_ticker(ticker)


def _fetch_indicator_rows(fred_series_id: str) -> list[dict[str, Any]]:
    response = (
        get_supabase_client()
        .table("macro_indicator_series")
        .select("observation_date,value")
        .eq("series_id", fred_series_id)
        .order("observation_date", desc=False)
        .limit(5000)
        .execute()
    )
    return getattr(response, "data", None) or []


def build_outcome_row(
    market: dict[str, Any],
    indicator_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    ticker = str(market.get("ticker") or "")
    parsed = parse_macro_market_ticker(ticker)
    if parsed is None:
        return None

    raw_observations = clean_observations(indicator_rows)
    metric_observations = derive_metric_observations(raw_observations, str(parsed["metric_id"]))
    target_date = parsed["target_date"]
    resolved_at = _parse_datetime(market.get("close_time")) or _parse_datetime(
        market.get("resolved_at")
    )
    if parsed["metric_id"] == "fed_upper_bound" and resolved_at is not None:
        target_date = resolved_at.date()

    actual = find_metric_value_for_target(
        metric_observations,
        target_date,
        str(parsed["metric_id"]),
    )
    if actual is None:
        LOGGER.info("No macro actual value available yet for %s", ticker)
        return None

    actual_date, actual_value = actual
    threshold = normalize_threshold_for_metric(
        str(parsed["metric_id"]),
        float(parsed.get("raw_threshold", parsed["threshold"])),
    )
    return {
        "ticker": ticker,
        "series": str(parsed["series"]),
        "metric_id": str(parsed["metric_id"]),
        "fred_series_id": str(parsed["fred_series_id"]),
        "target_date": actual_date.isoformat(),
        "threshold": round(threshold, 4),
        "actual_value": round(float(actual_value), 4),
        "yes_resolved": bool(float(actual_value) > threshold),
        "resolved_at": resolved_at.isoformat() if resolved_at is not None else None,
        "source": "kalshi_market_catalog",
    }


def store_macro_market_outcome(outcome: dict[str, Any]) -> int:
    response = (
        get_supabase_client()
        .table("macro_market_outcomes")
        .upsert(outcome, on_conflict="ticker")
        .execute()
    )
    return len(getattr(response, "data", None) or [outcome])


def _fetch_candidate_markets() -> list[dict[str, Any]]:
    response = (
        get_supabase_client()
        .table("market_catalog")
        .select("ticker,title,close_time,status")
        .or_(SUPPORTED_MARKET_FILTER)
        .in_("status", ["closed", "resolved"])
        .execute()
    )
    return getattr(response, "data", None) or []


def collect_macro_market_outcomes(markets: list[dict[str, Any]] | None = None) -> int:
    """
    Build and upsert real macro outcome labels from resolved market rows.
    """
    if markets is None:
        markets = _fetch_candidate_markets()
    parser = MacroEstimator.__new__(MacroEstimator)
    rows_by_series: dict[str, list[dict[str, Any]]] = {}
    stored = 0
    for market in markets:
        parsed = parser._parse_ticker(str(market.get("ticker") or ""))
        if parsed is None:
            continue
        fred_series_id = str(parsed["fred_series_id"])
        if fred_series_id not in rows_by_series:
            rows_by_series[fred_series_id] = _fetch_indicator_rows(fred_series_id)
        outcome = build_outcome_row(market, rows_by_series[fred_series_id])
        if outcome is None:
            continue
        stored += store_macro_market_outcome(outcome)
    LOGGER.info("Stored %s macro market outcomes", stored)
    return stored


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(collect_macro_market_outcomes())
