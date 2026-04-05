from abc import ABC, abstractmethod

from core.schemas.market import MarketState


class BaseStrategy(ABC):
    @abstractmethod
    def evaluate(self, market: MarketState) -> str:
        raise NotImplementedError
