from __future__ import annotations

import logging
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from services.models.shared.artifact_store import save_artifact
from services.models.shared.model_registry import get_supabase_client, register_model
from services.models.weather.estimator import WeatherEnsembleEstimator, fahrenheit_to_celsius

LOGGER = logging.getLogger(__name__)
MODEL_NAME = "weather_bracket_model"
SYNTHETIC_WARNING = (
    "WARNING: training weather_bracket_model on SYNTHETIC placeholder data. "
    "This model is NOT suitable for real trading and must be retrained on "
    "resolved weather outcomes before production use."
)
_TITLE_GREATER_RE = re.compile(r">\s*(?P<threshold>-?\d+(?:\.\d+)?)")
_TITLE_LESS_RE = re.compile(r"<\s*(?P<threshold>-?\d+(?:\.\d+)?)")
_TITLE_BETWEEN_RE = re.compile(
    r"(?P<lower>-?\d+(?:\.\d+)?)\s*-\s*(?P<upper>-?\d+(?:\.\d+)?)\s*°"
)


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _compute_ensemble_features(temperatures: list[float]) -> dict[str, float]:
    values = np.array(temperatures, dtype=float)
    return {
        "ensemble_mean": float(np.mean(values)),
        "ensemble_std": float(np.std(values)),
        "ensemble_min": float(np.min(values)),
        "ensemble_max": float(np.max(values)),
    }


def _parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value))


def _parse_market_for_training(ticker: str) -> dict[str, Any] | None:
    parser = WeatherEnsembleEstimator.__new__(WeatherEnsembleEstimator)
    return parser._parse_ticker(ticker)


def _label_from_title(title: str, actual_temp_f: float) -> tuple[int, float] | None:
    greater = _TITLE_GREATER_RE.search(title)
    if greater:
        threshold = float(greater.group("threshold"))
        return int(actual_temp_f > threshold), threshold

    less = _TITLE_LESS_RE.search(title)
    if less:
        threshold = float(less.group("threshold"))
        return int(actual_temp_f < threshold), threshold

    between = _TITLE_BETWEEN_RE.search(title)
    if between:
        lower = float(between.group("lower"))
        upper = float(between.group("upper"))
        # Use the midpoint threshold representation used by the synthetic
        # placeholder model while keeping labels from the actual bracket bounds.
        return int(lower <= actual_temp_f <= upper), (lower + upper) / 2.0

    return None


