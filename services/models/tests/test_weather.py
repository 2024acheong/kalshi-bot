from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from core.schemas.market import FeatureVector, MarketState, MarketStatus
from services.models.shared.artifact_store import save_artifact
from services.models.weather.backfill_markets import (
    backfill_weather_markets,
    catalog_row_from_kalshi_market,
    default_weather_series_tickers,
)
from services.models.weather.estimator import WeatherEnsembleEstimator, compute_ensemble_features
from services.models.weather.market_outcomes import (
    build_weather_market_outcome_row,
    collect_weather_market_outcomes,
    store_weather_market_outcome,
)
from services.models.weather.outcomes import (
    chunk_date_range_by_year,
    parse_ncei_daily_summary_outcome,
    parse_nws_cli_outcome,
    store_temperature_outcome,
)
from services.models.weather.train import (
    MIN_REAL_TRAINING_ROWS,
    _canonical_city_code,
    _label_from_title,
    _load_real_training_rows,
)


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
    monkeypatch.setenv("MODEL_ARTIFACT_STORAGE", "local")
    model = {"kind": "stand-in", "weights": [1, 2, 3]}

    path = save_artifact(model, "test_model", "v1")

    from services.models.shared.artifact_store import load_artifact

    assert load_artifact(path) == model


def test_estimator_returns_none_with_no_registered_model(monkeypatch) -> None:
    monkeypatch.setattr("services.models.weather.estimator.get_recent_models", lambda name: [])

    estimator = WeatherEnsembleEstimator()

    assert estimator.estimate(make_market("KXHIGHMIA-26MAR24-B80.5"), make_features()) is None


def test_estimator_returns_none_for_unparseable_ticker(monkeypatch) -> None:
    monkeypatch.setattr("services.models.weather.estimator.get_recent_models", lambda name: [])
    estimator = WeatherEnsembleEstimator()

    assert estimator._parse_ticker("FED-23DEC-T3.00") is None
    assert estimator.estimate(make_market("FED-23DEC-T3.00"), make_features()) is None


def test_ticker_parsing_extracts_expected_fields(monkeypatch) -> None:
    monkeypatch.setattr("services.models.weather.estimator.get_recent_models", lambda name: [])
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
    monkeypatch.setattr("services.models.weather.estimator.get_recent_models", lambda name: [])
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


def test_parse_ncei_daily_summary_outcome_extracts_standard_unit_temperatures() -> None:
    row = {"DATE": "2026-05-21", "TMAX": "75", "TMIN": "68"}

    outcome = parse_ncei_daily_summary_outcome(row, "NYC", "USW00094728")

    assert outcome is not None
    assert outcome.city_code == "NYC"
    assert outcome.station_id == "USW00094728"
    assert outcome.outcome_date == date(2026, 5, 21)
    assert outcome.high_temp_f == pytest.approx(75)
    assert outcome.low_temp_f == pytest.approx(68)
    assert outcome.source == "ncei_daily_summaries"
    assert outcome.source_product_id == "ncei:daily-summaries:USW00094728"


def test_parse_ncei_daily_summary_outcome_allows_one_missing_temperature() -> None:
    row = {"DATE": "2026-05-21", "TMAX": "75"}

    outcome = parse_ncei_daily_summary_outcome(row, "NYC", "USW00094728")

    assert outcome is not None
    assert outcome.high_temp_f == pytest.approx(75)
    assert outcome.low_temp_f is None


def test_chunk_date_range_by_year_splits_multi_year_ranges() -> None:
    chunks = chunk_date_range_by_year(date(2024, 12, 30), date(2026, 1, 2))

    assert chunks == [
        (date(2024, 12, 30), date(2024, 12, 31)),
        (date(2025, 1, 1), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 1, 2)),
    ]


def test_chunk_date_range_by_year_rejects_invalid_ranges() -> None:
    with pytest.raises(ValueError, match="start_date"):
        chunk_date_range_by_year(date(2026, 1, 2), date(2026, 1, 1))


def test_store_temperature_outcome_uses_outcome_source(monkeypatch) -> None:
    class FakeExecute:
        data = [{"id": 1}]

    class FakeTable:
        def __init__(self) -> None:
            self.payload = None
            self.conflict = None

        def upsert(self, payload, on_conflict):
            self.payload = payload
            self.conflict = on_conflict
            return self

        def execute(self):
            return FakeExecute()

    class FakeClient:
        def __init__(self) -> None:
            self.table_name = None
            self.table_obj = FakeTable()

        def table(self, name):
            self.table_name = name
            return self.table_obj

    fake = FakeClient()
    monkeypatch.setattr("services.models.weather.outcomes.get_supabase_client", lambda: fake)
    outcome = parse_ncei_daily_summary_outcome(
        {"DATE": "2026-05-21", "TMAX": "75", "TMIN": "68"},
        "NYC",
        "USW00094728",
    )
    assert outcome is not None

    assert store_temperature_outcome(outcome) == 1

    assert fake.table_name == "actual_temperature_outcomes"
    assert fake.table_obj.conflict == "city_code,outcome_date"
    assert fake.table_obj.payload["source"] == "ncei_daily_summaries"
    assert fake.table_obj.payload["station_id"] == "USW00094728"
    assert fake.table_obj.payload["source_product_id"] == "ncei:daily-summaries:USW00094728"


