from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import numpy as np

from core.schemas.market import FeatureVector, MarketState
from core.strategies.calibration_mispricing import ProbabilityEstimator
from services.models.macro.features import (
    MACRO_METRICS,
    clean_observations,
    compute_trend_features,
    derive_metric_observations,
    feature_dict_to_array,
)
from services.models.shared.artifact_store import load_artifact
from services.models.shared.model_registry import get_latest_model, get_supabase_client

LOGGER = logging.getLogger(__name__)

# Public Kalshi samples inspected on 2026-07-22 included:
# KXCPI-26SEP-T0.6, KXCPI-26SEP-T-0.4, KXGDP-26JUL30-T3.0,
# KXPAYROLLS-26NOV-T90000, KXU3-26NOV-T4.5, KXFED-27APR-T3.75.
_MACRO_TICKER_RE = re.compile(
    r"^(?P<series>KXCPIYOY|KXCPI|KXGDP|KXPAYROLLS|KXU3|KXFED)-"
    r"(?P<event>\d{2}[A-Z]{3}(?:\d{2})?)-T(?P<threshold>-?\d+(?:\.\d+)?)$",
    re.IGNORECASE,
)
_EVENT_RE = re.compile(r"^(?P<year>\d{2})(?P<month>[A-Z]{3})(?P<day>\d{2})?$", re.IGNORECASE)
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
_SERIES_TO_METRIC = {
    "KXCPI": "cpi_mom",
    "KXCPIYOY": "cpi_yoy",
    "KXGDP": "gdp_annualized",
    "KXPAYROLLS": "payrolls_delta",
    "KXU3": "unemployment_rate",
    "KXFED": "fed_upper_bound",
}


class MacroEstimator(ProbabilityEstimator):
    """
    Estimates P(YES) for Kalshi macro threshold markets.

    IMPORTANT: trained on SYNTHETIC placeholder data by train.py until resolved
    macro outcome collection exists. It is not trustworthy for real trading.
    """

    def __init__(self, model_name: str = "macro_threshold_model"):
        try:
            registry_entry = get_latest_model(model_name)
        except Exception as exc:
            LOGGER.warning("No macro model available from registry: %s", exc)
            registry_entry = None
        self.model = (
            None if registry_entry is None else load_artifact(registry_entry["artifact_path"])
        )

    def _parse_ticker(self, ticker: str) -> dict[str, Any] | None:
        match = _MACRO_TICKER_RE.match(ticker)
        if not match:
            return None

        series = match.group("series").upper()
        metric_id = _SERIES_TO_METRIC.get(series)
        if metric_id is None:
            return None

        event_match = _EVENT_RE.match(match.group("event"))
        if not event_match:
            return None
        month = _MONTHS.get(event_match.group("month").upper())
        if month is None:
            return None

        year = 2000 + int(event_match.group("year"))
        day = int(event_match.group("day") or "01")
        try:
            target_date = datetime(year, month, day, tzinfo=timezone.utc).date()
        except ValueError:
            return None

        metric = MACRO_METRICS[metric_id]
        return {
            "series": series,
            "event_token": match.group("event").upper(),
            "target_date": target_date,
            "threshold": float(match.group("threshold")),
            "direction": "greater",
            "strike_type": "greater",
            "metric_id": metric.metric_id,
            "fred_series_id": metric.fred_series_id,
        }

    def _fetch_series_rows(self, fred_series_id: str) -> list[dict[str, Any]]:
        response = (
            get_supabase_client()
            .table("macro_indicator_series")
            .select("observation_date,value")
            .eq("series_id", fred_series_id)
            .order("observation_date", desc=True)
            .limit(240)
            .execute()
        )
        return getattr(response, "data", None) or []

    def _feature_vector_for_market(
        self,
        parsed: dict[str, Any],
        as_of: datetime,
    ) -> list[float] | None:
        rows = self._fetch_series_rows(str(parsed["fred_series_id"]))
        raw_observations = clean_observations(rows)
        metric_observations = derive_metric_observations(raw_observations, str(parsed["metric_id"]))
        features = compute_trend_features(metric_observations, as_of=as_of)
        if features is None:
            return None

        model_features = feature_dict_to_array(features)
        model_features[0] -= float(parsed["threshold"])
        model_features[1] -= float(parsed["threshold"])
        return model_features

    def _predict_probability(self, model_features: list[float]) -> float:
        values = np.array([model_features], dtype=float)
        if hasattr(self.model, "predict_proba"):
            return float(self.model.predict_proba(values)[0][1])
        prediction = self.model.predict(values)
        if isinstance(prediction, (list, tuple, np.ndarray)):
            return float(prediction[0])
        return float(prediction)

    def estimate(self, market: MarketState, features: FeatureVector) -> float | None:
        """
        Return a clipped YES probability, or None on missing data/model/parse.
        """
        if self.model is None:
            return None

        try:
            parsed = self._parse_ticker(market.ticker)
            if parsed is None:
                return None

            model_features = self._feature_vector_for_market(parsed, features.timestamp)
            if model_features is None:
                return None

            probability = self._predict_probability(model_features)
            return min(max(float(probability), 0.01), 0.99)
        except Exception as exc:
            LOGGER.warning("Macro estimator skipped %s: %s", market.ticker, exc)
            return None
