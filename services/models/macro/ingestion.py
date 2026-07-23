from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

try:
    from fredapi import Fred
except ImportError:  # pragma: no cover - optional until model deps are installed
    Fred = None  # type: ignore[assignment]

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from services.models.macro.features import FRED_SERIES_OF_INTEREST
from services.models.shared.model_registry import get_supabase_client

LOGGER = logging.getLogger(__name__)


def fetch_series(series_id: str, api_key: str) -> pd.Series:
    """Fetch a single FRED series via fredapi. Return as a pandas Series indexed by date."""
    if Fred is None:
        raise RuntimeError("fredapi is required for macro ingestion")
    fred = Fred(api_key=api_key)
    series = fred.get_series(series_id)
    series.index = pd.to_datetime(series.index)
    return series.sort_index()


def store_series_observations(series_id: str, series: pd.Series) -> int:
    """
    Upsert observations into macro_indicator_series.

    Returns the count of rows submitted for upsert.
    """
    rows = []
    for observation_date, value in series.dropna().items():
        rows.append(
            {
                "series_id": series_id,
                "observation_date": pd.Timestamp(observation_date).date().isoformat(),
                "value": round(float(value), 4),
            }
        )
    if not rows:
        LOGGER.warning("FRED series %s produced no non-null observations", series_id)
        return 0

    client = get_supabase_client()
    upserted = 0
    for start in range(0, len(rows), 500):
        batch = rows[start : start + 500]
        response = (
            client.table("macro_indicator_series")
            .upsert(batch, on_conflict="series_id,observation_date")
            .execute()
        )
        upserted += len(getattr(response, "data", None) or batch)
    return upserted


def run_ingestion_for_series(series_ids: list[str], fred_api_key: str) -> None:
    """Fetch and store each requested FRED series. Log counts per series."""
    for series_id in series_ids:
        if series_id not in FRED_SERIES_OF_INTEREST:
            LOGGER.warning("Skipping unsupported macro FRED series %s", series_id)
            continue
        series = fetch_series(series_id, fred_api_key)
        count = store_series_observations(series_id, series)
        LOGGER.info("Upserted %s macro observations for %s", count, series_id)
