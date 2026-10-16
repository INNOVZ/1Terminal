from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest

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
from algotrader.domain.models import Direction


@pytest.mark.asyncio
async def test_fear_greed_client_parses_valid_payload() -> None:
    payload = {
        "name": "Fear and Greed Index",
        "data": [
            {
                "value": "22",
                "value_classification": "Extreme Fear",
                "timestamp": "1728457200",
            },
            {
                "value": "26",
                "value_classification": "Fear",
                "timestamp": "1728370800",
            },
        ],
    }

    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=json.dumps(payload))
    )
    client = AlternativeMeFearGreedClient(transport=transport)
    try:
        reading = await client.get_latest()
        assert reading is not None
        assert reading.value == 22
        assert reading.classification == "Extreme Fear"
        assert reading.is_extreme_fear is True
        assert reading.previous_value == 26
        assert reading.seven_day_average == Decimal("24")

        # Caching test: second call does not re-fetch
        reading_cached = await client.get_latest()
        assert reading_cached is reading
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_fear_greed_client_handles_http_failure_gracefully() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    client = AlternativeMeFearGreedClient(transport=transport)
    try:
        reading = await client.get_latest()
        assert reading is None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_defillama_client_aggregates_stablecoin_flows() -> None:
    payload = {
        "peggedAssets": [
            {
                "id": "1",
                "name": "Tether",
                "symbol": "USDT",
                "circulating": {"peggedUSD": 100000000000},
                "circulatingPrevDay": {"peggedUSD": 99500000000},
                "circulatingPrevWeek": {"peggedUSD": 98000000000},
                "circulatingPrevMonth": {"peggedUSD": 95000000000},
            },
            {
                "id": "2",
                "name": "USD Coin",
                "symbol": "USDC",
                "circulating": {"peggedUSD": 30000000000},
                "circulatingPrevDay": {"peggedUSD": 29800000000},
                "circulatingPrevWeek": {"peggedUSD": 29000000000},
                "circulatingPrevMonth": {"peggedUSD": 28000000000},
            },
        ]
    }
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=json.dumps(payload))
    )
    client = DefiLlamaClient(transport=transport)
    try:
        metric = await client.get_stablecoin_metrics()
        assert metric is not None
        assert metric.total_circulating_usd == Decimal("130000000000")
        assert metric.change_7d_pct > Decimal("0")
        assert metric.is_expanding_macro_liquidity is True
        assert metric.is_contracting_macro_liquidity is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_coingecko_client_parses_global_metrics() -> None:
    payload = {
        "data": {
            "market_cap_percentage": {"btc": 57.5, "eth": 13.5},
            "total_market_cap": {"usd": 2400000000000},
            "total_volume": {"usd": 85000000000},
        }
    }
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=json.dumps(payload))
    )
    client = CoinGeckoGlobalClient(transport=transport)
    try:
        metrics = await client.get_global_metrics()
        assert metrics is not None
        assert metrics.btc_dominance_pct == Decimal("57.5")
        assert metrics.total_volume_24h_usd == Decimal("85000000000")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_macro_sentiment_multiplier_calculation() -> None:
    # Extreme fear with liquidity expansion
    ctx = MacroSentimentContext(
        fear_greed=FearGreedReading(
            value=18,
            classification="Extreme Fear",
            observed_at=None,  # type: ignore[arg-type]
        ),
        stablecoin_supply=StablecoinSupplyMetric(
            total_circulating_usd=Decimal("150000000000"),
            change_1d_pct=Decimal("0.2"),
            change_7d_pct=Decimal("1.2"),
            change_30d_pct=Decimal("3.5"),
        ),
        market_breadth=GlobalMarketMetrics(
            btc_dominance_pct=Decimal("56.0"),
            btc_dominance_24h_delta=Decimal("-0.3"),
            total_market_cap_usd=Decimal("2500000000000"),
            total_volume_24h_usd=Decimal("90000000000"),
        ),
    )

    # Long setup gets positive boost: contrarian fear (+0.05) + stablecoin inflow (+0.08) + altseason breadth (+0.04) = 1.17 -> clamped to 1.15
    long_mult = ctx.multiplier_for(Direction.LONG)
    assert long_mult == Decimal("1.15")

    # Short setup gets penalized in liquidity expansion + extreme fear
    short_mult = ctx.multiplier_for(Direction.SHORT)
    assert short_mult < Decimal("1.0")

    narrative = ctx.narrative()
    assert "F&G: 18/100 (Extreme Fear)" in narrative
    assert "Stablecoins 7d: +1.20% (Inflow)" in narrative
    assert "BTC.D: 56.0%" in narrative


@pytest.mark.asyncio
async def test_macro_sentiment_provider_resilient_to_all_errors() -> None:
    mock_fg = AsyncMock()
    mock_fg.get_latest.side_effect = RuntimeError("FG timeout")
    mock_dl = AsyncMock()
    mock_dl.get_stablecoin_metrics.side_effect = TimeoutError("DL timeout")
    mock_cg = AsyncMock()
    mock_cg.get_global_metrics.side_effect = ConnectionError("CG failed")

    provider = MacroSentimentProvider(
        fear_greed_client=mock_fg,
        defillama_client=mock_dl,
        coingecko_client=mock_cg,
    )

    ctx = await provider.get_context()
    assert ctx.fear_greed is None
    assert ctx.stablecoin_supply is None
    assert ctx.market_breadth is None
    assert ctx.multiplier_for(Direction.LONG) == Decimal("1.0")
    assert ctx.multiplier_for(Direction.SHORT) == Decimal("1.0")
    assert ctx.narrative() == "Neutral (Macro Offline)"
