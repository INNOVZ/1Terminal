from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from algotrader.adapters.binance.bridge import LiveDataTestnetBridge
from algotrader.domain.models import Candle, Instrument, Quote, Venue
from tests.conftest import make_instrument


class FakeVenue:
    def __init__(
        self,
        venue: Venue,
        *,
        instruments: Sequence[Instrument],
        candle_count: int,
        price: str,
    ) -> None:
        self.venue = venue
        self._instruments = tuple(instruments)
        self._candle_count = candle_count
        self._price = Decimal(price)

    async def instruments(self) -> Sequence[Instrument]:
        return self._instruments

    async def quotes(self, instruments: Sequence[Instrument]) -> Sequence[Quote]:
        return tuple(
            Quote(
                instrument_id=item.id,
                bid=self._price - Decimal("1"),
                ask=self._price + Decimal("1"),
                last=self._price,
                quote_volume_24h=Decimal("50000000"),
                observed_at=datetime(2026, 8, 10, tzinfo=UTC),
            )
            for item in instruments
        )

    async def candles(self, instrument: Instrument, interval: str, limit: int) -> Sequence[Candle]:
        del instrument, interval
        start = datetime(2026, 1, 1, tzinfo=UTC)
        return tuple(
            Candle(
                opened_at=start + timedelta(hours=i),
                closed_at=start + timedelta(hours=i + 1),
                open=self._price,
                high=self._price,
                low=self._price,
                close=self._price,
                volume=Decimal("1"),
            )
            for i in range(min(limit, self._candle_count))
        )


BTC = make_instrument(symbol="BTCUSDT")
ONLY_ON_TESTNET = make_instrument(symbol="TESTUSDT")


def _live() -> FakeVenue:
    # Real venue: deep history, real price.
    return FakeVenue(Venue.BINANCE_SPOT, instruments=(BTC,), candle_count=500, price="60000")


def _testnet() -> FakeVenue:
    # Testnet: shallow history (~5 days of 4h bars), synthetic price, its own listing.
    return FakeVenue(
        Venue.BINANCE_SPOT,
        instruments=(BTC, ONLY_ON_TESTNET),
        candle_count=29,
        price="12345",
    )


async def test_candles_come_from_live_so_history_is_deep_enough() -> None:
    """Regression: Spot Testnet serves 29 4h candles against a 480 requirement."""
    bridge = LiveDataTestnetBridge(live=_live(), execution_venue=_testnet())

    assert len(await _testnet().candles(BTC, "4h", 485)) == 29, "testnet really is this shallow"
    assert len(await bridge.candles(BTC, "4h", 485)) == 485


async def test_quotes_come_from_live_not_from_the_synthetic_testnet_book() -> None:
    bridge = LiveDataTestnetBridge(live=_live(), execution_venue=_testnet())
    quotes = await bridge.quotes((BTC,))
    assert quotes[0].last == Decimal("60000")


async def test_instruments_come_from_the_execution_venue() -> None:
    """The screener must only ever rank symbols the execution venue can actually trade."""
    bridge = LiveDataTestnetBridge(live=_live(), execution_venue=_testnet())
    symbols = {item.id.symbol for item in await bridge.instruments()}
    assert symbols == {"BTCUSDT", "TESTUSDT"}


async def test_venue_identity_follows_the_execution_venue() -> None:
    bridge = LiveDataTestnetBridge(live=_live(), execution_venue=_testnet())
    assert bridge.venue is Venue.BINANCE_SPOT


def test_mismatched_venues_are_refused() -> None:
    mismatched = FakeVenue(Venue.BINANCE_USDM, instruments=(BTC,), candle_count=500, price="60000")
    with pytest.raises(ValueError, match="same venue"):
        LiveDataTestnetBridge(live=mismatched, execution_venue=_testnet())