def _load_real_training_rows() -> tuple[np.ndarray, np.ndarray] | None:
    """
    Build training rows from weather_ensemble_snapshots and real outcomes.

    A row is produced for each weather market whose city/date has an actual
    outcome and whose city/date has ensemble snapshots. Threshold direction is
    read from market_catalog.title because inspected -T tickers are ambiguous.
    """
    client = get_supabase_client()
    outcomes_response = (
        client.table("actual_temperature_outcomes")
        .select("city_code,outcome_date,high_temp_f,low_temp_f")
        .execute()
    )
    outcomes = getattr(outcomes_response, "data", None) or []
    if not outcomes:
        return None

    outcome_by_city_date: dict[tuple[str, date], dict[str, Any]] = {}
    for row in outcomes:
        key = (str(row["city_code"]).upper(), _parse_date(row["outcome_date"]))
        outcome_by_city_date[key] = row

    markets_response = (
        client.table("market_catalog")
        .select("ticker,title")
        .or_("ticker.ilike.KXHIGH%,ticker.ilike.KXLOW%")
        .execute()
    )
    markets = getattr(markets_response, "data", None) or []
    candidate_markets: list[tuple[dict[str, Any], str, str, int, float]] = []
    for market in markets:
        parsed = _parse_market_for_training(str(market["ticker"]))
        if parsed is None:
            continue
        outcome = outcome_by_city_date.get(
            (str(parsed["city_code"]).upper(), parsed["target_date"])
        )
        if outcome is None:
            continue
        temp_key = "high_temp_f" if str(parsed["kind"]).upper() == "HIGH" else "low_temp_f"
        actual_temp = outcome.get(temp_key)
        if actual_temp is None:
            continue
        label = _label_from_title(str(market.get("title") or ""), float(actual_temp))
        if label is None:
            continue
        label_value, threshold_f = label
        candidate_markets.append(
            (
                parsed,
                str(market["ticker"]),
                str(market.get("title") or ""),
                label_value,
                threshold_f,
            )
        )

    if not candidate_markets:
        return None

    snapshot_response = (
        client.table("weather_ensemble_snapshots")
        .select(
            "location_lat,location_lon,forecast_issued_at,"
            "target_datetime,ensemble_member,temperature_c"
        )
        .limit(50000)
        .execute()
    )
    snapshots = getattr(snapshot_response, "data", None) or []
    grouped: dict[tuple[float, float, date, str, int], list[float]] = defaultdict(list)
    issued_by_group: dict[tuple[float, float, date, str, int], datetime] = {}
    for row in snapshots:
        temperature = row.get("temperature_c")
        if temperature is None:
            continue
        target = _parse_datetime(row["target_datetime"])
        key = (
            round(float(row["location_lat"]), 3),
            round(float(row["location_lon"]), 3),
            target.date(),
            str(row["forecast_issued_at"]),
            int(row.get("ensemble_member", 0)),
        )
        grouped[key].append(float(temperature))
        issued_by_group[key] = _parse_datetime(row["forecast_issued_at"])

    by_forecast: dict[tuple[float, float, date, str], list[tuple[int, float, float, datetime]]] = (
        defaultdict(list)
    )
    for (lat, lon, target_date, issued_raw, member), temperatures in grouped.items():
        if not temperatures:
            continue
        by_forecast[(lat, lon, target_date, issued_raw)].append(
            (
                member,
                max(temperatures),
                min(temperatures),
                issued_by_group[(lat, lon, target_date, issued_raw)],
            )
        )

    x_rows: list[list[float]] = []
    y_rows: list[int] = []
    for parsed, _ticker, _title, label_value, threshold_f in candidate_markets:
        target_date = parsed["target_date"]
        threshold_c = fahrenheit_to_celsius(threshold_f)
        for (lat, lon, forecast_date, _issued_raw), member_values in by_forecast.items():
            if (
                lat != round(float(parsed["location_lat"]), 3)
                or lon != round(float(parsed["location_lon"]), 3)
                or forecast_date != target_date
            ):
                continue
            kind = str(parsed["kind"]).upper()
            daily_values = [
                high_value if kind == "HIGH" else low_value
                for _member, high_value, low_value, _issued_at in member_values
            ]
            if len(daily_values) < 2:
                continue
            stats = _compute_ensemble_features(daily_values)
            issued_at = min(
                issued_at for _member, _high_value, _low_value, issued_at in member_values
            )
            hours_to_target = max(
                (
                    datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
                    - issued_at
                ).total_seconds()
                / 3600.0,
                0.0,
            )
            x_rows.append(
                [
                    stats["ensemble_mean"] - threshold_c,
                    stats["ensemble_std"],
                    stats["ensemble_min"] - threshold_c,
                    stats["ensemble_max"] - threshold_c,
                    hours_to_target,
                ]
            )
            y_rows.append(label_value)

    if len(set(y_rows)) < 2 or len(y_rows) < 10:
        LOGGER.warning(
            "Real outcome training data is present but insufficient "
            "(rows=%s, classes=%s); falling back to synthetic placeholder.",
            len(y_rows),
            sorted(set(y_rows)),
        )
        return None

    return np.array(x_rows, dtype=float), np.array(y_rows, dtype=int)


