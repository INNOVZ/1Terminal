from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from algotrader.config import Settings
from algotrader.domain.models import (
    AssetClass,
    Candle,
    Instrument,
    InstrumentId,
    MarketKind,
    Venue,
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "operator_token": "x" * 40,
            "state_db_path": tmp_path / "state.sqlite3",
            "min_quote_volume_24h": Decimal("1000"),
            "max_symbols_to_analyze": 10,
            "min_4h_history_bars": 210,
            # Set RSI exhaustion thresholds high enough for synthetic trending fixtures.
            "rsi_long_max": Decimal("75"),
            "four_hour_rsi_long_max": Decimal("75"),
        }
    )


def make_instrument(
    *,
    venue: Venue = Venue.BINANCE_SPOT,
    market_kind: MarketKind = MarketKind.SPOT,
    symbol: str = "BTCUSDT",
) -> Instrument:
    return Instrument(
        id=InstrumentId(venue, symbol),
        base_asset=symbol.removesuffix("USDT"),
        quote_asset="USDT",
        asset_class=AssetClass.CRYPTO,
        market_kind=market_kind,
        trading=True,
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal("5"),
        max_leverage=1 if market_kind is MarketKind.SPOT else 20,
    )


def trending_candles(*, up: bool = True, count: int = 260) -> list[Candle]:
    """Generate a realistic trending series with 2-up-1-down rhythm.

    Starts at price 10 so that ATR/close > 0.5% (the volatility gate).
    Produces RSI in the 60-68 range (a normal trending market).
    """
    started = datetime(2025, 1, 1, tzinfo=UTC)
    candles: list[Candle] = []
    price = Decimal("10")
    boost = Decimal("0.08")
    for index in range(count):
        # 2 advancing bars then 1 retreating bar
        change = (-boost if up else boost) if index % 3 == 2 else (boost if up else -boost)
        prev = price
        price = max(Decimal("1"), price + change)
        candles.append(
            Candle(
                opened_at=started + timedelta(hours=index),
                closed_at=started + timedelta(hours=index + 1),
                open=prev,
                high=max(prev, price) + Decimal("0.01"),
                low=min(prev, price) - Decimal("0.01"),
                close=price,
                volume=Decimal("1000"),
                quote_volume=Decimal("100000"),
                closed=True,
            )
        )
    return candles
