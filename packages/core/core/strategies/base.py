from abc import ABC, abstractmethod
from core.schemas.market import MarketState, FeatureVector

class BaseStrategy(ABC):
    @abstractmethod
    def evaluate(
        self,
        market: MarketState,
        features: FeatureVector,
        position=None,
    ):
        raise NotImplementedError

    @property
    @abstractmethod
    def config_schema(self) -> dict:
        raise NotImplementedError