def test_label_from_title_handles_greater_less_and_between() -> None:
    assert _label_from_title("Will the **high temp in NYC** be >75°?", 76) == (1, 75)
    assert _label_from_title("Will the **high temp in NYC** be <68°?", 68) == (0, 68)
    assert _label_from_title("Will the **high temp in NYC** be 74-75°?", 75) == (1, 74.5)


def test_training_city_alias_matches_nyc_outcomes_to_ny_tickers() -> None:
    assert _canonical_city_code("NYC") == "NY"
    assert _canonical_city_code("NY") == "NY"


def test_build_weather_market_outcome_row_labels_threshold_market() -> None:
    market = {
        "ticker": "KXHIGHNY-26MAY21-T75",
        "title": "Will the high temp in NYC be >75°?",
        "status": "resolved",
        "close_time": "2026-05-22T04:00:00Z",
    }
    actual = {
        "city_code": "NYC",
        "outcome_date": "2026-05-21",
        "high_temp_f": 76,
        "low_temp_f": 68,
    }

    outcome = build_weather_market_outcome_row(market, actual)

    assert outcome is not None
    assert outcome["ticker"] == "KXHIGHNY-26MAY21-T75"
    assert outcome["city_code"] == "NY"
    assert outcome["strike_type"] == "greater"
    assert outcome["threshold_f"] == pytest.approx(75)
    assert outcome["actual_value_f"] == pytest.approx(76)
    assert outcome["yes_resolved"] is True


def test_build_weather_market_outcome_row_labels_between_market() -> None:
    market = {
        "ticker": "KXLOWNY-26MAY21-B67.5",
        "title": "Will the low temp in NYC be 67-68°?",
        "status": "resolved",
    }
    actual = {
        "city_code": "NY",
        "outcome_date": date(2026, 5, 21),
        "high_temp_f": 76,
        "low_temp_f": 68,
    }

    outcome = build_weather_market_outcome_row(market, actual)

    assert outcome is not None
    assert outcome["strike_type"] == "between"
    assert outcome["threshold_f"] == pytest.approx(67.5)
    assert outcome["lower_f"] == pytest.approx(67)
    assert outcome["upper_f"] == pytest.approx(68)
    assert outcome["yes_resolved"] is True


def test_build_weather_market_outcome_row_returns_none_for_ambiguous_threshold() -> None:
    market = {
        "ticker": "KXHIGHNY-26MAY21-T75",
        "title": "Will the high temp in NYC settle at 75°?",
        "status": "resolved",
    }
    actual = {
        "city_code": "NY",
        "outcome_date": "2026-05-21",
        "high_temp_f": 76,
        "low_temp_f": 68,
    }

    assert build_weather_market_outcome_row(market, actual) is None


def test_store_weather_market_outcome_upserts_by_ticker(monkeypatch) -> None:
    class FakeExecute:
        data = [{"id": 1}]

    class FakeTable:
        def __init__(self) -> None:
            self.payload = None
            self.conflict = None

        def upsert(self, payload, on_conflict):
            self.payload = payload
            self.conflict = on_conflict
            return self

        def execute(self):
            return FakeExecute()

    class FakeClient:
        def __init__(self) -> None:
            self.table_name = None
            self.table_obj = FakeTable()

        def table(self, name):
            self.table_name = name
            return self.table_obj

    fake = FakeClient()
    monkeypatch.setattr(
        "services.models.weather.market_outcomes.get_supabase_client",
        lambda: fake,
    )
    row = {
        "ticker": "KXHIGHNY-26MAY21-T75",
        "series": "KXHIGHNY",
        "kind": "HIGH",
        "city_code": "NY",
        "target_date": "2026-05-21",
        "strike_type": "greater",
        "threshold_f": 75,
        "actual_value_f": 76,
        "yes_resolved": True,
    }

    assert store_weather_market_outcome(row) == 1

    assert fake.table_name == "weather_market_outcomes"
    assert fake.table_obj.conflict == "ticker"
    assert fake.table_obj.payload["ticker"] == "KXHIGHNY-26MAY21-T75"


def test_weather_catalog_row_maps_settled_market_to_resolved() -> None:
    row = catalog_row_from_kalshi_market(
        {
            "ticker": "KXHIGHNY-26MAY21-T75",
            "title": "Will the high temp in NYC be >75°?",
            "category": "weather",
            "close_time": "2026-05-22T03:59:00Z",
            "status": "settled",
            "updated_time": "2026-05-22T04:01:00Z",
        }
    )

    assert row["ticker"] == "KXHIGHNY-26MAY21-T75"
    assert row["status"] == "resolved"
    assert row["category"] == "weather"
    assert row["close_time"] == "2026-05-22T03:59:00+00:00"


