from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from services.models.shared.model_registry import get_supabase_client

LOGGER = logging.getLogger(__name__)
NWS_API_BASE = "https://api.weather.gov"
NWS_USER_AGENT = "kalshi-bot-weather-outcomes/0.1"
NCEI_DATA_SERVICE_URL = "https://www.ncei.noaa.gov/access/services/data/v1"
NCEI_SOURCE = "ncei_daily_summaries"


@dataclass(frozen=True)
class WeatherOutcome:
    city_code: str
    station_id: str
    outcome_date: date
    high_temp_f: float | None
    low_temp_f: float | None
    source_product_id: str | None
    raw_text: str
    source: str = "nws_cli"


# This is intentionally small and explicit. Kalshi settlement stations should
# be verified per city before adding production coverage.
NWS_CLI_SOURCES = {
    "NY": {"station_id": "KNYC", "nws_location_id": "NYC", "cli_product_code": "CLINYC"},
    "NYC": {"station_id": "KNYC", "nws_location_id": "NYC", "cli_product_code": "CLINYC"},
}
NCEI_DAILY_SUMMARY_SOURCES = {
    "NY": {"station_id": "USW00094728"},
    "NYC": {"station_id": "USW00094728"},
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
        source="nws_cli",
    )


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Expected YYYY-MM-DD date, got {value!r}") from exc


def _parse_float(value: Any) -> float | None:
    if value in (None, "", "MM"):
        return None
    return float(value)


def parse_ncei_daily_summary_outcome(
    row: dict[str, Any],
    city_code: str,
    station_id: str,
) -> WeatherOutcome | None:
    """
    Parse an NCEI daily-summaries JSON row into a temperature outcome.

    The NCEI query uses units=standard, so TMAX/TMIN are already Fahrenheit.
    Missing temperatures are kept as None; rows with neither max nor min are
    skipped because they cannot contribute labels.
    """
    date_raw = row.get("DATE")
    if not isinstance(date_raw, str):
        return None

    high_temp_f = _parse_float(row.get("TMAX"))
    low_temp_f = _parse_float(row.get("TMIN"))
    if high_temp_f is None and low_temp_f is None:
        return None

    return WeatherOutcome(
        city_code=city_code.upper(),
        station_id=station_id,
        outcome_date=_parse_iso_date(date_raw[:10]),
        high_temp_f=high_temp_f,
        low_temp_f=low_temp_f,
        source_product_id=f"ncei:daily-summaries:{station_id}",
        raw_text=json.dumps(row, sort_keys=True),
        source=NCEI_SOURCE,
    )


def chunk_date_range_by_year(start_date: date, end_date: date) -> list[tuple[date, date]]:
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")

    chunks: list[tuple[date, date]] = []
    cursor = start_date
    while cursor <= end_date:
        year_end = date(cursor.year, 12, 31)
        chunk_end = min(year_end, end_date)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


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


