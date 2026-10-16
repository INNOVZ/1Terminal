from __future__ import annotations

from algotrader.adapters.external.coingecko import CoinGeckoGlobalClient
from algotrader.adapters.external.defillama import DefiLlamaClient
from algotrader.adapters.external.fear_greed import AlternativeMeFearGreedClient
from algotrader.adapters.external.macro_provider import MacroSentimentProvider
from algotrader.adapters.external.models import (
    FearGreedReading,
    GlobalMarketMetrics,
    MacroSentimentContext,
    StablecoinSupplyMetric,
)

__all__ = [
    "AlternativeMeFearGreedClient",
    "CoinGeckoGlobalClient",
    "DefiLlamaClient",
    "FearGreedReading",
    "GlobalMarketMetrics",
    "MacroSentimentContext",
    "MacroSentimentProvider",
    "StablecoinSupplyMetric",
]
