from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import httpx
from dotenv import load_dotenv

try:
    from supabase import create_client
except ImportError:  # pragma: no cover - optional in lightweight test envs
    create_client = None  # type: ignore[assignment]

load_dotenv()

OPEN_METEO_ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"

LOGGER = logging.getLogger(__name__)
_MEMBER_RE = re.compile(r"^temperature_2m_member(?P<member>\d+)$")


@lru_cache(maxsize=1)
def get_supabase_client() -> Any:
    if create_client is None:
        raise RuntimeError("supabase>=2.4 is required for weather ingestion")
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SECRET_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY or SUPABASE_SECRET_KEY are required"
        )
    return create_client(url, key)


async def fetch_weather_ensemble(
    lat: float,
    lon: float,
    hourly_var: str = "temperature_2m",
    model: str = "gfs_seamless",
) -> dict:
    """
    Fetch ensemble forecast data from Open-Meteo for the given coordinates.

    Open-Meteo's ensemble endpoint accepts a `models` parameter and returns an
    `hourly` object whose ensemble variables are represented as parallel arrays
    such as `temperature_2m_member01`, plus a `time` array.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": hourly_var,
        "models": model,
        "timezone": "UTC",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(OPEN_METEO_ENSEMBLE_URL, params=params)
        response.raise_for_status()
        return response.json()


def _parse_open_meteo_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _extract_temperature_rows(
    lat: float,
    lon: float,
    forecast_issued_at: datetime,
    raw_response: dict,
) -> list[dict[str, Any]]:
    hourly = raw_response.get("hourly")
    if not isinstance(hourly, dict):
        return []

    times = hourly.get("time")
    if not isinstance(times, list):
        return []

    member_series: list[tuple[int, list[Any]]] = []
    for key, values in hourly.items():
        match = _MEMBER_RE.match(key)
        if match and isinstance(values, list):
            member_series.append((int(match.group("member")), values))

    if not member_series and isinstance(hourly.get("temperature_2m"), list):
        LOGGER.warning(
            "Open-Meteo response did not include explicit ensemble member keys; "
            "storing temperature_2m as ensemble_member=0."
        )
        member_series.append((0, hourly["temperature_2m"]))

    issued_at = forecast_issued_at.astimezone(timezone.utc)
    rows: list[dict[str, Any]] = []
    for member, temperatures in member_series:
        for index, target in enumerate(times):
            temperature = temperatures[index] if index < len(temperatures) else None
            rows.append(
                {
                    "location_lat": round(lat, 3),
                    "location_lon": round(lon, 3),
                    "forecast_issued_at": issued_at.isoformat(),
                    "target_datetime": _parse_open_meteo_time(str(target)).isoformat(),
                    "ensemble_member": member,
                    "temperature_c": None if temperature is None else round(float(temperature), 2),
                }
            )
    return rows


def store_ensemble_snapshot(
    lat: float,
    lon: float,
    forecast_issued_at: datetime,
    raw_response: dict,
) -> int:
    """
    Store one row per (target_datetime, ensemble_member) pair.

    The parser intentionally recognizes Open-Meteo's documented member-key
    shape (`temperature_2m_member01`, etc.) and logs if it has to fall back to
    a single aggregate `temperature_2m` series.
    """
    rows = _extract_temperature_rows(lat, lon, forecast_issued_at, raw_response)
    if not rows:
        LOGGER.warning("Open-Meteo response produced no weather ensemble rows")
        return 0

    client = get_supabase_client()
    inserted = 0
    for start in range(0, len(rows), 500):
        batch = rows[start : start + 500]
        response = client.table("weather_ensemble_snapshots").insert(batch).execute()
        inserted += len(getattr(response, "data", None) or batch)
    return inserted


async def run_ingestion_for_locations(locations: list[tuple[float, float]]) -> None:
    """
    Fetch and store an ensemble snapshot for each location.

    This is the callable entry point a scheduler can invoke later; scheduling
    itself is intentionally out of scope.
    """
    for lat, lon in locations:
        issued_at = datetime.now(timezone.utc)
        raw_response = await fetch_weather_ensemble(lat, lon)
        inserted = store_ensemble_snapshot(lat, lon, issued_at, raw_response)
        LOGGER.info("Inserted %s weather ensemble rows for %.3f, %.3f", inserted, lat, lon)
