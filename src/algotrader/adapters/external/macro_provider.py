from __future__ import annotations

import asyncio

import structlog

from algotrader.adapters.external.coingecko import CoinGeckoGlobalClient
from algotrader.adapters.external.defillama import DefiLlamaClient
from algotrader.adapters.external.fear_greed import AlternativeMeFearGreedClient
from algotrader.adapters.external.models import MacroSentimentContext

log = structlog.get_logger(__name__)


class MacroSentimentProvider:
    """Orchestrates non-blocking external free market analytics providers."""

    def __init__(
        self,
        fear_greed_client: AlternativeMeFearGreedClient | None = None,
        defillama_client: DefiLlamaClient | None = None,
        coingecko_client: CoinGeckoGlobalClient | None = None,
    ) -> None:
        self._fear_greed = fear_greed_client or AlternativeMeFearGreedClient()
        self._defillama = defillama_client or DefiLlamaClient()
        self._coingecko = coingecko_client or CoinGeckoGlobalClient()

    async def close(self) -> None:
        await asyncio.gather(
            self._fear_greed.close(),
            self._defillama.close(),
            self._coingecko.close(),
            return_exceptions=True,
        )

    async def get_context(self) -> MacroSentimentContext:
        """Fetch all macro analytics concurrently with complete fault isolation."""
        fg_res, dl_res, cg_res = await asyncio.gather(
            self._fear_greed.get_latest(),
            self._defillama.get_stablecoin_metrics(),
            self._coingecko.get_global_metrics(),
            return_exceptions=True,
        )

        fg = fg_res if not isinstance(fg_res, BaseException) else None
        dl = dl_res if not isinstance(dl_res, BaseException) else None
        cg = cg_res if not isinstance(cg_res, BaseException) else None

        if isinstance(fg_res, BaseException):
            log.warning("macro_provider_fg_error", error=str(fg_res))
        if isinstance(dl_res, BaseException):
            log.warning("macro_provider_defillama_error", error=str(dl_res))
        if isinstance(cg_res, BaseException):
            log.warning("macro_provider_coingecko_error", error=str(cg_res))

        context = MacroSentimentContext(
            fear_greed=fg,
            stablecoin_supply=dl,
            market_breadth=cg,
        )
        log.info("macro_context_assembled", narrative=context.narrative())
        return context