async def fetch_ncei_daily_summary_outcomes(
    city_code: str,
    start_date: date,
    end_date: date,
) -> list[WeatherOutcome]:
    """
    Fetch historical high/low outcomes from NCEI daily-summaries.

    This endpoint is tokenless and suitable for historical backfills. It does
    not replace the recent NWS CLI path, which stays useful for settlement-like
    preliminary daily reports.
    """
    source = NCEI_DAILY_SUMMARY_SOURCES.get(city_code.upper())
    if source is None:
        raise ValueError(f"No NCEI daily-summaries source configured for {city_code!r}")

    station_id = source["station_id"]
    params = {
        "dataset": "daily-summaries",
        "stations": station_id,
        "dataTypes": "TMAX,TMIN",
        "units": "standard",
        "format": "json",
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(NCEI_DATA_SERVICE_URL, params=params)
        response.raise_for_status()
        rows = response.json()

    if not isinstance(rows, list):
        raise RuntimeError("NCEI daily-summaries response was not a JSON list")

    outcomes = [
        outcome
        for row in rows
        if isinstance(row, dict)
        for outcome in [parse_ncei_daily_summary_outcome(row, city_code, station_id)]
        if outcome is not None
    ]
    return sorted(outcomes, key=lambda outcome: outcome.outcome_date)


def store_temperature_outcome(outcome: WeatherOutcome) -> int:
    row = {
        "city_code": outcome.city_code,
        "station_id": outcome.station_id,
        "outcome_date": outcome.outcome_date.isoformat(),
        "high_temp_f": outcome.high_temp_f,
        "low_temp_f": outcome.low_temp_f,
        "source": outcome.source,
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


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _derive_ncei_range_from_market_catalog(city_code: str) -> tuple[date, date] | None:
    response = (
        get_supabase_client()
        .table("market_catalog")
        .select("ticker,close_time")
        .ilike("ticker", f"KXHIGH{city_code.upper()}%")
        .execute()
    )
    rows = getattr(response, "data", None) or []
    dates: list[date] = []
    ticker_date_re = re.compile(
        rf"^KXHIGH{re.escape(city_code.upper())}-(\d{{2}}[A-Z]{{3}}\d{{2}})-"
    )
    month_by_abbrev = {
        "JAN": 1,
        "FEB": 2,
        "MAR": 3,
        "APR": 4,
        "MAY": 5,
        "JUN": 6,
        "JUL": 7,
        "AUG": 8,
        "SEP": 9,
        "OCT": 10,
        "NOV": 11,
        "DEC": 12,
    }

    for row in rows:
        ticker = str(row.get("ticker") or "")
        match = ticker_date_re.match(ticker)
        if match:
            encoded = match.group(1)
            dates.append(
                date(
                    2000 + int(encoded[:2]),
                    month_by_abbrev[encoded[2:5]],
                    int(encoded[5:]),
                )
            )
            continue

        close_time = _parse_datetime(row.get("close_time"))
        if close_time is not None:
            dates.append(close_time.date())

    if not dates:
        return None
    return min(dates), max(dates)


def _default_ncei_backfill_range(city_code: str) -> tuple[date, date]:
    catalog_range = _derive_ncei_range_from_market_catalog(city_code)
    if catalog_range is not None:
        return catalog_range
    end_date = datetime.now(timezone.utc).date()
    return end_date - timedelta(days=365), end_date


async def backfill_ncei_daily_summary_outcomes(
    city_code: str = "NYC",
    start_date: date | None = None,
    end_date: date | None = None,
) -> int:
    """
    Backfill historical NCEI daily-summaries outcomes and upsert them.

    If dates are omitted, derive the range from existing weather market tickers
    when possible; otherwise use the last 365 days.
    """
    resolved_start, resolved_end = (
        _default_ncei_backfill_range(city_code)
        if start_date is None or end_date is None
        else (start_date, end_date)
    )
    if start_date is not None:
        resolved_start = start_date
    if end_date is not None:
        resolved_end = end_date

    stored = 0
    for chunk_start, chunk_end in chunk_date_range_by_year(resolved_start, resolved_end):
        outcomes = await fetch_ncei_daily_summary_outcomes(city_code, chunk_start, chunk_end)
        for outcome in outcomes:
            stored += store_temperature_outcome(outcome)
        LOGGER.info(
            "Stored %s NCEI daily-summaries outcomes for %s from %s to %s",
            len(outcomes),
            city_code,
            chunk_start,
            chunk_end,
        )
    return stored


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill weather temperature outcomes.")
    parser.add_argument("--city", default="NYC", help="City code to backfill, currently NYC/NY.")
    parser.add_argument(
        "--start-date",
        type=_parse_iso_date,
        help="Start date in YYYY-MM-DD format. Defaults to market_catalog range or last 365 days.",
    )
    parser.add_argument(
        "--end-date",
        type=_parse_iso_date,
        help="End date in YYYY-MM-DD format. Defaults to market_catalog range or today.",
    )
    parser.add_argument(
        "--source",
        choices=("ncei", "nws-cli"),
        default="ncei",
        help="Backfill source. NCEI is tokenless and suitable for history.",
    )
    return parser


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    args = _build_arg_parser().parse_args()
    if args.source == "nws-cli":
        print(asyncio.run(backfill_recent_nws_cli_outcomes([args.city])))
    else:
        print(
            asyncio.run(
                backfill_ncei_daily_summary_outcomes(
                    args.city,
                    args.start_date,
                    args.end_date,
                )
            )
        )