def test_backfill_weather_markets_fetches_and_upserts_unique_rows(monkeypatch) -> None:
    fetched = {
        ("KXHIGHNY", "closed"): [
            {
                "ticker": "KXHIGHNY-26MAY21-T75",
                "title": "Will the high temp in NYC be >75°?",
                "status": "closed",
            }
        ],
        ("KXHIGHNY", "settled"): [
            {
                "ticker": "KXHIGHNY-26MAY21-T75",
                "title": "Will the high temp in NYC be >75°?",
                "status": "settled",
                "result": "yes",
            }
        ],
    }
    upserted_rows = []

    monkeypatch.setattr(
        "services.models.weather.backfill_markets.fetch_kalshi_markets",
        lambda series, status: fetched.get((series, status), []),
    )
    monkeypatch.setattr(
        "services.models.weather.backfill_markets.upsert_market_catalog_rows",
        lambda rows: upserted_rows.extend(rows) or len(rows),
    )

    stored = backfill_weather_markets(
        series_tickers=["KXHIGHNY"],
        statuses=["closed", "settled"],
    )

    assert stored == 1
    assert len(upserted_rows) == 1
    assert upserted_rows[0]["ticker"] == "KXHIGHNY-26MAY21-T75"
    assert upserted_rows[0]["status"] == "resolved"


def test_default_weather_series_tickers_include_supported_high_low_markets() -> None:
    series = default_weather_series_tickers()

    assert "KXHIGHNY" in series
    assert "KXLOWNY" in series
    assert "KXHIGHMIA" in series
    assert "KXLOWMIA" in series


def test_collect_weather_market_outcomes_matches_catalog_to_actuals(monkeypatch) -> None:
    stored_rows = []

    monkeypatch.setattr(
        "services.models.weather.market_outcomes._fetch_temperature_outcomes",
        lambda: [
            {
                "city_code": "NYC",
                "outcome_date": "2026-05-21",
                "high_temp_f": 76,
                "low_temp_f": 68,
            }
        ],
    )
    monkeypatch.setattr(
        "services.models.weather.market_outcomes.store_weather_market_outcome",
        lambda row: stored_rows.append(row) or 1,
    )

    stored = collect_weather_market_outcomes(
        [
            {
                "ticker": "KXHIGHNY-26MAY21-T75",
                "title": "Will the high temp in NYC be >75°?",
                "status": "resolved",
            }
        ]
    )

    assert stored == 1
    assert stored_rows[0]["yes_resolved"] is True


def test_load_real_weather_training_rows_uses_market_outcomes(monkeypatch) -> None:
    class FakeExecute:
        def __init__(self, data):
            self.data = data

    class FakeQuery:
        def __init__(self, table_name: str):
            self.table_name = table_name

        def select(self, columns):
            return self

        def limit(self, count):
            return self

        def execute(self):
            if self.table_name == "weather_market_outcomes":
                rows = []
                for index in range(MIN_REAL_TRAINING_ROWS):
                    threshold = 75 if index % 2 == 0 else 95
                    rows.append(
                        {
                            "ticker": f"KXHIGHNY-26MAY21-T{threshold}",
                            "kind": "HIGH",
                            "city_code": "NY",
                            "target_date": "2026-05-21",
                            "strike_type": "greater",
                            "threshold_f": threshold,
                            "lower_f": None,
                            "upper_f": None,
                            "yes_resolved": index % 2 == 0,
                        }
                    )
                return FakeExecute(rows)

            snapshots = []
            for member, temperature in enumerate([24.0, 25.0, 26.0], start=1):
                snapshots.append(
                    {
                        "location_lat": 40.783,
                        "location_lon": -73.967,
                        "forecast_issued_at": "2026-05-20T00:00:00+00:00",
                        "target_datetime": "2026-05-21T12:00:00+00:00",
                        "ensemble_member": member,
                        "temperature_c": temperature,
                    }
                )
            return FakeExecute(snapshots)

    class FakeClient:
        def table(self, table_name):
            return FakeQuery(table_name)

    monkeypatch.setattr("services.models.weather.train.get_supabase_client", lambda: FakeClient())

    rows = _load_real_training_rows()

    assert rows is not None
    x_values, y_values = rows
    assert len(x_values) == MIN_REAL_TRAINING_ROWS
    assert set(y_values.tolist()) == {0, 1}


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
    monkeypatch.setattr("services.models.weather.estimator.get_recent_models", lambda name: [])
    estimator = WeatherEnsembleEstimator()
    estimator.model = FakeModel()
    monkeypatch.setattr(estimator, "_fetch_latest_ensemble_rows", lambda parsed, as_of: rows)
    monkeypatch.setattr(estimator, "_resolve_threshold_strike_type", lambda ticker: "greater")

    assert estimator.estimate(make_market("KXHIGHMIA-26MAR24-T80"), make_features()) == 0.99
