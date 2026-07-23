from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable

import numpy as np


@dataclass(frozen=True)
class MacroMetricConfig:
    metric_id: str
    fred_series_id: str
    description: str


MACRO_METRICS: dict[str, MacroMetricConfig] = {
    "cpi_mom": MacroMetricConfig("cpi_mom", "CPIAUCSL", "CPI month-over-month percent"),
    "cpi_yoy": MacroMetricConfig("cpi_yoy", "CPIAUCSL", "CPI year-over-year percent"),
    "gdp_annualized": MacroMetricConfig(
        "gdp_annualized",
        "A191RL1Q225SBEA",
        "Real GDP annualized percent change",
    ),
    "payrolls_delta": MacroMetricConfig(
        "payrolls_delta",
        "PAYEMS",
        "Monthly nonfarm payroll change in thousands",
    ),
    "unemployment_rate": MacroMetricConfig(
        "unemployment_rate",
        "UNRATE",
        "U-3 unemployment rate percent",
    ),
    "fed_upper_bound": MacroMetricConfig(
        "fed_upper_bound",
        "DFEDTARU",
        "Federal funds target upper bound percent",
    ),
}

FRED_SERIES_OF_INTEREST: dict[str, str] = {
    "CPIAUCSL": "consumer_price_index_all_items",
    "UNRATE": "unemployment_rate",
    "PAYEMS": "nonfarm_payrolls",
    "A191RL1Q225SBEA": "real_gdp_annualized_percent_change",
    "DFEDTARU": "fed_funds_target_upper_bound",
}


def normalize_threshold_for_metric(metric_id: str, threshold: float) -> float:
    if metric_id == "payrolls_delta":
        return threshold / 1000.0
    return threshold


def parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value)[:10])


def months_between(start: date, end: date) -> float:
    return max((end.year - start.year) * 12 + (end.month - start.month), 0)


def clean_observations(rows: Iterable[dict[str, Any]]) -> list[tuple[date, float]]:
    observations: list[tuple[date, float]] = []
    for row in rows:
        value = row.get("value")
        if value is None:
            continue
        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            continue
        observations.append((parse_date(row["observation_date"]), numeric_value))
    observations.sort(key=lambda item: item[0])
    return observations


def derive_metric_observations(
    raw_observations: list[tuple[date, float]],
    metric_id: str,
) -> list[tuple[date, float]]:
    """
    Convert raw FRED observations into the unit traded by the supported market.
    """
    observations = [(obs_date, value) for obs_date, value in raw_observations if value is not None]
    observations.sort(key=lambda item: item[0])
    if metric_id == "cpi_mom":
        return [
            (
                observations[index][0],
                (observations[index][1] / observations[index - 1][1] - 1.0) * 100,
            )
            for index in range(1, len(observations))
            if observations[index - 1][1] != 0
        ]
    if metric_id == "cpi_yoy":
        return [
            (
                observations[index][0],
                (observations[index][1] / observations[index - 12][1] - 1.0) * 100,
            )
            for index in range(12, len(observations))
            if observations[index - 12][1] != 0
        ]
    if metric_id == "payrolls_delta":
        return [
            (observations[index][0], observations[index][1] - observations[index - 1][1])
            for index in range(1, len(observations))
        ]
    return observations


def compute_trend_features(
    observations: list[tuple[date, float]],
    as_of: datetime | date | None = None,
) -> dict[str, float] | None:
    """
    Compute [latest, 3mo lag, slope, months since release, trailing volatility].
    """
    values = [(obs_date, value) for obs_date, value in observations if math.isfinite(value)]
    values.sort(key=lambda item: item[0])
    if len(values) < 3:
        return None

    latest_date, latest_value = values[-1]
    three_month_cutoff = date(latest_date.year, latest_date.month, 1)
    target_month_index = three_month_cutoff.year * 12 + three_month_cutoff.month - 3
    lag_candidates = [
        (obs_date, value)
        for obs_date, value in values
        if obs_date.year * 12 + obs_date.month <= target_month_index
    ]
    value_3mo_ago = lag_candidates[-1][1] if lag_candidates else values[0][1]

    trailing = values[-12:]
    x_values = np.arange(len(trailing), dtype=float)
    y_values = np.array([value for _obs_date, value in trailing], dtype=float)
    trend_slope = 0.0
    if len(trailing) >= 2:
        trend_slope = float(np.polyfit(x_values, y_values, 1)[0])

    if as_of is None:
        as_of_date = datetime.now(timezone.utc).date()
    elif isinstance(as_of, datetime):
        as_of_date = as_of.astimezone(timezone.utc).date() if as_of.tzinfo else as_of.date()
    else:
        as_of_date = as_of

    return {
        "latest_value": float(latest_value),
        "value_3mo_ago": float(value_3mo_ago),
        "trend_slope": trend_slope,
        "months_since_last_release": float(months_between(latest_date, as_of_date)),
        "series_volatility": float(np.std(y_values)),
    }


def feature_dict_to_array(features: dict[str, float]) -> list[float]:
    return [
        features["latest_value"],
        features["value_3mo_ago"],
        features["trend_slope"],
        features["months_since_last_release"],
        features["series_volatility"],
    ]


def observations_on_or_before(
    observations: list[tuple[date, float]],
    cutoff_date: date,
) -> list[tuple[date, float]]:
    return [(obs_date, value) for obs_date, value in observations if obs_date <= cutoff_date]


def find_metric_value_for_target(
    metric_observations: list[tuple[date, float]],
    target_date: date,
    metric_id: str,
) -> tuple[date, float] | None:
    """
    Find the FRED-derived actual value that maps to a parsed Kalshi target.

    Monthly/quarterly macro tickers usually encode the target period rather
    than the exact FRED observation date, so match by year/month. Daily Fed
    target-rate markets fall back to the latest observation on or before the
    target date.
    """
    observations = sorted(metric_observations, key=lambda item: item[0])
    if metric_id == "fed_upper_bound":
        candidates = [
            (obs_date, value)
            for obs_date, value in observations
            if obs_date <= target_date
        ]
        return candidates[-1] if candidates else None

    for obs_date, value in observations:
        if obs_date.year == target_date.year and obs_date.month == target_date.month:
            return obs_date, value
    return None


def compute_threshold_model_features(
    metric_observations: list[tuple[date, float]],
    threshold: float,
    cutoff_date: date,
    as_of: datetime | date | None = None,
) -> list[float] | None:
    historical_observations = observations_on_or_before(metric_observations, cutoff_date)
    features = compute_trend_features(historical_observations, as_of=as_of or cutoff_date)
    if features is None:
        return None

    model_features = feature_dict_to_array(features)
    model_features[0] -= threshold
    model_features[1] -= threshold
    return model_features
