from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class WorkerSettings:
    kalshi_base_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    poll_interval_seconds: float = 5.0
    staleness_threshold_seconds: float = 30.0
    gap_alert_factor: float = 2.5
    redis_url: str | None = None
    database_url: str | None = None
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "WorkerSettings":
        return cls(
            kalshi_base_url=os.getenv(
                "KALSHI_BASE_URL",
                "https://api.elections.kalshi.com/trade-api/v2",
            ),
            poll_interval_seconds=float(os.getenv("POLL_INTERVAL_SECONDS", "5")),
            staleness_threshold_seconds=float(os.getenv("STALENESS_THRESHOLD_SECONDS", "30")),
            gap_alert_factor=float(os.getenv("POLL_GAP_ALERT_FACTOR", "2.5")),
            redis_url=os.getenv("UPSTASH_REDIS_URL") or os.getenv("REDIS_URL"),
            database_url=os.getenv("DATABASE_URL"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )
