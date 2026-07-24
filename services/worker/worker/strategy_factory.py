from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from core.execution.adapters import PaperAdapter
from core.risk.engine import RiskConfig, RiskEngine
from core.strategies.calibration_mispricing import (
    CalibrationMispricingStrategy,
    NaiveMidpointDriftEstimator,
    ProbabilityEstimator,
)
from core.strategies.event_drift import EventDriftStrategy
from core.strategies.mean_reversion import MeanReversionStrategy
from core.strategies.spread_capture import SpreadCaptureStrategy

LOGGER = logging.getLogger(__name__)

SUPPORTED_STRATEGIES = {
    "spread_capture",
    "mean_reversion",
    "event_drift",
    "calibration_mispricing_macro",
    "calibration_mispricing_weather",
}


@dataclass(frozen=True)
class StrategyRuntimeSpec:
    config_id: str
    name: str
    version: int
    params: dict[str, Any]
    strategy: Any
    risk_engine: RiskEngine
    paper_adapter: PaperAdapter


def build_strategy_runtime_spec(config: dict[str, Any]) -> StrategyRuntimeSpec:
    name = str(config["name"])
    version = int(config.get("version") or 1)
    params = dict(config.get("params_json") or {})
    if name not in SUPPORTED_STRATEGIES:
        raise ValueError(f"Unsupported strategy config: {name}")

    return StrategyRuntimeSpec(
        config_id=str(config["id"]),
        name=name,
        version=version,
        params=params,
        strategy=_build_strategy(name, params),
        risk_engine=_build_risk_engine(name, params),
        paper_adapter=PaperAdapter(),
    )


def _build_strategy(name: str, params: dict[str, Any]) -> Any:
    strategy_params = dict(params.get("strategy") or {})
    if name == "spread_capture":
        return SpreadCaptureStrategy(**strategy_params)
    if name == "mean_reversion":
        return MeanReversionStrategy(**strategy_params)
    if name == "event_drift":
        return EventDriftStrategy(**strategy_params)
    if name == "calibration_mispricing_macro":
        return CalibrationMispricingStrategy(
            estimator=_macro_estimator(),
            **strategy_params,
        )
    if name == "calibration_mispricing_weather":
        return CalibrationMispricingStrategy(
            estimator=_weather_estimator(),
            **strategy_params,
        )
    raise ValueError(f"Unsupported strategy: {name}")


def _build_risk_engine(name: str, params: dict[str, Any]) -> RiskEngine:
    risk_params = dict(params.get("risk") or {})
    if name == "spread_capture":
        risk_params = {"min_edge_to_trade": 0.003, "kelly_fraction": 0.5, **risk_params}
    return RiskEngine(config=RiskConfig(**risk_params))


def _macro_estimator() -> ProbabilityEstimator:
    try:
        from services.models.macro.estimator import MacroEstimator

        return MacroEstimator()
    except Exception as exc:
        LOGGER.warning("Falling back to naive estimator for macro calibration: %s", exc)
        return NaiveMidpointDriftEstimator()


def _weather_estimator() -> ProbabilityEstimator:
    try:
        from services.models.weather.estimator import WeatherEnsembleEstimator

        return WeatherEnsembleEstimator()
    except Exception as exc:
        LOGGER.warning("Falling back to naive estimator for weather calibration: %s", exc)
        return NaiveMidpointDriftEstimator()
