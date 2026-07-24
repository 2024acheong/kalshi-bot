from __future__ import annotations

import asyncio
import logging
import os

from _bootstrap import add_repo_paths

add_repo_paths()

from services.models.weather.estimator import CITY_COORDINATES
from services.models.weather.ingestion import run_ingestion_for_locations


def _unique_locations() -> list[tuple[float, float]]:
    return sorted(set(CITY_COORDINATES.values()))


async def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    await run_ingestion_for_locations(_unique_locations())


if __name__ == "__main__":
    asyncio.run(main())
