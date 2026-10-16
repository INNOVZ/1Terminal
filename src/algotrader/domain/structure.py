"""Swing-pivot market structure: higher highs, higher lows, and break of structure.

Rolling ``min(low)``/``max(high)`` over a fixed window is not market structure -- it is a
rolling extreme. It cannot distinguish an uptrend making higher lows from a range whose
floor happens to sit at the same price, and it produces a "resistance" level that a genuine
breakout is always sitting on top of.

This module derives *confirmed* swing pivots and classifies the sequence, which gives three
things the rolling window cannot:

1. A trend definition that fires on structure (HH + HL) rather than on a 200-period average,
   so it turns well before EMA200 alignment.
2. Support/resistance anchored to levels other traders can see, for stop placement.
3. A ratchet reference for trailing: the last confirmed higher low.

A pivot needs ``right`` bars of forward confirmation, so structure lags by exactly ``right``
closed bars. That lag is the honest price of not repainting.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from algotrader.domain.models import Candle, Direction


class SwingKind(StrEnum):
    HIGH = "high"
    LOW = "low"


class TrendState(StrEnum):
    UPTREND = "uptrend"
    DOWNTREND = "downtrend"
    RANGE = "range"


@dataclass(frozen=True, slots=True)
class SwingPoint:
    kind: SwingKind
    index: int
    price: Decimal
    closed_at: datetime


@dataclass(frozen=True, slots=True)
class MarketStructure:
    """Confirmed swing sequence and the trend it implies."""

    trend: TrendState
    swings: tuple[SwingPoint, ...]
    higher_high: bool
    higher_low: bool
    lower_high: bool
    lower_low: bool
    break_of_structure: bool
    reasons: tuple[str, ...]

    @property
    def last_swing_high(self) -> SwingPoint | None:
        return next(
            (point for point in reversed(self.swings) if point.kind is SwingKind.HIGH), None
        )

    @property
    def last_swing_low(self) -> SwingPoint | None:
        return next((point for point in reversed(self.swings) if point.kind is SwingKind.LOW), None)

    def aligned_with(self, direction: Direction) -> bool:
        return (
            self.trend is TrendState.UPTREND
            if direction is Direction.LONG
            else self.trend is TrendState.DOWNTREND
        )

    def trailing_reference(self, direction: Direction, price: Decimal) -> SwingPoint | None:
        """Most recent confirmed swing the stop can hide behind, if it is already protective.

        For a long this is the newest swing low strictly below the current price. A swing low
        above price would imply structure has already broken, and hiding a stop above the
        market would close the position at once, so it is rejected here rather than clamped.
        """
        if direction is Direction.LONG:
            return next(
                (
                    point
                    for point in reversed(self.swings)
                    if point.kind is SwingKind.LOW and point.price < price
                ),
                None,
            )
        return next(
            (
                point
                for point in reversed(self.swings)
                if point.kind is SwingKind.HIGH and point.price > price
            ),
            None,
        )

    def opposing_level(self, direction: Direction, price: Decimal) -> Decimal | None:
        """Nearest confirmed swing standing between price and open space."""
        if direction is Direction.LONG:
            highs = [
                point.price
                for point in self.swings
                if point.kind is SwingKind.HIGH and point.price > price
            ]
            return min(highs) if highs else None
        lows = [
            point.price
            for point in self.swings
            if point.kind is SwingKind.LOW and point.price < price
        ]
        return max(lows) if lows else None

    def protective_level(self, direction: Direction, price: Decimal) -> Decimal | None:
        """Nearest confirmed swing behind price, used to place the initial stop."""
        reference = self.trailing_reference(direction, price)
        return reference.price if reference is not None else None


def swing_points(
    candles: Sequence[Candle], *, left: int = 2, right: int = 2
) -> tuple[SwingPoint, ...]:
    """Confirmed fractal pivots, filtered to a strictly alternating high/low sequence."""
    if left <= 0 or right <= 0:
        raise ValueError("swing confirmation windows must be positive")
    closed = sorted(
        (candle for candle in candles if candle.closed), key=lambda item: item.closed_at
    )
    if len(closed) < left + right + 1:
        return ()

    raw: list[SwingPoint] = []
    for index in range(left, len(closed) - right):
        bar = closed[index]
        before = closed[index - left : index]
        after = closed[index + 1 : index + right + 1]
        window = (*before, bar, *after)
        if all(item.high == bar.high for item in window) and all(
            item.low == bar.low for item in window
        ):
            # A perfectly flat window is not a swing in either direction.
            continue
        # Non-strict on both sides: tied highs are common on thin books and low-precision
        # ticks, and a strict rule silently finds no structure at all in a staircase.
        # `_alternate` collapses the resulting plateau into a single pivot.
        if all(bar.high >= item.high for item in before) and all(
            bar.high >= item.high for item in after
        ):
            raw.append(SwingPoint(SwingKind.HIGH, index, bar.high, bar.closed_at))
        if all(bar.low <= item.low for item in before) and all(
            bar.low <= item.low for item in after
        ):
            raw.append(SwingPoint(SwingKind.LOW, index, bar.low, bar.closed_at))

    raw.sort(key=lambda point: point.index)
    return _alternate(raw)


def _alternate(points: Sequence[SwingPoint]) -> tuple[SwingPoint, ...]:
    """Collapse consecutive same-kind pivots to the most extreme one.

    HH/HL analysis is only meaningful over an alternating sequence; two swing highs with no
    intervening low are one swing high observed twice.
    """
    sequence: list[SwingPoint] = []
    for point in points:
        if not sequence or sequence[-1].kind is not point.kind:
            sequence.append(point)
            continue
        previous = sequence[-1]
        more_extreme = (
            point.price > previous.price
            if point.kind is SwingKind.HIGH
            else point.price < previous.price
        )
        if more_extreme:
            sequence[-1] = point
    return tuple(sequence)


def classify(candles: Sequence[Candle], *, left: int = 2, right: int = 2) -> MarketStructure:
    """Classify trend from the confirmed swing sequence."""
    swings = swing_points(candles, left=left, right=right)
    highs = [point for point in swings if point.kind is SwingKind.HIGH]
    lows = [point for point in swings if point.kind is SwingKind.LOW]

    if len(highs) < 2 or len(lows) < 2:
        return MarketStructure(
            trend=TrendState.RANGE,
            swings=swings,
            higher_high=False,
            higher_low=False,
            lower_high=False,
            lower_low=False,
            break_of_structure=False,
            reasons=("insufficient confirmed swings to classify structure",),
        )

    higher_high = highs[-1].price > highs[-2].price
    higher_low = lows[-1].price > lows[-2].price
    lower_high = highs[-1].price < highs[-2].price
    lower_low = lows[-1].price < lows[-2].price

    reasons: tuple[str, ...]
    if higher_high and higher_low:
        trend = TrendState.UPTREND
        reasons = (
            f"higher high {highs[-2].price} -> {highs[-1].price}",
            f"higher low {lows[-2].price} -> {lows[-1].price}",
        )
    elif lower_high and lower_low:
        trend = TrendState.DOWNTREND
        reasons = (
            f"lower high {highs[-2].price} -> {highs[-1].price}",
            f"lower low {lows[-2].price} -> {lows[-1].price}",
        )
    else:
        trend = TrendState.RANGE
        reasons = ("swing sequence is not a consistent higher-high/higher-low structure",)

    closed = sorted(
        (candle for candle in candles if candle.closed), key=lambda item: item.closed_at
    )
    last_close = closed[-1].close if closed else Decimal("0")
    break_of_structure = (
        trend is TrendState.UPTREND
        and last_close > highs[-1].price
        or trend is TrendState.DOWNTREND
        and last_close < lows[-1].price
    )
    if break_of_structure:
        reasons = (*reasons, "price has broken the last confirmed swing in the trend direction")

    return MarketStructure(
        trend=trend,
        swings=swings,
        higher_high=higher_high,
        higher_low=higher_low,
        lower_high=lower_high,
        lower_low=lower_low,
        break_of_structure=break_of_structure,
        reasons=reasons,
    )
