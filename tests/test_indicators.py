from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from algotrader.domain.indicators import (
    confirmed_rsi_divergences,
    continuation_trigger,
    ema,
    rsi,
    snapshot,
    support_resistance,
    volume_contraction_metrics,
    volume_profile,
)
from algotrader.domain.models import Candle, Direction
from tests.conftest import trending_candles


def test_ema_seeds_with_sma_like_tradingview() -> None:
    values = [Decimal("1"), Decimal("2"), Decimal("3")]
    result = ema(values, 2)
    # Pine's ta.ema seeds with sma(source, length) at index length-1, so the seed here is
    # mean(1, 2) = 1.5 rather than the first observation.
    assert result[1] == Decimal("1.5")
    assert abs(result[2] - Decimal("2.5")) < Decimal("1e-26")


def test_ema200_does_not_inherit_the_first_observation(settings: object) -> None:
    """EMA200 over the 260 bars the screener fetches must track the real level.

    Seeding from ``values[0]`` leaves (1 - 2/201)**259 ~= 7.5% weight on a single arbitrary
    close. Here that would drag EMA200 to ~96.2 against a true level of 100 -- enough to
    flip the `close > ema200` regime gate that the screener treats as a hard trend filter.
    """
    del settings
    series = [Decimal("50"), *[Decimal("100")] * 259]
    value = ema(series, 200)[-1]
    assert abs(value - Decimal("100")) < Decimal("0.2")

    seeded_from_first = Decimal("100") - Decimal("50") * (
        (Decimal("1") - Decimal("2") / Decimal("201")) ** 259
    )
    assert abs(seeded_from_first - Decimal("100")) > Decimal("3")


def test_rsi_matches_wilder_reference_seed() -> None:
    closes = [
        Decimal(value)
        for value in (
            "44.34",
            "44.09",
            "44.15",
            "43.61",
            "44.33",
            "44.83",
            "45.10",
            "45.42",
            "45.84",
            "46.08",
            "45.89",
            "46.03",
            "45.61",
            "46.28",
            "46.28",
        )
    ]

    assert abs(rsi(closes, 14)[-1] - Decimal("70.4641350211")) < Decimal("0.0000000001")


def test_rsi_divergence_waits_for_confirmed_pivots_and_does_not_repaint() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    closes = [Decimal("100")] * 30
    closes += [Decimal(value) for value in (102, 104, 108, 112, 108, 105, 104)]
    closes += [Decimal(value) for value in (105, 106, 107, 108, 109, 110, 111, 113, 110, 109, 108)]
    bars: list[Candle] = []
    previous = closes[0]
    for index, close in enumerate(closes):
        bars.append(
            Candle(
                opened_at=started + timedelta(hours=index),
                closed_at=started + timedelta(hours=index + 1),
                open=previous,
                high=max(previous, close) + Decimal("0.1"),
                low=min(previous, close) - Decimal("0.1"),
                close=close,
                volume=Decimal("1000"),
            )
        )
        previous = close

    # With only one bar to the right, the second high is not yet a confirmed pivot.
    assert not confirmed_rsi_divergences(bars[:-2], right=2, min_rsi_delta=Decimal("1"))

    divergences = confirmed_rsi_divergences(bars, right=2, min_rsi_delta=Decimal("1"))

    assert divergences
    bearish = divergences[-1]
    assert bearish.kind == "bearish"
    assert bearish.newer_price > bearish.older_price
    assert bearish.newer_rsi < bearish.older_rsi
    assert bearish.age_bars >= 2


def test_snapshot_recognizes_strong_uptrend() -> None:
    result = snapshot(trending_candles())
    assert result.ema20 > result.ema50 > result.ema200
    assert result.plus_di > result.minus_di
    assert result.rsi14 > Decimal("50")
    assert result.atr14 > 0


def test_snapshot_requires_closed_history() -> None:
    with pytest.raises(ValueError, match="210"):
        snapshot(trending_candles(count=100))


def test_support_resistance_uses_prior_closed_bars_only() -> None:
    candles = trending_candles(count=60)
    candles[-1] = replace(candles[-1], high=Decimal("999"), low=Decimal("0"))

    levels = support_resistance(candles, lookback=20)

    assert levels.support == Decimal("11.03")
    assert levels.resistance == Decimal("11.69")


def _continuation_candles(direction: Direction) -> list[Candle]:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [
        Candle(
            opened_at=started + timedelta(hours=index),
            closed_at=started + timedelta(hours=index + 1),
            open=Decimal("100"),
            high=Decimal("100.2"),
            low=Decimal("99.8"),
            close=Decimal("100"),
            volume=Decimal("1000"),
        )
        for index in range(60)
    ]
    if direction is Direction.LONG:
        bars[-2] = replace(
            bars[-2],
            open=Decimal("100"),
            high=Decimal("100.4"),
            low=Decimal("99.6"),
            close=Decimal("100.1"),
        )
        bars[-1] = replace(
            bars[-1],
            open=Decimal("100.1"),
            high=Decimal("101"),
            low=Decimal("99.8"),
            close=Decimal("100.8"),
        )
    else:
        bars[-2] = replace(
            bars[-2],
            open=Decimal("100"),
            high=Decimal("100.4"),
            low=Decimal("99.6"),
            close=Decimal("99.9"),
        )
        bars[-1] = replace(
            bars[-1],
            open=Decimal("99.9"),
            high=Decimal("100.2"),
            low=Decimal("99"),
            close=Decimal("99.2"),
        )
    return bars


@pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
def test_continuation_trigger_is_directionally_symmetric(direction: Direction) -> None:
    trigger = continuation_trigger(_continuation_candles(direction), direction)

    assert trigger.passed
    assert trigger.touched_ema20
    assert trigger.held_ema50
    assert trigger.reclaimed_ema20
    assert trigger.confirmed_previous_bar


def test_continuation_trigger_can_require_a_previous_bar_break() -> None:
    bars = _continuation_candles(Direction.LONG)
    bars[-1] = replace(bars[-1], high=Decimal("100.3"), close=Decimal("100.2"))

    ordinary = continuation_trigger(bars, Direction.LONG)
    strict = continuation_trigger(bars, Direction.LONG, require_previous_bar_break=True)

    assert ordinary.passed, "the existing three-bar composite supplies ordinary confirmation"
    assert not strict.passed
    assert "signal did not confirm through the previous bar" in strict.failures


def test_volume_profile_calculation() -> None:
    candles = trending_candles(up=True, count=50)
    vp = volume_profile(candles, bins=20)
    assert vp.val_price <= vp.poc_price <= vp.vah_price
    assert vp.total_volume > Decimal("0")
    assert vp.value_area_volume <= vp.total_volume
    assert vp.value_area_volume >= vp.total_volume * Decimal("0.65")


def test_volume_contraction_metrics() -> None:
    candles = trending_candles(up=True, count=350)
    adv_7d, adv_14d, ratio = volume_contraction_metrics(candles, 7, 14)
    assert adv_7d > Decimal("0")
    assert adv_14d > Decimal("0")
    assert ratio > Decimal("0")

