from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from algotrader.api import portal
from algotrader.config import load_settings
from algotrader.domain.models import AssetClass, Instrument, InstrumentId, MarketKind, Venue


def _make_inst(
    symbol: str,
    base_asset: str,
    quote_asset: str = "USDT",
    *,
    trading: bool = True,
    price_tick: str = "0.01",
) -> Instrument:
    return Instrument(
        id=InstrumentId(Venue.BINANCE_USDM, symbol),
        base_asset=base_asset,
        quote_asset=quote_asset,
        asset_class=AssetClass.CRYPTO,
        market_kind=MarketKind.FUTURES,
        trading=trading,
        price_tick=Decimal(price_tick),
        quantity_step=Decimal("0.1"),
        min_quantity=Decimal("0.1"),
        min_notional=Decimal("5.0"),
    )


@pytest.mark.asyncio
async def test_screener_universe_filters_and_deduplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    instruments = [
        _make_inst("SOLUSDC", "SOL", "USDC", price_tick="0.01"),
        _make_inst("SOLUSDT", "SOL", "USDT", price_tick="0.01"),
        _make_inst("BTCUSDT", "BTC", "USDT", price_tick="0.1"),
        _make_inst("1000PEPEUSDT", "1000PEPE", "USDT", price_tick="0.0000001"),
        _make_inst("USDCUSDT", "USDC", "USDT"),
        _make_inst("PAXGUSDT", "PAXG", "USDT"),
        _make_inst("DELISTEDUSDT", "DELISTED", "USDT", trading=False),
        _make_inst("ETHBTC", "ETH", "BTC"),
    ]

    class FakeFuturesMarket:
        venue = Venue.BINANCE_USDM

        async def instruments(self) -> list[Instrument]:
            return instruments

    fake_runtime = SimpleNamespace(
        futures_market=FakeFuturesMarket(),
        settings=load_settings(),
    )

    async def fake_get_portal_runtime(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return fake_runtime

    monkeypatch.setattr(portal, "_cached_instruments", None)
    monkeypatch.setattr(portal, "get_portal_runtime", fake_get_portal_runtime)

    resp = await portal.get_screener_universe()

    assert resp["venue"] == Venue.BINANCE_USDM.value
    assert resp["market_kind"] == "futures"
    assert resp["count"] == 3
    assert [t["symbol"] for t in resp["tokens"]] == ["1000PEPEUSDT", "BTCUSDT", "SOLUSDT"]
    sol = next(t for t in resp["tokens"] if t["base_asset"] == "SOL")
    assert sol["quote_asset"] == "USDT"
    assert sol["tick_size"] == "0.01"


@pytest.mark.asyncio
async def test_screener_universe_recovers_after_transient_fetch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = {"count": 0}

    class FlakyFuturesMarket:
        venue = Venue.BINANCE_USDM

        async def instruments(self) -> list[Instrument]:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise TimeoutError("temporary Binance timeout")
            return [_make_inst("ETHUSDT", "ETH", "USDT", price_tick="0.01")]

    fake_runtime = SimpleNamespace(
        futures_market=FlakyFuturesMarket(),
        settings=load_settings(),
    )

    async def fake_get_portal_runtime(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return fake_runtime

    monkeypatch.setattr(portal, "_cached_instruments", None)
    monkeypatch.setattr(portal, "get_portal_runtime", fake_get_portal_runtime)

    first = await portal.get_screener_universe()
    assert first["count"] == 0
    assert portal._cached_instruments is None

    second = await portal.get_screener_universe()
    assert second["count"] == 1
    assert [t["symbol"] for t in second["tokens"]] == ["ETHUSDT"]

