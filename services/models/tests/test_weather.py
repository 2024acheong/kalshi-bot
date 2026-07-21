from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.schemas.market import FeatureVector, MarketState, MarketStatus
from services.models.shared.artifact_store import save_artifact
from services.models.weather.outcomes import parse_nws_cli_outcome
from services.models.weather.estimator import WeatherEnsembleEstimator, compute_ensemble_features
from services.models.weather.train import _label_from_title


def make_market(ticker: str) -> MarketState:
    return MarketState(
        ticker=ticker,
        timestamp=datetime(2026, 3, 24, 12, tzinfo=timezone.utc),
        yes_bid=Decimal("0.40"),
        yes_ask=Decimal("0.45"),
        yes_bid_size=100,
        yes_ask_size=100,
        last_price=Decimal("0.42"),
        volume_24h=1000,
        open_interest=2000,
        close_time=datetime(2026, 3, 25, 5, tzinfo=timezone.utc),
        status=MarketStatus.OPEN,
        source="test",
    )


def make_features() -> FeatureVector:
    return FeatureVector(
        ticker="KXHIGHMIA-26MAR24-B80.5",
        timestamp=datetime(2026, 3, 24, 12, tzinfo=timezone.utc),
        mid_price=0.425,
        spread_pct=0.05,
        spread_ticks=5,
        bid_ask_imbalance=0,
        time_to_close_hours=17,
        implied_probability=0.425,
        liquidity_score=1,
        price_momentum_1h=0,
        price_momentum_24h=0,
        volume_zscore=0,
        open_interest_delta=0,
    )


def test_artifact_store_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("services.models.shared.artifact_store.ARTIFACT_DIR", str(tmp_path))
    model = {"kind": "stand-in", "weights": [1, 2, 3]}

    path = save_artifact(model, "test_model", "v1")

    from services.models.shared.artifact_store import load_artifact

    assert load_artifact(path) == model


def test_estimator_returns_none_with_no_registered_model(monkeypatch) -> None:
    monkeypatch.setattr("services.models.weather.estimator.get_latest_model", lambda name: None)

    estimator = WeatherEnsembleEstimator()

    assert estimator.estimate(make_market("KXHIGHMIA-26MAR24-B80.5"), make_features()) is None


def test_estimator_returns_none_for_unparseable_ticker(monkeypatch) -> None:
    monkeypatch.setattr("services.models.weather.estimator.get_latest_model", lambda name: None)
    estimator = WeatherEnsembleEstimator()

    assert estimator._parse_ticker("FED-23DEC-T3.00") is None
    assert estimator.estimate(make_market("FED-23DEC-T3.00"), make_features()) is None


def test_ticker_parsing_extracts_expected_fields(monkeypatch) -> None:
    monkeypatch.setattr("services.models.weather.estimator.get_latest_model", lambda name: None)
    estimator = WeatherEnsembleEstimator()

    parsed = estimator._parse_ticker("KXHIGHMIA-26MAR24-B80.5")

    assert parsed is not None
    assert parsed["kind"] == "HIGH"
    assert parsed["city_code"] == "MIA"
    assert parsed["target_date"].isoformat() == "2026-03-24"
    assert parsed["strike_type"] == "between"
    assert parsed["lower_f"] == 80
    assert parsed["upper_f"] == 81


def test_ticker_parsing_marks_threshold_direction_as_title_dependent(monkeypatch) -> None:
    monkeypatch.setattr("services.models.weather.estimator.get_latest_model", lambda name: None)
    estimator = WeatherEnsembleEstimator()

    parsed = estimator._parse_ticker("KXHIGHNY-26MAY21-T68")

    assert parsed is not None
    assert parsed["strike_type"] == "threshold"
    assert parsed["threshold_f"] == 68


def test_ensemble_feature_computation() -> None:
    stats = compute_ensemble_features([10.0, 12.0, 14.0])

    assert stats is not None
    assert stats["ensemble_mean"] == pytest.approx(12.0)
    assert stats["ensemble_std"] == pytest.approx(1.632993, rel=1e-6)
    assert stats["ensemble_min"] == pytest.approx(10.0)
    assert stats["ensemble_max"] == pytest.approx(14.0)


def test_parse_nws_cli_outcome_extracts_date_and_temperatures() -> None:
    raw_text = """
CLINYC
...THE CENTRAL PARK NY CLIMATE SUMMARY FOR MAY 21 2026...

TEMPERATURE (F)
 YESTERDAY
  MAXIMUM         75   3:24 PM
  MINIMUM         68   5:11 AM
"""

    outcome = parse_nws_cli_outcome(raw_text, "NYC", "KNYC", "abc123")

    assert outcome is not None
    assert outcome.outcome_date.isoformat() == "2026-05-21"
    assert outcome.high_temp_f == pytest.approx(75)
    assert outcome.low_temp_f == pytest.approx(68)
    assert outcome.source_product_id == "abc123"


def test_label_from_title_handles_greater_less_and_between() -> None:
    assert _label_from_title("Will the **high temp in NYC** be >75°?", 76) == (1, 75)
    assert _label_from_title("Will the **high temp in NYC** be <68°?", 68) == (0, 68)
    assert _label_from_title("Will the **high temp in NYC** be 74-75°?", 75) == (1, 74.5)


def test_estimator_clips_output_to_valid_range(monkeypatch) -> None:
    class FakeModel:
        def predict(self, values):
            return [1.5]

    rows = [
        {
            "forecast_issued_at": "2026-03-24T00:00:00+00:00",
            "target_datetime": "2026-03-24T12:00:00+00:00",
            "ensemble_member": 1,
            "temperature_c": 28.0,
        },
        {
            "forecast_issued_at": "2026-03-24T00:00:00+00:00",
            "target_datetime": "2026-03-24T12:00:00+00:00",
            "ensemble_member": 2,
            "temperature_c": 29.0,
        },
    ]
    monkeypatch.setattr("services.models.weather.estimator.get_latest_model", lambda name: None)
    estimator = WeatherEnsembleEstimator()
    estimator.model = FakeModel()
    monkeypatch.setattr(estimator, "_fetch_latest_ensemble_rows", lambda parsed: rows)
    monkeypatch.setattr(estimator, "_resolve_threshold_strike_type", lambda ticker: "greater")

    assert estimator.estimate(make_market("KXHIGHMIA-26MAR24-T80"), make_features()) == 0.99
