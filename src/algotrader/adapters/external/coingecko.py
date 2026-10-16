from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import structlog

from algotrader.adapters.external.models import GlobalMarketMetrics

log = structlog.get_logger(__name__)


class CoinGeckoGlobalClient:
    """Async client for CoinGecko global market breadth (BTC dominance, total market cap)."""

    def __init__(
        self,
        base_url: str = "https://api.coingecko.com/api/v3",
        *,
        timeout_seconds: float = 4.0,
        cache_ttl_seconds: float = 900.0,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"User-Agent": "atlas-algotrader/0.1"}
        if api_key:
            headers["x-cg-demo-api-key"] = api_key

        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
            headers=headers,
            transport=transport,
        )
        self._cache_ttl = cache_ttl_seconds
        self._cached_metrics: GlobalMarketMetrics | None = None
        self._last_fetched_ts: float = 0.0
        self._prev_btc_dominance: Decimal | None = None

    async def close(self) -> None:
        await self._http.aclose()

    async def get_global_metrics(self, *, force_refresh: bool = False) -> GlobalMarketMetrics | None:
        """Fetch global crypto metrics: BTC dominance, market cap change, and breadth."""
        now = time.time()
        if not force_refresh and self._cached_metrics is not None:
            if now - self._last_fetched_ts < self._cache_ttl:
                return self._cached_metrics

        try:
            resp = await self._http.get("/global")
            if resp.status_code != 200:
                log.warning("coingecko_http_error", status_code=resp.status_code)
                return self._cached_metrics

            payload = resp.json()
            data = payload.get("data", {})
            if not isinstance(data, dict):
                log.warning("coingecko_invalid_payload")
                return self._cached_metrics

            mcap_pct = data.get("market_cap_percentage", {})
            btc_dom = Decimal(str(mcap_pct.get("btc", "50.0")))
            tot_mcap = Decimal(str(data.get("total_market_cap", {}).get("usd", "0")))
            tot_vol = Decimal(str(data.get("total_volume", {}).get("usd", "0")))

            delta_24h = Decimal("0")
            if self._prev_btc_dominance is not None:
                delta_24h = btc_dom - self._prev_btc_dominance
            self._prev_btc_dominance = btc_dom

            metrics = GlobalMarketMetrics(
                btc_dominance_pct=btc_dom.quantize(Decimal("0.1")),
                btc_dominance_24h_delta=delta_24h.quantize(Decimal("0.1")),
                total_market_cap_usd=tot_mcap,
                total_volume_24h_usd=tot_vol,
                observed_at=datetime.now(UTC),
            )
            self._cached_metrics = metrics
            self._last_fetched_ts = now
            log.info("coingecko_global_updated", btc_dominance=float(btc_dom))
            return metrics
        except Exception as exc:
            log.warning("coingecko_fetch_failed", error=str(exc))
            return self._cached_metrics
