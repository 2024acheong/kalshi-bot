from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.schemas import MarketState


@dataclass
class AlertEvent:
    level: str
    code: str
    message: str
    ticker: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "ticker": self.ticker,
        }


class MarketMonitor:
    def __init__(
        self,
        *,
        poll_interval_seconds: float,
        staleness_threshold_seconds: float,
        gap_alert_factor: float,
        logger: logging.Logger | None = None,
    ) -> None:
        self._poll_interval_seconds = poll_interval_seconds
        self._staleness_threshold_seconds = staleness_threshold_seconds
        self._gap_alert_factor = gap_alert_factor
        self._logger = logger or logging.getLogger(__name__)
        self._last_poll_completed_at: datetime | None = None

    def on_poll_started(self, started_at: datetime) -> list[AlertEvent]:
        alerts: list[AlertEvent] = []
        if self._last_poll_completed_at is not None:
            elapsed = (started_at - self._last_poll_completed_at).total_seconds()
            expected_max = self._poll_interval_seconds * self._gap_alert_factor
            if elapsed > expected_max:
                alerts.append(
                    AlertEvent(
                        level="warning",
                        code="poll_gap_detected",
                        message=(
                            f"REST polling gap detected: {elapsed:.1f}s since previous successful poll "
                            f"(threshold {expected_max:.1f}s)"
                        ),
                    )
                )
        return alerts

    def on_poll_completed(self, completed_at: datetime) -> None:
        self._last_poll_completed_at = completed_at

    def evaluate_market(self, market: MarketState, now: datetime) -> list[AlertEvent]:
        age = (now - market.timestamp.astimezone(timezone.utc)).total_seconds()
        if age <= self._staleness_threshold_seconds:
            return []
        return [
            AlertEvent(
                level="warning",
                code="stale_market_data",
                ticker=market.ticker,
                message=(
                    f"Market {market.ticker} is stale: last update age {age:.1f}s exceeds "
                    f"{self._staleness_threshold_seconds:.1f}s"
                ),
            )
        ]

    def emit(self, alerts: list[AlertEvent]) -> None:
        for alert in alerts:
            log = self._logger.warning if alert.level == "warning" else self._logger.info
            log("%s: %s", alert.code, alert.message)
