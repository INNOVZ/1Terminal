from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import structlog

from algotrader.adapters.external.models import StablecoinSupplyMetric

log = structlog.get_logger(__name__)


class DefiLlamaClient:
    """Async client for DeFiLlama macro stablecoin circulating supply flows."""

    def __init__(
        self,
        base_url: str = "https://stablecoins.llama.fi",
        *,
        timeout_seconds: float = 5.0,
        cache_ttl_seconds: float = 1800.0,  # 30 minutes
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
            headers={"User-Agent": "atlas-algotrader/0.1"},
        )
        self._cache_ttl = cache_ttl_seconds
        self._cached_metric: StablecoinSupplyMetric | None = None
        self._last_fetched_ts: float = 0.0

    async def close(self) -> None:
        await self._http.aclose()

    async def get_stablecoin_metrics(self, *, force_refresh: bool = False) -> StablecoinSupplyMetric | None:
        """Aggregate total circulating USD across major stablecoins and compute 1d/7d/30d flow delta."""
        now = time.time()
        if not force_refresh and self._cached_metric is not None:
            if now - self._last_fetched_ts < self._cache_ttl:
                return self._cached_metric

        try:
            resp = await self._http.get("/stablecoins", params={"includePrices": "true"})
            if resp.status_code != 200:
                log.warning("defillama_http_error", status_code=resp.status_code)
                return self._cached_metric

            payload = resp.json()
            assets = payload.get("peggedAssets", [])
            if not isinstance(assets, list) or not assets:
                log.warning("defillama_empty_pegged_assets")
                return self._cached_metric

            total_cur = Decimal("0")
            total_1d = Decimal("0")
            total_7d = Decimal("0")
            total_30d = Decimal("0")

            # Aggregate top stablecoins
            for item in assets[:20]:
                if not isinstance(item, dict):
                    continue
                circ = item.get("circulating", {})
                prev_1d = item.get("circulatingPrevDay", {})
                prev_7d = item.get("circulatingPrevWeek", {})
                prev_30d = item.get("circulatingPrevMonth", {})

                if isinstance(circ, dict) and "peggedUSD" in circ and circ["peggedUSD"]:
                    c_val = Decimal(str(circ["peggedUSD"]))
                    total_cur += c_val

                    p1 = Decimal(str(prev_1d.get("peggedUSD", c_val))) if isinstance(prev_1d, dict) and prev_1d.get("peggedUSD") else c_val
                    p7 = Decimal(str(prev_7d.get("peggedUSD", c_val))) if isinstance(prev_7d, dict) and prev_7d.get("peggedUSD") else c_val
                    p30 = Decimal(str(prev_30d.get("peggedUSD", c_val))) if isinstance(prev_30d, dict) and prev_30d.get("peggedUSD") else c_val

                    total_1d += p1
                    total_7d += p7
                    total_30d += p30

            chg_1d = ((total_cur - total_1d) / total_1d * Decimal("100")) if total_1d > 0 else Decimal("0")
            chg_7d = ((total_cur - total_7d) / total_7d * Decimal("100")) if total_7d > 0 else Decimal("0")
            chg_30d = ((total_cur - total_30d) / total_30d * Decimal("100")) if total_30d > 0 else Decimal("0")

            metric = StablecoinSupplyMetric(
                total_circulating_usd=total_cur,
                change_1d_pct=chg_1d.quantize(Decimal("0.01")),
                change_7d_pct=chg_7d.quantize(Decimal("0.01")),
                change_30d_pct=chg_30d.quantize(Decimal("0.01")),
                observed_at=datetime.now(UTC),
            )
            self._cached_metric = metric
            self._last_fetched_ts = now
            log.info(
                "defillama_stablecoin_updated",
                total_usd_bn=float(total_cur / Decimal("1000000000")),
                change_7d_pct=float(chg_7d),
            )
            return metric
        except Exception as exc:
            log.warning("defillama_fetch_failed", error=str(exc))
            return self._cached_metric
