from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from algotrader.application.screener import MultiFactorScreener
from algotrader.domain.indicators import IndicatorSnapshot, PriceLevels
from algotrader.domain.models import (
    Candle,
    Direction,
    Instrument,
    MarketKind,
    MomentumState,
    Quote,
    Venue,
)
from tests.conftest import make_instrument, trending_candles


def gaining_momentum_candles() -> list[Candle]:
    # Use a small graduated boost over the last 8 bars so that:
    # - close > momentum_reference_close (GAINING triggers)
    # - RSI stays below the exhaustion threshold (< 72 on 1h, < 70 on 4h)
    candles = trending_candles()
    for offset, index in enumerate(range(len(candles) - 8, len(candles))):
        boost = Decimal("0.02") * Decimal(offset + 1)
        candle = candles[index]
        candles[index] = replace(
            candle,
            open=candle.open + boost,
            high=candle.high + boost,
            low=candle.low + boost,
            close=candle.close + boost,
        )
    return candles


class FakeMarketData:
    venue = Venue.BINANCE_SPOT

    def __init__(
        self,
        instrument: Instrument,
        *,
        ask: Decimal = Decimal("16.80"),
        gaining_momentum: bool = True,
    ) -> None:
        self.instrument = instrument
        self.ask = ask
        self.gaining_momentum = gaining_momentum

    async def instruments(self) -> Sequence[Instrument]:
        return [self.instrument]

    async def quotes(self, instruments: Sequence[Instrument]) -> Sequence[Quote]:
        del instruments
        return [
            Quote(
                instrument_id=self.instrument.id,
                bid=self.ask - Decimal("0.01"),
                ask=self.ask,
                last=self.ask - Decimal("0.005"),
                quote_volume_24h=Decimal("100000000"),
            )
        ]

    async def candles(self, instrument: Instrument, interval: str, limit: int) -> Sequence[Candle]:
        del instrument, interval, limit
        return gaining_momentum_candles() if self.gaining_momentum else trending_candles()


async def test_spot_screener_never_creates_short_candidate(settings: object) -> None:
    spot = make_instrument()
    candidates = await MultiFactorScreener(settings).screen(FakeMarketData(spot))  # type: ignore[arg-type]
    assert candidates
    assert {candidate.direction for candidate in candidates} == {Direction.LONG}
    assert candidates[0].momentum_state is MomentumState.GAINING
    assert Decimal("70") <= candidates[0].score < Decimal("100")
    # Support (the stop side) comes from the nearest confirmed swing low, which is far
    # tighter than the 48-bar rolling minimum of 15.67 it replaced. Resistance (the target
    # side) stays on the rolling extreme, because minor swing highs break inside a trend.
    assert candidates[0].levels_from_structure
    assert candidates[0].support_price == Decimal("16.63")
    assert candidates[0].support_price > Decimal("15.67")
    assert candidates[0].resistance_price == Decimal("17.11")


async def test_futures_evaluates_both_directions_but_only_returns_qualified(
    settings: object,
) -> None:
    futures = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES)
    market = FakeMarketData(futures)
    market.venue = Venue.BINANCE_USDM
    candidates = await MultiFactorScreener(settings).screen(market)  # type: ignore[arg-type]
    assert candidates[0].direction is Direction.LONG
    assert all(candidate.score >= settings.min_candidate_score for candidate in candidates)  # type: ignore[attr-defined]


async def test_long_candidate_is_blocked_near_resistance(settings: object) -> None:
    spot = make_instrument()
    candidates = await MultiFactorScreener(settings).screen(  # type: ignore[arg-type]
        FakeMarketData(spot, ask=Decimal("17.08"))
    )
    assert candidates == []


async def test_candidate_is_blocked_when_directional_momentum_is_not_gaining(
    settings: object,
) -> None:
    spot = make_instrument()
    candidates = await MultiFactorScreener(settings).screen(  # type: ignore[arg-type]
        FakeMarketData(spot, gaining_momentum=False)
    )
    assert candidates == []


async def test_optional_4h_strength_gate_is_auditable(settings: object) -> None:
    strict = settings.model_copy(update={"min_4h_adx": Decimal("100")})  # type: ignore[attr-defined]
    spot = make_instrument()

    candidates = await MultiFactorScreener(strict).screen(
        FakeMarketData(spot), include_rejected=True
    )

    assert candidates
    assert all(candidate.score == 0 for candidate in candidates)
    assert any(
        "4h trend strength is below ADX 100" in reason
        for candidate in candidates
        for reason in candidate.reasons
    )


