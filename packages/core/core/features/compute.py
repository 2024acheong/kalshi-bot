from core.schemas.market import MarketState


def compute_mid_price(market: MarketState) -> float | None:
    if market.yes_bid is None or market.yes_ask is None:
        return None
    return (market.yes_bid + market.yes_ask) / 2
