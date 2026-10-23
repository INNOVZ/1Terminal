"""Credential-free HTTP transport restricted to public Binance market data."""
from __future__ import annotations

import asyncio
import math
import random
import time
from collections.abc import Mapping
from typing import Any

import httpx

from algotrader.domain.errors import ConfigurationError, RateLimitError, VenueError

# Explicit paths prevent even GET requests from accessing account/trading endpoints.
PUBLIC_PATHS = frozenset({
    "/api/v3/time", "/api/v3/exchangeInfo", "/api/v3/ticker/24hr",
    "/api/v3/ticker/bookTicker", "/api/v3/klines", "/api/v3/depth",
    "/fapi/v1/time", "/fapi/v1/exchangeInfo", "/fapi/v1/ticker/24hr",
    "/fapi/v1/ticker/bookTicker", "/fapi/v1/klines", "/fapi/v1/depth",
    "/fapi/v1/premiumIndex", "/fapi/v1/openInterest", "/fapi/v1/fundingRate",
    "/futures/data/openInterestHist", "/futures/data/takerlongshortRatio",
})


class BinanceHttpClient:
    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10.0,
        max_rate_limit_retries: int = 5,
        rate_limit_backoff_seconds: float = 1.0,
        max_rate_limit_backoff_seconds: float = 30.0,
        max_transport_retries: int = 3,
        transport_backoff_seconds: float = 0.4,
    ) -> None:
        self._max_rate_limit_retries = max_rate_limit_retries
        self._rate_limit_backoff_seconds = rate_limit_backoff_seconds
        self._max_rate_limit_backoff_seconds = max_rate_limit_backoff_seconds
        self._max_transport_retries = max_transport_retries
        self._transport_backoff_seconds = transport_backoff_seconds
        self._used_weight_1m = 0
        self._weight_updated_at = 0.0
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
            headers={"User-Agent": "atlas-screener/0.2"},
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=50),
            follow_redirects=False,
        )

    @property
    def used_weight_1m(self) -> int:
        return self._used_weight_1m

    async def close(self) -> None:
        await self._http.aclose()

    async def request(
        self, method: str, path: str, *, params: Mapping[str, Any] | None = None
    ) -> Any:
        if method != "GET" or path not in PUBLIC_PATHS:
            raise ConfigurationError("Screener permits only allowlisted public GET endpoints")
        if any(key.lower() in {"signature", "apikey", "api_key", "secret", "timestamp", "recvwindow"} for key in (params or {})):
            raise ConfigurationError("Authenticated request parameters are not supported")
        rate_attempt = 0
        transport_attempt = 0
        while True:
            try:
                # Wait for the observed weight window to expire near its ceiling.
                if self._used_weight_1m >= 2150:
                    remaining = 60.0 - (time.monotonic() - self._weight_updated_at)
                    if remaining > 0:
                        await asyncio.sleep(remaining)
                response = await self._http.get(path, params=params)
                return self._parse_response(response)
            except RateLimitError as exc:
                rate_attempt += 1
                if rate_attempt > self._max_rate_limit_retries:
                    raise
                venue_delay = exc.retry_after_seconds
                if venue_delay is not None and math.isfinite(venue_delay) and venue_delay > 0:
                    # Never cap the exchange's explicit Retry-After.
                    delay = venue_delay
                else:
                    delay = min(
                        self._rate_limit_backoff_seconds * 2 ** (rate_attempt - 1),
                        self._max_rate_limit_backoff_seconds,
                    )
                await asyncio.sleep(delay + random.uniform(0.01, 0.05))
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                transport_attempt += 1
                if transport_attempt > self._max_transport_retries:
                    raise VenueError("Binance public market-data transport failed") from exc
                await asyncio.sleep(self._transport_backoff_seconds * 2 ** (transport_attempt - 1))

    def _parse_response(self, response: httpx.Response) -> Any:
        weight = response.headers.get("x-mbx-used-weight-1m")
        if weight:
            try:
                self._used_weight_1m = int(weight)
                self._weight_updated_at = time.monotonic()
            except ValueError:
                pass
        if response.status_code in {418, 429}:
            try:
                retry_after = float(response.headers["Retry-After"])
            except (KeyError, ValueError):
                retry_after = None
            raise RateLimitError(f"Binance rate limit response {response.status_code}", retry_after)
        if response.status_code >= 500:
            raise VenueError(f"Binance server error {response.status_code}")
        if response.is_redirect:
            raise VenueError("Unexpected redirect from Binance market-data endpoint")
        try:
            payload = response.json()
        except ValueError as exc:
            raise VenueError("Binance returned a non-JSON response") from exc
        if response.is_error:
            raise VenueError(f"Binance market-data error {response.status_code}")
        return payload