async def test_optional_ema_extension_gate_blocks_late_entry(settings: object) -> None:
    strict = settings.model_copy(  # type: ignore[attr-defined]
        update={"max_entry_extension_from_ema20_atr": Decimal("0.01")}
    )
    spot = make_instrument()

    candidates = await MultiFactorScreener(strict).screen(
        FakeMarketData(spot), include_rejected=True
    )

    assert any(
        "entry is too extended from the 1h EMA20" in reason
        for candidate in candidates
        for reason in candidate.reasons
    )


async def test_optional_pullback_gate_explains_missing_confirmation(settings: object) -> None:
    strict = settings.model_copy(  # type: ignore[attr-defined]
        update={
            "require_pullback_confirmation": True,
            "pullback_require_previous_bar_break": True,
        }
    )
    spot = make_instrument()

    candidates = await MultiFactorScreener(strict).screen(
        FakeMarketData(spot), include_rejected=True
    )

    assert any(
        "pullback continuation is unconfirmed" in reason
        for candidate in candidates
        for reason in candidate.reasons
    )
    assert all(candidate.continuation_trigger_evidence for candidate in candidates)


async def test_rejected_candidates_are_available_for_diagnosis(settings: object) -> None:
    """Blocking reasons are computed on every pass; they must be inspectable.

    Without this, a long/short imbalance cannot be attributed to venue coverage, market
    regime, or a specific gate -- the evidence is discarded before anyone can see it.
    """
    futures = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES)
    market = FakeMarketData(futures)
    market.venue = Venue.BINANCE_USDM
    screener = MultiFactorScreener(settings)  # type: ignore[arg-type]

    eligible = await screener.screen(market)
    everything = await screener.screen(market, include_rejected=True)

    assert len(everything) > len(eligible), "rejected candidates must surface"
    rejected = [c for c in everything if c.score == Decimal("0")]
    assert rejected, "the short direction is blocked in an uptrend"
    assert any(reason.startswith("entry blocked: ") for c in rejected for reason in c.reasons), (
        "each rejection must carry why"
    )


async def test_default_screen_never_returns_rejected_candidates(settings: object) -> None:
    """The trading cycle must not see score-zero candidates as tradable."""
    futures = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES)
    market = FakeMarketData(futures)
    market.venue = Venue.BINANCE_USDM

    candidates = await MultiFactorScreener(settings).screen(market)  # type: ignore[arg-type]

    assert all(c.score >= settings.min_candidate_score for c in candidates)  # type: ignore[attr-defined]


def _snapshot(*, price_up: bool, macd_up: bool, rsi_up: bool, adx_up: bool) -> IndicatorSnapshot:
    """A snapshot with each of the four momentum checks set independently."""
    now = datetime(2026, 8, 10, tzinfo=UTC)
    return IndicatorSnapshot(
        closed_at=now,
        momentum_reference_closed_at=now,
        close=Decimal("101") if price_up else Decimal("99"),
        momentum_reference_close=Decimal("100"),
        ema9=Decimal("100.5") if price_up else Decimal("99.5"),
        ema20=Decimal("100"),
        ema50=Decimal("99"),
        ema200=Decimal("95"),
        rsi14=Decimal("61") if rsi_up else Decimal("59"),
        momentum_reference_rsi14=Decimal("60"),
        atr14=Decimal("1"),
        adx14=Decimal("26") if adx_up else Decimal("24"),
        momentum_reference_adx14=Decimal("25"),
        plus_di=Decimal("30"),
        minus_di=Decimal("10"),
        macd_line=Decimal("1.2") if macd_up else Decimal("0.8"),
        macd_signal_line=Decimal("1.0"),
        macd_hist=Decimal("1.1") if macd_up else Decimal("0.9"),
        prev_macd_line=Decimal("1.0"),
        prev_macd_signal_line=Decimal("1.0"),
        momentum_reference_macd_hist=Decimal("1.0"),
        relative_volume=Decimal("1"),
        choppiness=Decimal("40"),
        momentum_normalized=Decimal("1.0") if price_up else Decimal("-1.0"),
        is_bullish_candle=price_up,
    )


