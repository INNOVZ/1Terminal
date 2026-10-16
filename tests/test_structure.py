from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from algotrader.domain.models import Candle, Direction
from algotrader.domain.structure import (
    SwingKind,
    TrendState,
    classify,
    swing_points,
)


def candles(closes: Sequence[str | int | Decimal]) -> list[Candle]:
    """Build candles whose highs and lows bracket each close by 10 bps."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    result: list[Candle] = []
    for index, value in enumerate(closes):
        close = Decimal(str(value))
        result.append(
            Candle(
                opened_at=start + timedelta(hours=index),
                closed_at=start + timedelta(hours=index + 1),
                open=close,
                high=close * Decimal("1.001"),
                low=close * Decimal("0.999"),
                close=close,
                volume=Decimal("1000"),
            )
        )
    return result


UPTREND_PATH = [
    100,
    102,
    104,
    106,
    108,
    110,
    108,
    106,
    112,
    118,
    124,
    130,
    126,
    122,
    140,
    160,
    180,
    172,
    165,
    200,
    240,
    280,
    300,
]

DOWNTREND_PATH = [300 - (value - 100) for value in UPTREND_PATH]


def test_swing_points_alternate_high_and_low() -> None:
    points = swing_points(candles(UPTREND_PATH))
    kinds = [point.kind for point in points]
    assert kinds, "expected confirmed pivots"
    # HH/HL analysis is only meaningful over an alternating sequence.
    for previous, current in zip(kinds, kinds[1:], strict=False):
        assert previous is not current


def test_uptrend_is_classified_from_higher_highs_and_higher_lows() -> None:
    structure = classify(candles(UPTREND_PATH))
    assert structure.trend is TrendState.UPTREND
    assert structure.higher_high and structure.higher_low
    assert structure.aligned_with(Direction.LONG)
    assert not structure.aligned_with(Direction.SHORT)

    highs = [p.price for p in structure.swings if p.kind is SwingKind.HIGH]
    lows = [p.price for p in structure.swings if p.kind is SwingKind.LOW]
    assert highs == sorted(highs), "swing highs should be ascending in an uptrend"
    assert lows == sorted(lows), "swing lows should be ascending in an uptrend"


def test_downtrend_is_classified_from_lower_highs_and_lower_lows() -> None:
    structure = classify(candles(DOWNTREND_PATH))
    assert structure.trend is TrendState.DOWNTREND
    assert structure.lower_high and structure.lower_low
    assert structure.aligned_with(Direction.SHORT)


def test_choppy_range_is_not_classified_as_a_trend() -> None:
    chop = [100, 104, 100, 96, 100, 104, 100, 96, 100, 104, 100, 96, 100]
    assert classify(candles(chop)).trend is TrendState.RANGE


def test_insufficient_history_reports_range_rather_than_guessing() -> None:
    structure = classify(candles([100, 101, 102]))
    assert structure.trend is TrendState.RANGE
    assert "insufficient" in structure.reasons[0]


def test_trailing_reference_is_the_newest_swing_low_below_price() -> None:
    structure = classify(candles(UPTREND_PATH))
    reference = structure.trailing_reference(Direction.LONG, Decimal("300"))
    assert reference is not None
    assert reference.kind is SwingKind.LOW
    # The most recent confirmed higher low, not the oldest one.
    lows = [p.price for p in structure.swings if p.kind is SwingKind.LOW]
    assert reference.price == max(lows)


def test_trailing_reference_rejects_a_swing_above_price() -> None:
    """A swing low above price means structure already broke; it is not a stop location."""
    structure = classify(candles(UPTREND_PATH))
    assert structure.trailing_reference(Direction.LONG, Decimal("50")) is None


def test_plateau_resolves_to_a_single_pivot() -> None:
    plateau = [100, 102, 104, 106, 106, 106, 104, 102, 100, 98, 100, 102, 104]
    highs = [p for p in swing_points(candles(plateau)) if p.kind is SwingKind.HIGH]
    assert len(highs) == 1, "a flat top should yield one pivot, not one per bar"


def test_break_of_structure_flags_price_beyond_the_last_swing_high() -> None:
    structure = classify(candles(UPTREND_PATH))
    # The path closes at 300, far above the last confirmed swing high of 180.
    assert structure.break_of_structure


def test_opposing_level_is_the_nearest_swing_high_above_price() -> None:
    structure = classify(candles(UPTREND_PATH))
    level = structure.opposing_level(Direction.LONG, Decimal("115"))
    assert level == Decimal("130") * Decimal("1.001")
