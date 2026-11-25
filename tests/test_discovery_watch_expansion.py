"""Tests for ALFA v3.3 Stage-0 sub-$1.2M activity watch expansion (200/75/45 = 320)."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from algotrader.adapters.external.models import (
    MacroSentimentContext,
)
from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.config import Settings
from algotrader.domain.crypto_screener_models import ScreenerMode
from algotrader.domain.discovery_selection import activity_watch_selection
from algotrader.domain.models import (
    AssetClass,
    Candle,
    Instrument,
    InstrumentId,
    MarketKind,
    Quote,
    Venue,
)


def _inst(idx: int) -> Instrument:
    sym = f"SYM{idx:03d}USDT"
    return Instrument(
        id=InstrumentId(Venue.BINANCE_USDM, sym),
        base_asset=f"SYM{idx:03d}",
        quote_asset="USDT",
        asset_class=AssetClass.CRYPTO,
        market_kind=MarketKind.FUTURES,
        trading=True,
        price_tick=Decimal("0.0001"),
        quantity_step=Decimal("1"),
        min_quantity=Decimal("1"),
        min_notional=Decimal("5"),
    )


def test_activity_watch_selection_supports_45_slot_cap() -> None:
    now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    liquid = [_inst(i) for i in range(360)]
    quotes: dict[str, Quote] = {}
    for i, inst in enumerate(liquid):
        # First 275 have >= $2M volume, next 85 have $600k-$1.1M volume
        qvol = Decimal("3000000") if i < 275 else Decimal("800000")
        quotes[inst.id.symbol] = Quote(
            instrument_id=inst.id,
            bid=Decimal("0.9995"),
            ask=Decimal("1.0005"),
            last=Decimal("1.0000"),
            quote_volume_24h=qvol,
            high_24h=Decimal("1.02"),
            low_24h=Decimal("0.96"),
            price_change_pct_24h=Decimal("3.5"),
            observed_at=now,
        )
    selected_base = liquid[:275]
    # Default max_watch_slots=20 -> 275 + 20 = 295
    res_default = activity_watch_selection(selected_base, liquid, quotes, 320, now)
    assert len(res_default) == 295
    # Expanded max_watch_slots=45 -> 275 + 45 = 320
    res_v33 = activity_watch_selection(
        selected_base, liquid, quotes, 320, now, max_watch_slots=45
    )
    assert len(res_v33) == 320


@pytest.mark.asyncio
async def test_screener_stage0_v33_allocates_200_75_45_when_enabled() -> None:
    now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    universe = [_inst(i) for i in range(400)]
    quotes_list: list[Quote] = []
    for i, inst in enumerate(universe):
        # Ranks 0..299 >= $2M volume (eligible for core & $1.2M discovery); 300..399 $750k volume (sub-$1.2M watch)
        qvol = Decimal(str(50_000_000 - i * 100_000)) if i < 300 else Decimal("750000")
        quotes_list.append(
            Quote(
                instrument_id=inst.id,
                bid=Decimal("0.9995"),
                ask=Decimal("1.0005"),
                last=Decimal("1.0000"),
                quote_volume_24h=qvol,
                high_24h=Decimal("1.02"),
                low_24h=Decimal("0.97"),
                price_change_pct_24h=Decimal("2.5"),
                observed_at=now,
            )
        )

    class DummyMarket:
        venue = Venue.BINANCE_USDM

        async def instruments(self) -> list[Instrument]:
            return universe

        async def quotes(self, _insts: object) -> list[Quote]:
            return quotes_list

        async def candles(self, _inst: Instrument, _iv: str, _lim: int) -> list[Candle]:
            return []

    class DummyMacro:
        async def get_context(self) -> MacroSentimentContext:
            return MacroSentimentContext(observed_at=now)

    s_off = Settings(_env_file=None, kinetic_ignition_v33_enabled=False)
    scr_off = CryptoOpportunityScreener(s_off, DummyMacro(), evaluated_at=now, feature_contract="discovery_v2")
    await scr_off.screen(DummyMarket(), mode=ScreenerMode.ALL, max_symbols=320)
    assert scr_off.scan_counts["core_selected"] == 243
    assert scr_off.scan_counts["discovery_selected"] == 57
    assert scr_off.scan_counts["discovery_watch_selected"] == 20
    assert scr_off.scan_counts["selected"] == 320

    s_on = Settings(_env_file=None, kinetic_ignition_v33_enabled=True)
    scr_on = CryptoOpportunityScreener(s_on, DummyMacro(), evaluated_at=now, feature_contract="discovery_v2")
    await scr_on.screen(DummyMarket(), mode=ScreenerMode.ALL, max_symbols=320)
    assert scr_on.scan_counts["core_selected"] == 200
    assert scr_on.scan_counts["discovery_selected"] == 75
    assert scr_on.scan_counts["discovery_watch_selected"] == 45
    assert scr_on.scan_counts["selected"] == 320

