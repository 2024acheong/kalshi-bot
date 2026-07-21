from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from services.models.shared.model_registry import get_supabase_client

LOGGER = logging.getLogger(__name__)
NWS_API_BASE = "https://api.weather.gov"
NWS_USER_AGENT = "kalshi-bot-weather-outcomes/0.1"


@dataclass(frozen=True)
class WeatherOutcome:
    city_code: str
    station_id: str
    outcome_date: date
    high_temp_f: float | None
    low_temp_f: float | None
    source_product_id: str | None
    raw_text: str


# This is intentionally small and explicit. Kalshi settlement stations should
# be verified per city before adding production coverage.
NWS_CLI_SOURCES = {
    "NY": {"station_id": "KNYC", "nws_location_id": "NYC", "cli_product_code": "CLINYC"},
    "NYC": {"station_id": "KNYC", "nws_location_id": "NYC", "cli_product_code": "CLINYC"},
}

_SUMMARY_DATE_RE = re.compile(
    r"CLIMATE SUMMARY FOR (?P<month>[A-Z]+) (?P<day>\d{1,2}) (?P<year>\d{4})",
    re.IGNORECASE,
)
_MAX_RE = re.compile(r"^\s+MAXIMUM\s+(?P<value>-?\d+|MM)\b", re.IGNORECASE | re.MULTILINE)
_MIN_RE = re.compile(r"^\s+MINIMUM\s+(?P<value>-?\d+|MM)\b", re.IGNORECASE | re.MULTILINE)
_MONTHS = {
    "JANUARY": 1,
    "FEBRUARY": 2,
    "MARCH": 3,
    "APRIL": 4,
    "MAY": 5,
    "JUNE": 6,
    "JULY": 7,
    "AUGUST": 8,
    "SEPTEMBER": 9,
    "OCTOBER": 10,
    "NOVEMBER": 11,
    "DECEMBER": 12,
}


def _parse_cli_temperature(match: re.Match[str] | None) -> float | None:
    if match is None:
        return None
    value = match.group("value")
    if value.upper() == "MM":
        return None
    return float(value)


def parse_nws_cli_outcome(
    raw_text: str,
    city_code: str,
    station_id: str,
    source_product_id: str | None = None,
) -> WeatherOutcome | None:
    """
    Parse the NWS Daily Climate Report (CLI) max/min temperatures.

    CLI products are text bulletins and can vary by office. This parser targets
    the common "TEMPERATURE (F)" section with MAXIMUM/MINIMUM rows and returns
    None instead of guessing when the summary date or both temps are missing.
    """
    date_match = _SUMMARY_DATE_RE.search(raw_text)
    if not date_match:
        return None
    month = _MONTHS.get(date_match.group("month").upper())
    if month is None:
        return None

    outcome_date = date(
        int(date_match.group("year")),
        month,
        int(date_match.group("day")),
    )
    high_temp_f = _parse_cli_temperature(_MAX_RE.search(raw_text))
    low_temp_f = _parse_cli_temperature(_MIN_RE.search(raw_text))
    if high_temp_f is None and low_temp_f is None:
        return None

    return WeatherOutcome(
        city_code=city_code.upper(),
        station_id=station_id,
        outcome_date=outcome_date,
        high_temp_f=high_temp_f,
        low_temp_f=low_temp_f,
        source_product_id=source_product_id,
        raw_text=raw_text,
    )


def _is_interim_cli(raw_text: str) -> bool:
    return "VALID TODAY AS OF" in raw_text.upper()


def _dedupe_outcomes(outcomes: list[WeatherOutcome]) -> list[WeatherOutcome]:
    by_date: dict[date, WeatherOutcome] = {}
    for outcome in outcomes:
        existing = by_date.get(outcome.outcome_date)
        if existing is None:
            by_date[outcome.outcome_date] = outcome
            continue
        if _is_interim_cli(existing.raw_text) and not _is_interim_cli(outcome.raw_text):
            by_date[outcome.outcome_date] = outcome
    return sorted(by_date.values(), key=lambda outcome: outcome.outcome_date)


async def _nws_get_json(client: httpx.AsyncClient, path: str) -> dict[str, Any]:
    response = await client.get(
        f"{NWS_API_BASE}{path}",
        headers={
            "Accept": "application/geo+json, application/ld+json",
            "User-Agent": NWS_USER_AGENT,
        },
    )
    response.raise_for_status()
    return response.json()


def _product_id(product: dict[str, Any]) -> str | None:
    for key in ("id", "@id"):
        value = product.get(key)
        if isinstance(value, str) and value:
            return value.rstrip("/").split("/")[-1]
    return None


async def fetch_recent_nws_cli_outcomes(city_code: str) -> list[WeatherOutcome]:
    """
    Fetch recent NWS CLI products for a configured city.

    The NWS products API is best suited for recent preliminary CLI bulletins,
    not deep history. Use manual/NCEI backfills for older training ranges.
    """
    source = NWS_CLI_SOURCES.get(city_code.upper())
    if source is None:
        raise ValueError(f"No NWS CLI source mapping configured for {city_code!r}")

    location_id = source["nws_location_id"]
    product_code = source["cli_product_code"]
    station_id = source["station_id"]
    outcomes: list[WeatherOutcome] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        listing = await _nws_get_json(client, f"/products/types/CLI/locations/{location_id}")
        products = listing.get("@graph") or listing.get("features") or []
        for product in products:
            product_id = _product_id(product)
            if product_id is None:
                continue
            detail = await _nws_get_json(client, f"/products/{product_id}")
            raw_text = str(
                detail.get("productText")
                or detail.get("properties", {}).get("productText")
                or ""
            )
            if product_code not in raw_text:
                continue
            parsed = parse_nws_cli_outcome(raw_text, city_code, station_id, product_id)
            if parsed is not None:
                outcomes.append(parsed)
    return _dedupe_outcomes(outcomes)


def store_temperature_outcome(outcome: WeatherOutcome) -> int:
    row = {
        "city_code": outcome.city_code,
        "station_id": outcome.station_id,
        "outcome_date": outcome.outcome_date.isoformat(),
        "high_temp_f": outcome.high_temp_f,
        "low_temp_f": outcome.low_temp_f,
        "source": "nws_cli",
        "source_product_id": outcome.source_product_id,
        "raw_text": outcome.raw_text,
    }
    response = (
        get_supabase_client()
        .table("actual_temperature_outcomes")
        .upsert(row, on_conflict="city_code,outcome_date")
        .execute()
    )
    return len(getattr(response, "data", None) or [row])


async def backfill_recent_nws_cli_outcomes(city_codes: list[str]) -> int:
    """
    Fetch and store recent CLI outcomes for configured cities.

    This is a smoke-test/backfill entry point. For older markets, insert rows
    into actual_temperature_outcomes from NCEI or manual Kalshi settlement data.
    """
    stored = 0
    for city_code in city_codes:
        outcomes = await fetch_recent_nws_cli_outcomes(city_code)
        for outcome in outcomes:
            stored += store_temperature_outcome(outcome)
        LOGGER.info("Stored %s recent NWS CLI outcomes for %s", len(outcomes), city_code)
    return stored


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(level=logging.INFO)
    print(asyncio.run(backfill_recent_nws_cli_outcomes(["NYC"])))
