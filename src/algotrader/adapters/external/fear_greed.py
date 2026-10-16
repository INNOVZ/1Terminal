from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import structlog

from algotrader.adapters.external.models import FearGreedReading

log = structlog.get_logger(__name__)


class AlternativeMeFearGreedClient:
    """Async client for the Alternative.me Crypto Fear & Greed Index."""

    def __init__(
        self,
        base_url: str = "https://api.alternative.me",
        *,
        timeout_seconds: float = 4.0,
        cache_ttl_seconds: float = 900.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
            headers={"User-Agent": "atlas-algotrader/0.1"},
        )
        self._cache_ttl = cache_ttl_seconds
        self._cached_reading: FearGreedReading | None = None
        self._last_fetched_ts: float = 0.0

    async def close(self) -> None:
        await self._http.aclose()

    async def get_latest(self, *, force_refresh: bool = False) -> FearGreedReading | None:
        """Fetch latest Fear & Greed reading with caching and resilient fallback."""
        now = time.time()
        if not force_refresh and self._cached_reading is not None:
            if now - self._last_fetched_ts < self._cache_ttl:
                return self._cached_reading

        try:
            resp = await self._http.get("/fng/", params={"limit": 7, "format": "json"})
            if resp.status_code != 200:
                log.warning("fear_greed_http_error", status_code=resp.status_code)
                return self._cached_reading

            data = resp.json()
            items = data.get("data", [])
            if not items or not isinstance(items, list):
                log.warning("fear_greed_empty_response")
                return self._cached_reading

            latest = items[0]
            val = int(latest["value"])
            classification = str(latest.get("value_classification", "Neutral"))
            ts = int(latest.get("timestamp", int(now)))
            dt = datetime.fromtimestamp(ts, tz=UTC)

            prev_val: int | None = None
            if len(items) > 1 and "value" in items[1]:
                prev_val = int(items[1]["value"])

            all_vals = [int(item["value"]) for item in items if "value" in item]
            avg_7d = Decimal(str(sum(all_vals))) / Decimal(str(len(all_vals))) if all_vals else None

            reading = FearGreedReading(
                value=val,
                classification=classification,
                observed_at=dt,
                previous_value=prev_val,
                seven_day_average=avg_7d,
            )
            self._cached_reading = reading
            self._last_fetched_ts = now
            log.info("fear_greed_updated", value=val, classification=classification)
            return reading
        except Exception as exc:
            log.warning("fear_greed_fetch_failed", error=str(exc))
            return self._cached_reading
