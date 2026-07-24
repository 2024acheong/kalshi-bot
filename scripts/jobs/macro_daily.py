from __future__ import annotations

import logging
import os

from _bootstrap import add_repo_paths

add_repo_paths()

from services.models.macro.backfill_markets import backfill_macro_markets
from services.models.macro.features import FRED_SERIES_OF_INTEREST
from services.models.macro.ingestion import run_ingestion_for_series
from services.models.macro.outcomes import collect_macro_market_outcomes
from services.models.macro.train import train_macro_model


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    fred_api_key = os.getenv("FRED_API_KEY")
    if not fred_api_key:
        raise RuntimeError("FRED_API_KEY is required for macro_daily")

    run_ingestion_for_series(sorted(FRED_SERIES_OF_INTEREST), fred_api_key)
    backfill_macro_markets(statuses=["open", "closed"])
    collect_macro_market_outcomes()
    result = train_macro_model()
    logging.info("Registered macro model version=%s metrics=%s", result["version"], result["metrics"])


if __name__ == "__main__":
    main()
