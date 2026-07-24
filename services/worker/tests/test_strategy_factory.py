from __future__ import annotations

import pytest

from core.risk.engine import RiskEngine
from core.strategies.calibration_mispricing import CalibrationMispricingStrategy
from core.strategies.event_drift import EventDriftStrategy
from core.strategies.mean_reversion import MeanReversionStrategy
from core.strategies.spread_capture import SpreadCaptureStrategy
from worker.strategy_factory import build_strategy_runtime_spec


def make_config(name: str, params: dict | None = None) -> dict:
    return {
        "id": f"config-{name}",
        "name": name,
        "version": 1,
        "params_json": params or {},
        "status": "enabled",
    }


def test_builds_spread_capture_with_custom_risk_floor() -> None:
    spec = build_strategy_runtime_spec(make_config("spread_capture"))

    assert isinstance(spec.strategy, SpreadCaptureStrategy)
    assert isinstance(spec.risk_engine, RiskEngine)
    assert spec.risk_engine.config.min_edge_to_trade == 0.003


@pytest.mark.parametrize(
    ("name", "expected_type"),
    [
        ("mean_reversion", MeanReversionStrategy),
        ("event_drift", EventDriftStrategy),
        ("calibration_mispricing_macro", CalibrationMispricingStrategy),
        ("calibration_mispricing_weather", CalibrationMispricingStrategy),
    ],
)
def test_builds_supported_strategies(monkeypatch, name: str, expected_type: type) -> None:
    if "macro" in name:
        monkeypatch.setattr("worker.strategy_factory._macro_estimator", lambda: object())
    if "weather" in name:
        monkeypatch.setattr("worker.strategy_factory._weather_estimator", lambda: object())

    spec = build_strategy_runtime_spec(make_config(name))

    assert isinstance(spec.strategy, expected_type)
    assert spec.name == name


def test_rejects_unknown_strategy() -> None:
    with pytest.raises(ValueError):
        build_strategy_runtime_spec(make_config("not_real"))
