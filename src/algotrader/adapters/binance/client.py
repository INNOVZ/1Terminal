from __future__ import annotations

import asyncio
import hashlib
import hmac
import math
import random
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog
from pydantic import SecretStr

from algotrader.domain.errors import (
    ConfigurationError,
    OrderRejected,
    RateLimitError,
    UnknownExecutionStatus,
    VenueError,
)

log = structlog.get_logger(__name__)


class BinanceHttpClient:
    """Small signed REST client with deliberately conservative retry semantics."""

    def __init__(
        self,
        base_url: str,
        api_key: SecretStr,
        api_secret: SecretStr,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10.0,
        max_rate_limit_retries: int = 3,
        rate_limit_backoff_seconds: float = 1.0,
        max_rate_limit_backoff_seconds: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._max_rate_limit_retries = max_rate_limit_retries
        self._rate_limit_backoff_seconds = rate_limit_backoff_seconds
        self._max_rate_limit_backoff_seconds = max_rate_limit_backoff_seconds
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
            headers={"User-Agent": "atlas-algotrader/0.1"},
        )
        self._clock_offset_ms = 0
        self._used_weight_1m = 0
        self._weight_updated_at = 0.0

    @property
    def used_weight_1m(self) -> int:
        return self._used_weight_1m

    async def close(self) -> None:
        await self._http.aclose()

    async def sync_clock(self, time_path: str) -> None:
        started = time.time_ns() // 1_000_000
        payload = await self.request("GET", time_path)
        finished = time.time_ns() // 1_000_000
        midpoint = (started + finished) // 2
        self._clock_offset_ms = int(payload["serverTime"]) - midpoint

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        signed: bool = False,
        order_submission: bool = False,
    ) -> Any:
        """Issue a request, retrying only on rate limits.

        Rate limits are the one venue response that is safe to retry: the request was
        rejected outright, so no order state was created. Everything else keeps the original
        conservative posture -- a timeout or 5xx on an order submission still raises
        ``UnknownExecutionStatus`` and is never retried, because the venue may have accepted
        it. Order submissions are excluded from retry entirely: a 429 on submission is
        safe in principle, but re-sending is not worth the ambiguity when the caller can
        reconcile by client order ID.
        """
        attempt = 0
        while True:
            try:
                return await self._attempt(
                    method, path, params, signed, order_submission=order_submission
                )
            except RateLimitError as exc:
                attempt += 1
                if order_submission or attempt > self._max_rate_limit_retries:
                    raise
                venue_delay = exc.retry_after_seconds
                delay = (
                    min(venue_delay, self._max_rate_limit_backoff_seconds)
                    if venue_delay is not None and math.isfinite(venue_delay) and venue_delay > 0
                    else min(
                        self._rate_limit_backoff_seconds * (2 ** (attempt - 1)),
                        self._max_rate_limit_backoff_seconds,
                    )
                )
                delay += random.uniform(0.1, 1.0)  # Desynchronize workers (avoid thundering herd)
                log.warning(
                    "binance_rate_limited",
                    path=path,
                    attempt=attempt,
                    sleeping_seconds=round(delay, 2),
                )
                await asyncio.sleep(delay)

    async def _attempt(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None,
        signed: bool,
        *,
        order_submission: bool,
    ) -> Any:
        request_params = {key: value for key, value in (params or {}).items() if value is not None}
        headers: dict[str, str] = {}
        if signed:
            self._sign_request(request_params, headers)

        # Real-time Adaptive Backpressure: time-decayed weight based on Binance 1-minute rolling window
        now = time.time()
        elapsed = now - self._weight_updated_at
        effective_weight = 0
        if elapsed < 60.0 and self._used_weight_1m > 0:
            effective_weight = int(self._used_weight_1m * (1.0 - elapsed / 60.0))

        if effective_weight >= 2100:
            # Danger zone (> 87% of 2400 ceiling): brief pause to let window drain
            await asyncio.sleep(0.3)
        elif effective_weight >= 1800:
            # High load (> 75%): light micro-spacing
            await asyncio.sleep(0.04)

        try:
            response = await self._http.request(
                method, path, params=request_params, headers=headers
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if order_submission:
                raise UnknownExecutionStatus(
                    "order request failed at transport level; reconcile by client order ID"
                ) from exc
            raise VenueError(f"Binance transport failure: {exc}") from exc

        return self._parse_response(response, order_submission=order_submission)

    def _sign_request(
        self,
        request_params: dict[str, Any],
        headers: dict[str, str],
    ) -> None:
        api_key = self._api_key.get_secret_value()
        secret = self._api_secret.get_secret_value()
        if not api_key or not secret:
            raise ConfigurationError("Binance credentials are required for signed requests")
        request_params["timestamp"] = time.time_ns() // 1_000_000 + self._clock_offset_ms
        request_params.setdefault("recvWindow", 5000)
        query = urlencode(request_params)
        request_params["signature"] = hmac.new(
            secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        headers["X-MBX-APIKEY"] = api_key

    def _parse_response(self, response: httpx.Response, *, order_submission: bool) -> Any:
        weight_hdr = response.headers.get("x-mbx-used-weight-1m")
        if weight_hdr:
            try:
                self._used_weight_1m = int(weight_hdr)
                self._weight_updated_at = time.time()
            except (ValueError, TypeError):
                pass

        if response.status_code in {418, 429}:
            retry_after = response.headers.get("Retry-After")
            try:
                retry_after_seconds = float(retry_after) if retry_after else None
            except ValueError:
                retry_after_seconds = None
            raise RateLimitError(
                f"Binance rate limit response {response.status_code}",
                retry_after_seconds,
            )
        if response.status_code >= 500:
            if order_submission:
                raise UnknownExecutionStatus(
                    f"Binance returned {response.status_code}; order state is unknown"
                )
            raise VenueError(f"Binance server error {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise VenueError("Binance returned a non-JSON response") from exc
        if response.is_error:
            code = (
                payload.get("code", response.status_code)
                if isinstance(payload, dict)
                else response.status_code
            )
            message = (
                payload.get("msg", str(payload)) if isinstance(payload, dict) else str(payload)
            )
            error = f"Binance error {code}: {message}"
            if order_submission:
                raise OrderRejected(error)
            raise VenueError(error)
        return payload


def decimal_text(value: object) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text
