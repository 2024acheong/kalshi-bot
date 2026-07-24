from __future__ import annotations

import asyncio
import logging
import os

from _bootstrap import add_repo_paths

add_repo_paths()

from services.models.weather.market_outcomes import collect_weather_market_outcomes
from services.models.weather.outcomes import backfill_ncei_daily_summary_outcomes
from services.models.weather.train import train_weather_model


def _weather_cities() -> list[str]:
    configured = os.getenv("WEATHER_OUTCOME_CITIES", "NYC")
    return [city.strip().upper() for city in configured.split(",") if city.strip()]


async def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    for city in _weather_cities():
        await backfill_ncei_daily_summary_outcomes(city)
    collect_weather_market_outcomes()
    result = train_weather_model()
    logging.info(
        "Registered weather model version=%s metrics=%s",
        result["version"],
        result["metrics"],
    )


if __name__ == "__main__":
    asyncio.run(main())
