from unittest.mock import AsyncMock

import pytest

from algotrader.config import Settings
from algotrader.infrastructure.runtime import build_runtime


async def test_runtime_has_no_trading_capabilities_or_startup_network_calls(monkeypatch):
    request = AsyncMock(side_effect=AssertionError("startup must not contact accounts"))
    monkeypatch.setattr("algotrader.adapters.binance.client.BinanceHttpClient.request", request)
    runtime = await build_runtime(Settings(_env_file=None))
    try:
        assert not hasattr(runtime, "system")
        assert not hasattr(runtime, "scheduler")
        for adapter in (runtime.spot_market, runtime.futures_market):
            for method in ("place_order", "account_snapshot", "cancel_order", "place_protection"):
                assert not hasattr(adapter, method)
        assert (await runtime.readiness())[0]
        request.assert_not_called()
    finally:
        await runtime.close()
    assert not (await runtime.readiness())[0]


async def test_cleanup_closes_every_client_even_after_error():
    runtime = await build_runtime(Settings(_env_file=None))
    first, second = runtime.clients
    await first.close()
    await second.close()
    first.close = AsyncMock(side_effect=RuntimeError("first failed"))
    second.close = AsyncMock()
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await runtime.close()
    second.close.assert_awaited_once()


async def test_screen_uses_only_public_market_data(monkeypatch):
    screen = AsyncMock(return_value=[])
    monkeypatch.setattr("algotrader.application.crypto_screener.CryptoOpportunityScreener.screen", screen)
    runtime = await build_runtime(Settings(_env_file=None))
    try:
        assert await runtime.crypto_screen() == []
        assert screen.call_args.args[0] is runtime.futures_market
        assert screen.call_args.kwargs["derivatives_port"] is runtime.futures_market
    finally:
        await runtime.close()


async def test_macro_provider_is_owned_and_closed_by_runtime():
    runtime = await build_runtime(Settings(_env_file=None))
    await runtime.macro_provider.close()
    close = AsyncMock()
    runtime.macro_provider.close = close
    await runtime.close()
    await runtime.close()
    close.assert_awaited_once()