def _load_snapshot_features() -> list[dict[str, float]]:
    response = (
        get_supabase_client()
        .table("weather_ensemble_snapshots")
        .select("forecast_issued_at,target_datetime,ensemble_member,temperature_c")
        .limit(10000)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        temperature = row.get("temperature_c")
        if temperature is None:
            continue
        grouped[(str(row["forecast_issued_at"]), str(row["target_datetime"]))].append(
            float(temperature)
        )

    features: list[dict[str, float]] = []
    for (issued_at_raw, target_raw), temperatures in grouped.items():
        if len(temperatures) < 2:
            continue
        issued_at = _parse_datetime(issued_at_raw)
        target = _parse_datetime(target_raw)
        row_features = _compute_ensemble_features(temperatures)
        row_features["hours_to_target"] = max(
            (target - issued_at).total_seconds() / 3600.0,
            0.0,
        )
        features.append(row_features)
    return features


def _synthetic_training_rows(
    base_features: list[dict[str, float]],
) -> tuple[np.ndarray, np.ndarray]:
    LOGGER.warning(SYNTHETIC_WARNING)
    if not base_features:
        rng = np.random.default_rng(42)
        base_features = [
            {
                "ensemble_mean": float(rng.normal(20.0, 9.0)),
                "ensemble_std": float(rng.uniform(0.5, 4.5)),
                "ensemble_min": 0.0,
                "ensemble_max": 0.0,
                "hours_to_target": float(rng.uniform(1.0, 96.0)),
            }
            for _ in range(240)
        ]
        for row in base_features:
            row["ensemble_min"] = row["ensemble_mean"] - row["ensemble_std"] * 1.8
            row["ensemble_max"] = row["ensemble_mean"] + row["ensemble_std"] * 1.8

    rng = np.random.default_rng(7)
    x_rows: list[list[float]] = []
    y_rows: list[int] = []
    for row in base_features:
        for threshold_offset in (-4.0, 0.0, 4.0):
            noisy_actual = row["ensemble_mean"] + float(
                rng.normal(0.0, max(row["ensemble_std"], 1.0))
            )
            threshold = row["ensemble_mean"] + threshold_offset
            # Features are expressed relative to the bracket threshold so the
            # binary model can be reused for arbitrary Kalshi strikes.
            x_rows.append(
                [
                    row["ensemble_mean"] - threshold,
                    row["ensemble_std"],
                    row["ensemble_min"] - threshold,
                    row["ensemble_max"] - threshold,
                    row["hours_to_target"],
                ]
            )
            y_rows.append(int(noisy_actual > threshold))
    return np.array(x_rows, dtype=float), np.array(y_rows, dtype=int)


def train_weather_model() -> dict[str, Any]:
    """
    Train and register a LightGBM weather bracket model.

    If actual_temperature_outcomes contains enough resolved labels, train on
    those. Otherwise fall back to synthetic labels with a prominent warning.
    """
    try:
        from lightgbm import LGBMClassifier
        from sklearn.metrics import accuracy_score, log_loss
        from sklearn.model_selection import train_test_split
    except ImportError as exc:  # pragma: no cover - dependency installation issue
        raise RuntimeError(
            "lightgbm and scikit-learn are required to train weather models"
        ) from exc

    try:
        base_features = _load_snapshot_features()
    except Exception as exc:
        LOGGER.warning(
            "Could not load weather_ensemble_snapshots; using synthetic base data: %s",
            exc,
        )
        base_features = []

    real_training_rows = None
    try:
        real_training_rows = _load_real_training_rows()
    except Exception as exc:
        LOGGER.warning("Could not build real outcome training rows: %s", exc)

    synthetic_placeholder = real_training_rows is None
    if real_training_rows is None:
        x_values, y_values = _synthetic_training_rows(base_features)
    else:
        x_values, y_values = real_training_rows
        LOGGER.info("Training weather model on %s real outcome rows", len(x_values))
    x_train, x_test, y_train, y_test = train_test_split(
        x_values,
        y_values,
        test_size=0.25,
        random_state=42,
        stratify=y_values,
    )

    model = LGBMClassifier(n_estimators=80, random_state=42, verbose=-1)
    model.fit(x_train, y_train)
    probabilities = model.predict_proba(x_test)[:, 1]
    metrics = {
        "accuracy": float(accuracy_score(y_test, probabilities >= 0.5)),
        "log_loss": float(log_loss(y_test, probabilities)),
        "synthetic_placeholder": synthetic_placeholder,
        "warning": SYNTHETIC_WARNING if synthetic_placeholder else None,
        "training_rows": int(len(x_values)),
    }

    version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    artifact_path = save_artifact(model, MODEL_NAME, version)
    model_id = register_model(MODEL_NAME, version, artifact_path, metrics)
    return {
        "model_id": model_id,
        "version": version,
        "artifact_path": artifact_path,
        "metrics": metrics,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(train_weather_model())
