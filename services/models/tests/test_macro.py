from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from core.schemas.market import FeatureVector, MarketState, MarketStatus
from services.models.macro.estimator import MacroEstimator
from services.models.macro.features import (
    clean_observations,
    compute_trend_features,
    derive_metric_observations,
)


def make_market(ticker: str) -> MarketState:
    return MarketState(
        ticker=ticker,
        timestamp=datetime(2026, 7, 22, 12, tzinfo=timezone.utc),
        yes_bid=Decimal("0.40"),
        yes_ask=Decimal("0.45"),
        yes_bid_size=100,
        yes_ask_size=100,
        last_price=Decimal("0.42"),
        volume_24h=1000,
        open_interest=2000,
        close_time=datetime(2026, 8, 12, 12, 25, tzinfo=timezone.utc),
        status=MarketStatus.OPEN,
        source="test",
    )


def make_features() -> FeatureVector:
    return FeatureVector(
        ticker="KXCPI-26SEP-T0.6",
        timestamp=datetime(2026, 7, 22, 12, tzinfo=timezone.utc),
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


def test_estimator_returns_none_with_no_registered_model(monkeypatch) -> None:
    monkeypatch.setattr("services.models.macro.estimator.get_latest_model", lambda name: None)

    estimator = MacroEstimator()

    assert estimator.estimate(make_market("KXCPI-26SEP-T0.6"), make_features()) is None


def test_estimator_returns_none_for_unparseable_ticker(monkeypatch) -> None:
    monkeypatch.setattr("services.models.macro.estimator.get_latest_model", lambda name: None)
    estimator = MacroEstimator()

    assert estimator._parse_ticker("KXFEDDECISION-28JAN-H25") is None
    assert estimator._parse_ticker("KXJOBLESS-26NOV-T250000") is None
    assert estimator.estimate(make_market("KXFEDDECISION-28JAN-H25"), make_features()) is None


@pytest.mark.parametrize(
    ("ticker", "series", "metric_id", "fred_series_id", "threshold", "target_date"),
    [
        ("KXCPI-26SEP-T0.6", "KXCPI", "cpi_mom", "CPIAUCSL", 0.6, date(2026, 9, 1)),
        ("KXCPI-26SEP-T-0.4", "KXCPI", "cpi_mom", "CPIAUCSL", -0.4, date(2026, 9, 1)),
        (
            "KXCPIYOY-26AUG-T2.5",
            "KXCPIYOY",
            "cpi_yoy",
            "CPIAUCSL",
            2.5,
            date(2026, 8, 1),
        ),
        (
            "KXGDP-26JUL30-T3.0",
            "KXGDP",
            "gdp_annualized",
            "A191RL1Q225SBEA",
            3.0,
            date(2026, 7, 30),
        ),
        (
            "KXPAYROLLS-26NOV-T90000",
            "KXPAYROLLS",
            "payrolls_delta",
            "PAYEMS",
            90000,
            date(2026, 11, 1),
        ),
        ("KXU3-26NOV-T4.5", "KXU3", "unemployment_rate", "UNRATE", 4.5, date(2026, 11, 1)),
        ("KXFED-27APR-T3.75", "KXFED", "fed_upper_bound", "DFEDTARU", 3.75, date(2027, 4, 1)),
    ],
)
def test_ticker_parsing_extracts_expected_fields(
    monkeypatch,
    ticker: str,
    series: str,
    metric_id: str,
    fred_series_id: str,
    threshold: float,
    target_date: date,
) -> None:
    monkeypatch.setattr("services.models.macro.estimator.get_latest_model", lambda name: None)
    estimator = MacroEstimator()

    parsed = estimator._parse_ticker(ticker)

    assert parsed is not None
    assert parsed["series"] == series
    assert parsed["metric_id"] == metric_id
    assert parsed["fred_series_id"] == fred_series_id
    assert parsed["threshold"] == pytest.approx(threshold)
    assert parsed["direction"] == "greater"
    assert parsed["target_date"] == target_date


def test_derived_macro_metric_computation() -> None:
    cpi_rows = [(date(2025, month, 1), 100 + month) for month in range(1, 13)]
    cpi_rows += [(date(2026, 1, 1), 114.0), (date(2026, 2, 1), 115.0)]
    payroll_rows = [(date(2026, 1, 1), 159000.0), (date(2026, 2, 1), 159125.0)]
    gdp_rows = [(date(2026, 4, 1), 2.4)]
    unrate_rows = [(date(2026, 6, 1), 4.5)]
    fed_rows = [(date(2026, 7, 22), 4.25)]

    assert derive_metric_observations(cpi_rows, "cpi_mom")[-1][1] == pytest.approx(
        (115 / 114 - 1) * 100
    )
    assert derive_metric_observations(cpi_rows, "cpi_yoy")[-1][1] == pytest.approx(
        (115 / 102 - 1) * 100
    )
    assert derive_metric_observations(payroll_rows, "payrolls_delta")[-1] == (
        date(2026, 2, 1),
        125.0,
    )
    assert derive_metric_observations(gdp_rows, "gdp_annualized") == gdp_rows
    assert derive_metric_observations(unrate_rows, "unemployment_rate") == unrate_rows
    assert derive_metric_observations(fed_rows, "fed_upper_bound") == fed_rows


def test_trend_feature_computation() -> None:
    observations = [
        (date(2026, 1, 1), 1.0),
        (date(2026, 2, 1), 2.0),
        (date(2026, 3, 1), 3.0),
        (date(2026, 4, 1), 4.0),
    ]

    features = compute_trend_features(observations, as_of=date(2026, 6, 1))

    assert features is not None
    assert features["latest_value"] == pytest.approx(4.0)
    assert features["value_3mo_ago"] == pytest.approx(1.0)
    assert features["trend_slope"] == pytest.approx(1.0)
    assert features["months_since_last_release"] == pytest.approx(2.0)
    assert features["series_volatility"] == pytest.approx(1.11803398875)


def test_estimator_returns_none_with_insufficient_history(monkeypatch) -> None:
    class FakeModel:
        def predict(self, values):
            return [0.5]

    monkeypatch.setattr("services.models.macro.estimator.get_latest_model", lambda name: None)
    estimator = MacroEstimator()
    estimator.model = FakeModel()
    monkeypatch.setattr(
        estimator,
        "_fetch_series_rows",
        lambda series_id: [{"observation_date": "2026-01-01", "value": 100.0}],
    )

    assert estimator.estimate(make_market("KXCPI-26SEP-T0.6"), make_features()) is None


def test_estimator_clips_output_to_valid_range(monkeypatch) -> None:
    class FakeModel:
        def predict(self, values):
            return [1.5]

    rows = [
        {"observation_date": f"2025-{month:02d}-01", "value": 100 + month}
        for month in range(1, 13)
    ]
    rows += [
        {"observation_date": "2026-01-01", "value": 114.0},
        {"observation_date": "2026-02-01", "value": 115.0},
        {"observation_date": "2026-03-01", "value": 116.0},
    ]
    monkeypatch.setattr("services.models.macro.estimator.get_latest_model", lambda name: None)
    estimator = MacroEstimator()
    estimator.model = FakeModel()
    monkeypatch.setattr(estimator, "_fetch_series_rows", lambda series_id: rows)

    assert estimator.estimate(make_market("KXCPIYOY-26AUG-T2.5"), make_features()) == 0.99


def test_clean_observations_sorts_and_skips_nulls() -> None:
    rows = [
        {"observation_date": "2026-02-01", "value": Decimal("2.0")},
        {"observation_date": "2026-01-01", "value": None},
        {"observation_date": "2026-01-15", "value": 1.5},
    ]

    assert clean_observations(rows) == [(date(2026, 1, 15), 1.5), (date(2026, 2, 1), 2.0)]
