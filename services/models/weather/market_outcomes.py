from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from services.models.shared.model_registry import get_supabase_client
from services.models.weather.estimator import WeatherEnsembleEstimator

LOGGER = logging.getLogger(__name__)
SUPPORTED_MARKET_FILTER = "ticker.ilike.KXHIGH%,ticker.ilike.KXLOW%"
CITY_ALIASES = {
    "NYC": "NY",
}


def _parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value))


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


def _canonical_city_code(city_code: Any) -> str:
    normalized = str(city_code).upper()
    return CITY_ALIASES.get(normalized, normalized)


def parse_weather_market_ticker(ticker: str) -> dict[str, Any] | None:
    parser = WeatherEnsembleEstimator.__new__(WeatherEnsembleEstimator)
    return parser._parse_ticker(ticker)


def _resolve_strike_type(parsed: dict[str, Any], title: str) -> str | None:
    strike_type = str(parsed["strike_type"])
    if strike_type in {"between", "less"}:
        return strike_type
    if ">" in title:
        return "greater"
    if "<" in title:
        return "less"
    return None


def _label_weather_outcome(
    strike_type: str,
    actual_value_f: float,
    threshold_f: float,
    lower_f: float | None,
    upper_f: float | None,
) -> bool | None:
    if strike_type == "greater":
        return actual_value_f > threshold_f
    if strike_type == "less":
        return actual_value_f < threshold_f
    if strike_type == "between":
        if lower_f is None or upper_f is None:
            return None
        return lower_f <= actual_value_f <= upper_f
    return None


def build_weather_market_outcome_row(
    market: dict[str, Any],
    temperature_outcome: dict[str, Any],
) -> dict[str, Any] | None:
    ticker = str(market.get("ticker") or "")
    parsed = parse_weather_market_ticker(ticker)
    if parsed is None:
        return None

    market_city = _canonical_city_code(parsed["city_code"])
    outcome_city = _canonical_city_code(temperature_outcome.get("city_code"))
    if market_city != outcome_city:
        return None
    if parsed["target_date"] != _parse_date(temperature_outcome.get("outcome_date")):
        return None

    temp_key = "high_temp_f" if str(parsed["kind"]).upper() == "HIGH" else "low_temp_f"
    actual_value = temperature_outcome.get(temp_key)
    if actual_value is None:
        return None

    strike_type = _resolve_strike_type(parsed, str(market.get("title") or ""))
    if strike_type is None:
        LOGGER.info("Cannot resolve weather strike direction for %s", ticker)
        return None

    lower_f = parsed.get("lower_f")
    upper_f = parsed.get("upper_f")
    threshold_f = float(parsed["threshold_f"])
    if strike_type == "between" and lower_f is not None and upper_f is not None:
        threshold_f = (float(lower_f) + float(upper_f)) / 2.0

    yes_resolved = _label_weather_outcome(
        strike_type,
        float(actual_value),
        threshold_f,
        float(lower_f) if lower_f is not None else None,
        float(upper_f) if upper_f is not None else None,
    )
    if yes_resolved is None:
        return None

    resolved_at = _parse_datetime(market.get("resolved_at")) or _parse_datetime(
        market.get("close_time")
    )
    return {
        "ticker": ticker,
        "series": str(parsed["series"]),
        "kind": str(parsed["kind"]).upper(),
        "city_code": market_city,
        "target_date": parsed["target_date"].isoformat(),
        "strike_type": strike_type,
        "threshold_f": round(threshold_f, 2),
        "lower_f": round(float(lower_f), 2) if lower_f is not None else None,
        "upper_f": round(float(upper_f), 2) if upper_f is not None else None,
        "actual_value_f": round(float(actual_value), 2),
        "yes_resolved": bool(yes_resolved),
        "resolved_at": resolved_at.isoformat() if resolved_at is not None else None,
        "source": "kalshi_market_catalog_actual_temperature_outcomes",
    }


def store_weather_market_outcome(outcome: dict[str, Any]) -> int:
    response = (
        get_supabase_client()
        .table("weather_market_outcomes")
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
        .in_("status", ["closed", "resolved", "settled"])
        .execute()
    )
    return getattr(response, "data", None) or []


def _fetch_temperature_outcomes() -> list[dict[str, Any]]:
    response = (
        get_supabase_client()
        .table("actual_temperature_outcomes")
        .select("city_code,outcome_date,high_temp_f,low_temp_f")
        .execute()
    )
    return getattr(response, "data", None) or []


def collect_weather_market_outcomes(markets: list[dict[str, Any]] | None = None) -> int:
    """
    Build and upsert weather market YES/NO labels from settled markets.

    This expects actual_temperature_outcomes to already contain the station
    high/low for each market city/date.
    """
    if markets is None:
        markets = _fetch_candidate_markets()

    outcomes_by_city_date = {
        (_canonical_city_code(row.get("city_code")), _parse_date(row.get("outcome_date"))): row
        for row in _fetch_temperature_outcomes()
    }
    stored = 0
    for market in markets:
        parsed = parse_weather_market_ticker(str(market.get("ticker") or ""))
        if parsed is None:
            continue
        temperature_outcome = outcomes_by_city_date.get(
            (_canonical_city_code(parsed["city_code"]), parsed["target_date"])
        )
        if temperature_outcome is None:
            continue
        outcome = build_weather_market_outcome_row(market, temperature_outcome)
        if outcome is None:
            continue
        stored += store_weather_market_outcome(outcome)

    LOGGER.info("Stored %s weather market outcomes", stored)
    return stored


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(collect_weather_market_outcomes())
