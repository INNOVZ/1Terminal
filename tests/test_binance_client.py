from __future__ import annotations

import hashlib
import hmac
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from algotrader.adapters.binance.client import BinanceHttpClient
from algotrader.domain.errors import RateLimitError, UnknownExecutionStatus


async def test_signed_request_has_valid_hmac_and_never_exposes_secret() -> None:
    observed_query = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_query
        observed_query = request.url.query.decode()
        return httpx.Response(200, json={"ok": True})

    client = BinanceHttpClient(
        "https://example.test",
        SecretStr("public-key"),
        SecretStr("private-secret"),
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.request("GET", "/signed", params={"symbol": "BTCUSDT"}, signed=True)
    finally:
        await client.close()
    unsigned, signature = observed_query.rsplit("&signature=", 1)
    expected = hmac.new(b"private-secret", unsigned.encode(), hashlib.sha256).hexdigest()
    assert signature == expected
    assert parse_qs(observed_query)["symbol"] == ["BTCUSDT"]
    assert "private-secret" not in observed_query


async def test_order_server_error_is_unknown_not_safe_to_retry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(503, json={"msg": "unavailable"})

    client = BinanceHttpClient(
        "https://example.test",
        SecretStr("public-key"),
        SecretStr("private-secret"),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(UnknownExecutionStatus):
            await client.request("POST", "/order", order_submission=True)
    finally:
        await client.close()


class RateLimitThenOk(httpx.AsyncBaseTransport):
    """Returns 429 for the first `fails` calls, then succeeds."""

    def __init__(self, fails: int, *, retry_after: str | None = None) -> None:
        self.fails = fails
        self.calls = 0
        self._retry_after = retry_after

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.calls <= self.fails:
            headers = {"Retry-After": self._retry_after} if self._retry_after else {}
            return httpx.Response(429, json={"code": -1003}, headers=headers)
        return httpx.Response(200, json={"ok": True})


async def test_rate_limited_read_is_retried_after_backoff() -> None:
    """A 429 means the request was rejected outright, so retrying creates no order state."""
    transport = RateLimitThenOk(fails=2)
    client = BinanceHttpClient(
        "https://x",
        SecretStr(""),
        SecretStr(""),
        transport=transport,
        rate_limit_backoff_seconds=0.001,
    )
    try:
        assert await client.request("GET", "/api/v3/klines") == {"ok": True}
        assert transport.calls == 3
    finally:
        await client.close()


async def test_rate_limit_retries_are_bounded() -> None:
    transport = RateLimitThenOk(fails=99)
    client = BinanceHttpClient(
        "https://x",
        SecretStr(""),
        SecretStr(""),
        transport=transport,
        max_rate_limit_retries=2,
        rate_limit_backoff_seconds=0.001,
    )
    try:
        with pytest.raises(RateLimitError):
            await client.request("GET", "/api/v3/klines")
        assert transport.calls == 3, "initial attempt plus two retries, then give up"
    finally:
        await client.close()


async def test_order_submission_is_never_retried_on_rate_limit() -> None:
    """Retrying a submission trades a clear failure for an ambiguous one."""
    transport = RateLimitThenOk(fails=1)
    client = BinanceHttpClient(
        "https://x",
        SecretStr(""),
        SecretStr(""),
        transport=transport,
        rate_limit_backoff_seconds=0.001,
    )
    try:
        with pytest.raises(RateLimitError):
            await client.request("POST", "/api/v3/order", order_submission=True)
        assert transport.calls == 1, "the order must not be resubmitted"
    finally:
        await client.close()


async def test_retry_after_header_is_honoured() -> None:
    transport = RateLimitThenOk(fails=1, retry_after="0.002")
    client = BinanceHttpClient(
        "https://x",
        SecretStr(""),
        SecretStr(""),
        transport=transport,
        rate_limit_backoff_seconds=99.0,
    )
    try:
        # Would hang on the 99s default if the venue's own hint were ignored.
        assert await client.request("GET", "/api/v3/time") == {"ok": True}
    finally:
        await client.close()


@pytest.mark.parametrize("retry_after", ["invalid", "nan", "-10", "999999"])
async def test_retry_after_header_cannot_break_or_stall_the_client(retry_after: str) -> None:
    transport = RateLimitThenOk(fails=1, retry_after=retry_after)
    client = BinanceHttpClient(
        "https://x",
        SecretStr(""),
        SecretStr(""),
        transport=transport,
        rate_limit_backoff_seconds=0.001,
        max_rate_limit_backoff_seconds=0.002,
    )
    try:
        assert await client.request("GET", "/api/v3/time") == {"ok": True}
        assert transport.calls == 2
    finally:
        await client.close()