def _state(settings: object, snap: IndicatorSnapshot) -> MomentumState:
    return MultiFactorScreener(settings)._momentum_state(snap, Direction.LONG)  # type: ignore[arg-type]


def test_gaining_and_losing_share_one_threshold(settings: object) -> None:
    """Regression: `gaining` required 4 of 4 while `losing` required only 3 of 4.

    The asymmetry meant a move had to be measurably cleaner to count as strengthening than
    as weakening. Across the liquid USD-M universe that alone left 5.4% of instruments
    `gaining` against 47.3% `losing`.
    """
    assert settings.momentum_min_rising_checks == 3  # type: ignore[attr-defined]

    three_up = _snapshot(price_up=True, macd_up=True, rsi_up=True, adx_up=False)
    three_down = _snapshot(price_up=False, macd_up=False, rsi_up=False, adx_up=True)

    assert _state(settings, three_up) is MomentumState.GAINING
    assert _state(settings, three_down) is MomentumState.LOSING


def test_strict_four_of_four_is_still_available(settings: object) -> None:
    strict = settings.model_copy(update={"momentum_min_rising_checks": 4})  # type: ignore[attr-defined]
    three_up = _snapshot(price_up=True, macd_up=True, rsi_up=True, adx_up=False)
    four_up = _snapshot(price_up=True, macd_up=True, rsi_up=True, adx_up=True)

    assert _state(strict, three_up) is MomentumState.MIXED, "4-of-4 rejects a 3-of-4 advance"
    assert _state(strict, four_up) is MomentumState.GAINING


def test_a_genuinely_mixed_move_is_never_gaining(settings: object) -> None:
    """Two-of-four must stay MIXED, or the gate stops filtering anything."""
    two_up = _snapshot(price_up=True, macd_up=True, rsi_up=False, adx_up=False)
    assert _state(settings, two_up) is MomentumState.MIXED


def _ranking_score(
    settings: object,
    one: IndicatorSnapshot,
    direction: Direction,
) -> Decimal:
    screener = MultiFactorScreener(settings)  # type: ignore[arg-type]
    instrument = make_instrument()
    quote = Quote(
        instrument_id=instrument.id,
        bid=Decimal("100"),
        ask=Decimal("100.01"),
        last=Decimal("100"),
        quote_volume_24h=Decimal("100000000"),
    )
    return screener._ranking_score(  # type: ignore[attr-defined]
        one,
        one,
        PriceLevels(support=Decimal("90"), resistance=Decimal("110")),
        quote,
        Decimal("100"),
        direction,
    )


def test_macd_ranking_rewards_only_long_directional_acceleration(settings: object) -> None:
    base = _snapshot(price_up=True, macd_up=True, rsi_up=True, adx_up=True)
    aligned = replace(
        base,
        macd_hist=Decimal("2"),
        momentum_reference_macd_hist=Decimal("1"),
    )
    conflicting = replace(
        base,
        macd_hist=Decimal("0"),
        momentum_reference_macd_hist=Decimal("1"),
    )

    assert _ranking_score(settings, aligned, Direction.LONG) - _ranking_score(
        settings, conflicting, Direction.LONG
    ) == Decimal("6")


def test_macd_ranking_rewards_only_short_directional_acceleration(settings: object) -> None:
    base = _snapshot(price_up=False, macd_up=False, rsi_up=False, adx_up=True)
    aligned = replace(
        base,
        macd_hist=Decimal("-2"),
        momentum_reference_macd_hist=Decimal("-1"),
    )
    conflicting = replace(
        base,
        macd_hist=Decimal("0"),
        momentum_reference_macd_hist=Decimal("-1"),
    )

    assert _ranking_score(settings, aligned, Direction.SHORT) - _ranking_score(
        settings, conflicting, Direction.SHORT
    ) == Decimal("6")


def test_macd_evidence_reports_a_tolerated_conflict_truthfully(settings: object) -> None:
    screener = MultiFactorScreener(settings)  # type: ignore[arg-type]
    short_with_decelerating_macd = replace(
        _snapshot(price_up=False, macd_up=False, rsi_up=False, adx_up=True),
        macd_hist=Decimal("-0.07"),
        momentum_reference_macd_hist=Decimal("-0.09"),
    )

    assert (
        screener._macd_evidence(  # type: ignore[attr-defined]
            short_with_decelerating_macd, Direction.SHORT
        )
        == "3-bar MACD conflict tolerated by momentum consensus"
    )
