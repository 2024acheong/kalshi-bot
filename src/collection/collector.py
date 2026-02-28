import time
from datetime import datetime

from src.collection.kalshi_api import get_orderbook
from src.storage.db import insert_snapshot, init_db
from src.utils.logger import get_logger

logger = get_logger("collector")

MARKETS = [
    "INFLATION-CPI-MAR"   # replace with real IDs
]

POLL_SECONDS = 5


def parse_top(ob):
    bid = ob["bids"][0]["price"] if ob["bids"] else None
    ask = ob["asks"][0]["price"] if ob["asks"] else None

    if bid and ask:
        mid = (bid + ask) / 2
        spread = ask - bid
    else:
        mid = None
        spread = None

    return bid, ask, mid, spread


def main():
    init_db()
    logger.info("Starting collector...")

    while True:
        for market in MARKETS:
            try:
                ob = get_orderbook(market)
                bid, ask, mid, spread = parse_top(ob)

                row = (
                    datetime.utcnow().isoformat(),
                    market,
                    bid,
                    ask,
                    mid,
                    spread
                )

                insert_snapshot(row)

                logger.info(
                    f"{market} | bid={bid} ask={ask} mid={mid}"
                )

            except Exception as e:
                logger.error(e)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()