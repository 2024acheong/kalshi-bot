from pydantic import BaseModel


class MarketState(BaseModel):
    ticker: str
    yes_bid: float | None = None
    yes_ask: float | None = None
    volume: int | None = None
    status: str = "open"
