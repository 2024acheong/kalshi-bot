from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from typing import Any

import numpy as np

from core.schemas.market import FeatureVector, MarketState
from core.strategies.calibration_mispricing import ProbabilityEstimator
from services.models.shared.artifact_store import load_artifact
from services.models.shared.model_registry import get_latest_model, get_supabase_client

LOGGER = logging.getLogger(__name__)

# The local ticker diagnostic confirmed rows like KXHIGHNY-26MAY21-B74.5 and
# KXHIGHNY-26MAY21-T75. For -T contracts, the ticker itself does not encode
# whether YES means above or below the threshold, so estimate() resolves that
# direction from market_catalog.title and returns None if the title is missing.
_WEATHER_TICKER_RE = re.compile(
    r"^(?P<series>KX(?P<kind>HIGH|LOWT?)(?P<city>[A-Z]+))-"
    r"(?P<date>\d{2}[A-Z]{3}\d{2})-"
    r"(?P<bracket_type>[BTL])(?P<strike>-?\d+(?:\.\d+)?)$",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"^(?P<year>\d{2})(?P<month>[A-Z]{3})(?P<day>\d{2})$", re.IGNORECASE)
_MONTHS = {
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

CITY_COORDINATES = {
    "NY": (40.783, -73.967),
    "NYC": (40.783, -73.967),
    "CHI": (41.786, -87.752),
    "MIA": (25.795, -80.287),
    "AUS": (30.320, -97.760),
    "PHIL": (39.872, -75.241),
    "DAL": (32.899, -97.040),
    "HOU": (29.646, -95.279),
    "LA": (33.938, -118.389),
}


def fahrenheit_to_celsius(value: float) -> float:
    return (value - 32.0) * 5.0 / 9.0


def compute_ensemble_features(temperatures: list[float]) -> dict[str, float] | None:
    values = [value for value in temperatures if value is not None and math.isfinite(value)]
    if not values:
        return None
    array = np.array(values, dtype=float)
    return {
        "ensemble_mean": float(np.mean(array)),
        "ensemble_std": float(np.std(array)),
        "ensemble_min": float(np.min(array)),
        "ensemble_max": float(np.max(array)),
    }


class WeatherEnsembleEstimator(ProbabilityEstimator):
    """
    Estimates P(YES) for Kalshi weather bracket markets.

    IMPORTANT: as of this branch, train.py produces a SYNTHETIC placeholder
    model. It must not be trusted for real trading decisions until retrained on
    real resolved weather outcomes.
    """

    def __init__(self, model_name: str = "weather_bracket_model"):
        try:
            registry_entry = get_latest_model(model_name)
        except Exception as exc:
            LOGGER.warning("No weather model available from registry: %s", exc)
            registry_entry = None
        self.model = (
            None if registry_entry is None else load_artifact(registry_entry["artifact_path"])
        )

    def _parse_ticker(self, ticker: str) -> dict[str, Any] | None:
        """
        Parse inspected Kalshi weather tickers, e.g. KXHIGHNY-26MAY21-B74.5.

        The diagnostic showed -T thresholds can be either "<68" or ">75"; that
        direction is resolved from market_catalog.title during estimate().
        """
        match = _WEATHER_TICKER_RE.match(ticker)
        if not match:
            return None

        date_match = _DATE_RE.match(match.group("date"))
        if not date_match:
            return None
        month = _MONTHS.get(date_match.group("month").upper())
        if month is None:
            return None

        city_code = match.group("city").upper()
        coordinates = CITY_COORDINATES.get(city_code)
        if coordinates is None:
            return None

        year = 2000 + int(date_match.group("year"))
        target_date = datetime(
            year,
            month,
            int(date_match.group("day")),
            tzinfo=timezone.utc,
        ).date()
        strike = float(match.group("strike"))
        bracket_type = match.group("bracket_type").upper()
        lower_f = strike
        upper_f = None
        strike_type = "threshold"
        if bracket_type == "B":
            lower_f = math.floor(strike)
            upper_f = math.ceil(strike)
            strike_type = "between"
        elif bracket_type == "L":
            strike_type = "less"

        return {
            "series": match.group("series").upper(),
            "kind": match.group("kind").upper(),
            "city_code": city_code,
            "location_lat": coordinates[0],
            "location_lon": coordinates[1],
            "target_date": target_date,
            "target_datetime": datetime.combine(target_date, time.min, tzinfo=timezone.utc),
            "bracket_type": bracket_type,
            "strike_type": strike_type,
            "threshold_f": strike,
            "lower_f": lower_f,
            "upper_f": upper_f,
        }

    def _fetch_latest_ensemble_rows(self, parsed: dict[str, Any]) -> list[dict[str, Any]]:
        start = parsed["target_datetime"]
        end = start + timedelta(days=1)
        response = (
            get_supabase_client()
            .table("weather_ensemble_snapshots")
            .select("forecast_issued_at,target_datetime,ensemble_member,temperature_c")
            .eq("location_lat", round(parsed["location_lat"], 3))
            .eq("location_lon", round(parsed["location_lon"], 3))
            .gte("target_datetime", start.isoformat())
            .lt("target_datetime", end.isoformat())
            .order("forecast_issued_at", desc=True)
            .limit(1000)
            .execute()
        )
        rows = getattr(response, "data", None) or []
        if not rows:
            return []
        latest = max(str(row.get("forecast_issued_at")) for row in rows)
        return [row for row in rows if str(row.get("forecast_issued_at")) == latest]

    def _resolve_threshold_strike_type(self, ticker: str) -> str | None:
        response = (
            get_supabase_client()
            .table("market_catalog")
            .select("title")
            .eq("ticker", ticker)
            .limit(1)
            .execute()
        )
        rows = getattr(response, "data", None) or []
        if not rows:
            LOGGER.warning("Cannot resolve threshold direction for %s without market title", ticker)
            return None

        title = str(rows[0].get("title") or "")
        if ">" in title:
            return "greater"
        if "<" in title:
            return "less"

        LOGGER.warning("Cannot resolve threshold direction for %s from title: %s", ticker, title)
        return None

    def _predict_greater_than(
        self,
        stats: dict[str, float],
        hours_to_target: float,
        threshold_f: float,
    ) -> float | None:
        if self.model is None:
            return None
        threshold_c = fahrenheit_to_celsius(threshold_f)
        model_features = np.array(
            [
                [
                    stats["ensemble_mean"] - threshold_c,
                    stats["ensemble_std"],
                    stats["ensemble_min"] - threshold_c,
                    stats["ensemble_max"] - threshold_c,
                    hours_to_target,
                ]
            ],
            dtype=float,
        )
        if hasattr(self.model, "predict_proba"):
            return float(self.model.predict_proba(model_features)[0][1])
        prediction = self.model.predict(model_features)
        if isinstance(prediction, (list, tuple, np.ndarray)):
            return float(prediction[0])
        return float(prediction)

    def estimate(self, market: MarketState, features: FeatureVector) -> float | None:
        """
        Return a clipped YES probability, or None on missing data/model/parse.

        This method catches malformed data and registry/query problems because
        it may be called for every market tick.
        """
        if self.model is None:
            return None

        try:
            parsed = self._parse_ticker(market.ticker)
            if parsed is None:
                return None

            rows = self._fetch_latest_ensemble_rows(parsed)
            if not rows:
                return None

            member_temperatures: dict[int, list[float]] = defaultdict(list)
            for row in rows:
                temperature = row.get("temperature_c")
                if temperature is None:
                    continue
                member_temperatures[int(row.get("ensemble_member", 0))].append(float(temperature))
            if not member_temperatures:
                return None

            kind = str(parsed["kind"]).upper()
            daily_values = [
                max(values) if kind == "HIGH" else min(values)
                for values in member_temperatures.values()
                if values
            ]
            stats = compute_ensemble_features(daily_values)
            if stats is None:
                return None

            hours_to_target = features.time_to_close_hours
            if hours_to_target is None:
                now = features.timestamp.astimezone(timezone.utc)
                hours_to_target = max(
                    (parsed["target_datetime"] - now).total_seconds() / 3600.0,
                    0.0,
                )

            strike_type = parsed["strike_type"]
            if strike_type == "threshold":
                strike_type = self._resolve_threshold_strike_type(market.ticker)
                if strike_type is None:
                    return None

            if strike_type == "between":
                lower_probability = self._predict_greater_than(
                    stats,
                    hours_to_target,
                    float(parsed["lower_f"]) - 0.5,
                )
                upper_probability = self._predict_greater_than(
                    stats,
                    hours_to_target,
                    float(parsed["upper_f"]) + 0.5,
                )
                if lower_probability is None or upper_probability is None:
                    return None
                probability = lower_probability - upper_probability
            else:
                probability = self._predict_greater_than(
                    stats,
                    hours_to_target,
                    float(parsed["threshold_f"]),
                )
                if probability is None:
                    return None
                if strike_type == "less":
                    probability = 1.0 - probability

            return min(max(float(probability), 0.01), 0.99)
        except Exception as exc:
            LOGGER.warning("Weather estimator skipped %s: %s", market.ticker, exc)
            return None
