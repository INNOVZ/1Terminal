from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from algotrader.application.alt_momentum import alt_momentum_row, rank_alt_momentum
from algotrader.domain.models import Candle, MarketKind, Quote, Venue
from tests.conftest import make_instrument, trending_candles


def _quote(symbol: str) -> Quote:
    instrument = make_instrument(
        venue=Venue.BINANCE_USDM,
        market_kind=MarketKind.FUTURES,
        symbol=symbol,
    )
    return Quote(
        instrument_id=instrument.id,
        bid=Decimal("99.9"),
        ask=Decimal("100.1"),
        last=Decimal("100"),
        quote_volume_24h=Decimal("50000000"),
    )


def _alt_from_benchmark(benchmark: list[Candle], *, rising: bool) -> list[Candle]:
    result: list[Candle] = []
    direction = Decimal("1") if rising else Decimal("-1")
    for index, candle in enumerate(benchmark):
        recent = max(0, index - (len(benchmark) - 30))
        multiplier = Decimal("2") * (
            Decimal("1") + direction * Decimal(recent * recent) / Decimal("2000")
        )
        open_price = candle.open * multiplier
        close = candle.close * multiplier
        result.append(
            replace(
                candle,
                open=open_price,
                high=max(open_price, close) + Decimal("0.02"),
                low=min(open_price, close) - Decimal("0.02"),
                close=close,
                volume=Decimal("2000") + Decimal(recent * 100),
                quote_volume=close * (Decimal("2000") + Decimal(recent * 100)),
            )
        )
    return result


def test_alt_momentum_separates_token_strength_from_btc_return() -> None:
    benchmark = trending_candles(count=260)
    instrument = make_instrument(
        venue=Venue.BINANCE_USDM,
        market_kind=MarketKind.FUTURES,
        symbol="ALTUSDT",
    )

    row = alt_momentum_row(
        instrument,
        _quote("ALTUSDT"),
        _alt_from_benchmark(benchmark, rising=True),
        benchmark,
    )

    assert row.residual_return_6h > 0
    assert row.acceleration_6h > 0
    assert row.participation_ratio > 1
    assert row.phase in {"emerging_up", "confirmed_up"}


def test_alt_momentum_down_phase_is_directional_mirror() -> None:
    benchmark = trending_candles(count=260)
    instrument = make_instrument(
        venue=Venue.BINANCE_USDM,
        market_kind=MarketKind.FUTURES,
        symbol="ALTUSDT",
    )

    row = alt_momentum_row(
        instrument,
        _quote("ALTUSDT"),
        _alt_from_benchmark(benchmark, rising=False),
        benchmark,
    )

    assert row.residual_return_6h < 0
    assert row.acceleration_6h < 0
    assert row.phase in {"emerging_down", "confirmed_down"}


def test_cross_sectional_scores_rank_relative_strength_and_weakness_symmetrically() -> None:
    benchmark = trending_candles(count=260)
    base = alt_momentum_row(
        make_instrument(
            venue=Venue.BINANCE_USDM,
            market_kind=MarketKind.FUTURES,
            symbol="MIDUSDT",
        ),
        _quote("MIDUSDT"),
        _alt_from_benchmark(benchmark, rising=True),
        benchmark,
    )
    weak = replace(
        base,
        instrument=make_instrument(
            venue=Venue.BINANCE_USDM,
            market_kind=MarketKind.FUTURES,
            symbol="WEAKUSDT",
        ),
        residual_return_6h=Decimal("-0.03"),
        residual_return_24h=Decimal("-0.06"),
        residual_return_7d=Decimal("-0.10"),
        acceleration_6h=Decimal("-0.02"),
        participation_ratio=Decimal("1"),
    )
    middle = replace(
        base,
        residual_return_6h=Decimal("0"),
        residual_return_24h=Decimal("0"),
        residual_return_7d=Decimal("0"),
        acceleration_6h=Decimal("0"),
        participation_ratio=Decimal("1"),
    )
    strong = replace(
        base,
        instrument=make_instrument(
            venue=Venue.BINANCE_USDM,
            market_kind=MarketKind.FUTURES,
            symbol="STRONGUSDT",
        ),
        residual_return_6h=Decimal("0.03"),
        residual_return_24h=Decimal("0.06"),
        residual_return_7d=Decimal("0.10"),
        acceleration_6h=Decimal("0.02"),
        participation_ratio=Decimal("1"),
    )

    ranked = rank_alt_momentum((weak, middle, strong))
    by_symbol = {row.instrument.id.symbol: row for row in ranked}

    assert by_symbol["STRONGUSDT"].long_score > by_symbol["MIDUSDT"].long_score
    assert by_symbol["MIDUSDT"].long_score > by_symbol["WEAKUSDT"].long_score
    assert by_symbol["WEAKUSDT"].short_score > by_symbol["MIDUSDT"].short_score
    assert by_symbol["MIDUSDT"].short_score > by_symbol["STRONGUSDT"].short_score
    assert all(row.long_score + row.short_score == Decimal("100.00") for row in ranked)
