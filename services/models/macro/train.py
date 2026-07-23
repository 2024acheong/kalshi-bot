from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from services.models.macro.features import (
    MACRO_METRICS,
    clean_observations,
    compute_trend_features,
    derive_metric_observations,
    feature_dict_to_array,
)
from services.models.shared.artifact_store import save_artifact
from services.models.shared.model_registry import get_supabase_client, register_model

LOGGER = logging.getLogger(__name__)
MODEL_NAME = "macro_threshold_model"
SYNTHETIC_WARNING = (
    "WARNING: training macro_threshold_model on SYNTHETIC placeholder data. "
    "This model is NOT suitable for real trading and must be retrained on "
    "resolved macro outcomes before production use."
)


def _load_base_feature_rows() -> list[list[float]]:
    client = get_supabase_client()
    base_rows: list[list[float]] = []
    for metric in MACRO_METRICS.values():
        response = (
            client.table("macro_indicator_series")
            .select("observation_date,value")
            .eq("series_id", metric.fred_series_id)
            .order("observation_date", desc=True)
            .limit(240)
            .execute()
        )
        rows = getattr(response, "data", None) or []
        raw_observations = clean_observations(rows)
        metric_observations = derive_metric_observations(raw_observations, metric.metric_id)
        for end_index in range(3, len(metric_observations) + 1):
            features = compute_trend_features(metric_observations[:end_index])
            if features is not None:
                base_rows.append(feature_dict_to_array(features))
    return base_rows


def _synthetic_training_rows(base_features: list[list[float]]) -> tuple[np.ndarray, np.ndarray]:
    LOGGER.warning(SYNTHETIC_WARNING)
    rng = np.random.default_rng(42)
    if not base_features:
        base_features = [
            [
                float(rng.normal(2.0, 1.0)),
                float(rng.normal(2.0, 1.0)),
                float(rng.normal(0.0, 0.15)),
                float(rng.integers(0, 4)),
                float(rng.uniform(0.1, 1.5)),
            ]
            for _ in range(240)
        ]

    x_rows: list[list[float]] = []
    y_rows: list[int] = []
    for features in base_features:
        latest_value, value_3mo_ago, trend_slope, months_since_release, volatility = features
        for threshold_offset in (-1.0, 0.0, 1.0):
            threshold = latest_value + threshold_offset * max(volatility, 0.25)
            noisy_actual = latest_value + trend_slope + float(
                rng.normal(0.0, max(volatility, 0.25))
            )
            x_rows.append(
                [
                    latest_value - threshold,
                    value_3mo_ago - threshold,
                    trend_slope,
                    months_since_release,
                    volatility,
                ]
            )
            y_rows.append(int(noisy_actual > threshold))

    return np.array(x_rows, dtype=float), np.array(y_rows, dtype=int)


def train_macro_model() -> dict[str, Any]:
    """
    Train and register a CatBoost macro threshold model.

    This currently uses synthetic placeholder labels because there is no paired
    resolved Kalshi macro outcome dataset in this service yet.
    """
    try:
        from catboost import CatBoostClassifier
        from sklearn.metrics import accuracy_score, log_loss
        from sklearn.model_selection import train_test_split
    except ImportError as exc:  # pragma: no cover - dependency installation issue
        raise RuntimeError(
            "catboost and scikit-learn are required to train macro models"
        ) from exc

    try:
        base_features = _load_base_feature_rows()
    except Exception as exc:
        LOGGER.warning("Could not load macro_indicator_series; using synthetic base data: %s", exc)
        base_features = []

    x_values, y_values = _synthetic_training_rows(base_features)
    x_train, x_test, y_train, y_test = train_test_split(
        x_values,
        y_values,
        test_size=0.25,
        random_state=42,
        stratify=y_values,
    )

    model = CatBoostClassifier(iterations=80, random_seed=42, verbose=False)
    model.fit(x_train, y_train)
    probabilities = model.predict_proba(x_test)[:, 1]
    metrics = {
        "accuracy": float(accuracy_score(y_test, probabilities >= 0.5)),
        "log_loss": float(log_loss(y_test, probabilities)),
        "synthetic_placeholder": True,
        "warning": SYNTHETIC_WARNING,
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
    print(train_macro_model())
