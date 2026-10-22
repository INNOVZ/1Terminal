from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from algotrader.domain.models import Candle, Direction

ZERO = Decimal("0")
HUNDRED = Decimal("100")


def ema(values: Sequence[Decimal], length: int) -> list[Decimal]:
    """Seed with the Wilder/TradingView SMA so long lengths do not carry seed bias.

    Seeding from ``values[0]`` leaves (1 - 2/(length+1))**(n-1) weight on a single
    arbitrary observation: 7.5% for EMA200 at 260 bars, which is enough to flip an
    ``close > ema200`` regime gate. Pine's ``ta.ema`` seeds with ``sma(source, length)``
    at index length-1, so this also restores parity with the reference indicator.
    """
    if length <= 0:
        raise ValueError("EMA length must be positive")
    if not values:
        return []
    if len(values) < length:
        # Too short to seed; fall back to a running mean so callers still get a series.
        return [sum(values[: index + 1], ZERO) / Decimal(index + 1) for index in range(len(values))]
    alpha = Decimal("2") / Decimal(length + 1)
    seed = sum(values[:length], ZERO) / Decimal(length)
    # Warm-up positions are diagnostics only; callers read the tail.
    result = [sum(values[: index + 1], ZERO) / Decimal(index + 1) for index in range(length - 1)]
    result.append(seed)
    for value in values[length:]:
        result.append(alpha * value + (Decimal("1") - alpha) * result[-1])
    return result


def rma(values: Sequence[Decimal], length: int) -> list[Decimal]:
    if length <= 0:
        raise ValueError("RMA length must be positive")
    if not values:
        return []
    alpha = Decimal("1") / Decimal(length)
    result: list[Decimal] = []
    rolling = ZERO
    for index, value in enumerate(values):
        rolling += value
        if index < length - 1:
            # Prefix values are warm-up diagnostics only. At length-1 the series is
            # reseeded with the Wilder SMA used by TradingView's RMA.
            result.append(rolling / Decimal(index + 1))
        elif index == length - 1:
            result.append(rolling / Decimal(length))
        else:
            result.append(alpha * value + (Decimal("1") - alpha) * result[-1])
    return result


def sma(values: Sequence[Decimal], length: int) -> list[Decimal | None]:
    if length <= 0:
        raise ValueError("SMA length must be positive")
    result: list[Decimal | None] = []
    rolling = ZERO
    for index, value in enumerate(values):
        rolling += value
        if index >= length:
            rolling -= values[index - length]
        result.append(rolling / Decimal(length) if index >= length - 1 else None)
    return result


def true_ranges(candles: Sequence[Candle]) -> list[Decimal]:
    if not candles:
        return []
    result = [candles[0].high - candles[0].low]
    for previous, current in zip(candles, candles[1:], strict=False):
        result.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return result


def atr(candles: Sequence[Candle], length: int = 14) -> list[Decimal]:
    return rma(true_ranges(candles), length)


def rsi(closes: Sequence[Decimal], length: int = 14) -> list[Decimal]:
    if not closes:
        return []
    if length <= 0:
        raise ValueError("RSI length must be positive")
    if len(closes) <= length:
        return [Decimal("50")] * len(closes)
    changes = [current - previous for previous, current in zip(closes, closes[1:], strict=False)]
    gains = [max(change, ZERO) for change in changes]
    losses = [max(-change, ZERO) for change in changes]
    average_gain = sum(gains[:length], ZERO) / Decimal(length)
    average_loss = sum(losses[:length], ZERO) / Decimal(length)
    result = [Decimal("50")] * length

    def value(gain: Decimal, loss: Decimal) -> Decimal:
        if loss == ZERO:
            return HUNDRED if gain > ZERO else Decimal("50")
        rs = gain / loss
        return HUNDRED - HUNDRED / (Decimal("1") + rs)

    result.append(value(average_gain, average_loss))
    alpha = Decimal("1") / Decimal(length)
    for gain, loss in zip(gains[length:], losses[length:], strict=True):
        average_gain = alpha * gain + (Decimal("1") - alpha) * average_gain
        average_loss = alpha * loss + (Decimal("1") - alpha) * average_loss
        result.append(value(average_gain, average_loss))
    return result


def macd(
    closes: Sequence[Decimal], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[Decimal], list[Decimal], list[Decimal]]:
    fast_values = ema(closes, fast)
    slow_values = ema(closes, slow)
    line = [
        fast_value - slow_value
        for fast_value, slow_value in zip(fast_values, slow_values, strict=True)
    ]
    signal_values = ema(line, signal)
    histogram = [
        value - signal_value for value, signal_value in zip(line, signal_values, strict=True)
    ]
    return line, signal_values, histogram


def adx(
    candles: Sequence[Candle], length: int = 14
) -> tuple[list[Decimal], list[Decimal], list[Decimal]]:
    if not candles:
        return [], [], []
    plus_dm = [ZERO]
    minus_dm = [ZERO]
    for previous, current in zip(candles, candles[1:], strict=False):
        up = current.high - previous.high
        down = previous.low - current.low
        plus_dm.append(up if up > down and up > ZERO else ZERO)
        minus_dm.append(down if down > up and down > ZERO else ZERO)
    tr_values = rma(true_ranges(candles), length)
    plus_smoothed = rma(plus_dm, length)
    minus_smoothed = rma(minus_dm, length)
    plus_di: list[Decimal] = []
    minus_di: list[Decimal] = []
    dx: list[Decimal] = []
    for tr, plus, minus in zip(tr_values, plus_smoothed, minus_smoothed, strict=True):
        pdi = HUNDRED * plus / tr if tr > ZERO else ZERO
        mdi = HUNDRED * minus / tr if tr > ZERO else ZERO
        plus_di.append(pdi)
        minus_di.append(mdi)
        denominator = pdi + mdi
        dx.append(HUNDRED * abs(pdi - mdi) / denominator if denominator > ZERO else ZERO)
    return rma(dx, length), plus_di, minus_di


def choppiness_index(candles: Sequence[Candle], length: int = 14) -> list[Decimal]:
    """Choppiness Index: low values mean trending, high values mean choppy/ranging.

    Formula matches TradingView's Pine ``ta.sma(ta.tr, length) * length`` divided by
    the highest-high minus lowest-low over the same window, log-scaled to 0-100.
    Values below ~38 are strongly trending; above ~62 is choppy. V10 uses < 58 as
    the hard gate.
    """
    if length <= 0:
        raise ValueError("choppiness length must be positive")
    if len(candles) < length:
        return [Decimal("50")] * len(candles)
    tr_values = true_ranges(candles)
    result: list[Decimal] = []
    log_length = Decimal(str(math.log10(length)))
    for index in range(len(candles)):
        if index < length - 1:
            result.append(Decimal("50"))  # Warm-up period
            continue
        window_start = index - length + 1
        tr_sum = sum(tr_values[window_start : index + 1], ZERO)
        highest_high = max(candle.high for candle in candles[window_start : index + 1])
        lowest_low = min(candle.low for candle in candles[window_start : index + 1])
        price_range = highest_high - lowest_low
        if price_range <= ZERO or log_length <= ZERO:
            result.append(HUNDRED)
            continue
        ratio = tr_sum / price_range
        if ratio <= ZERO:
            result.append(ZERO)
            continue
        result.append(HUNDRED * Decimal(str(math.log10(float(ratio)))) / log_length)
    return result


def momentum_displacement(
    closes: Sequence[Decimal], atr_values: Sequence[Decimal], lookback: int = 10
) -> list[Decimal]:
    """ATR-normalized price displacement: ``(close - close[lookback]) / ATR``.

    Positive values mean price is higher than ``lookback`` bars ago relative to
    volatility. This is Pine V10's momentum concept — pure price movement, not a
    composite indicator consensus.
    """
    if lookback <= 0:
        raise ValueError("momentum displacement lookback must be positive")
    result: list[Decimal] = []
    for index in range(len(closes)):
        if index < lookback or atr_values[index] <= ZERO:
            result.append(ZERO)
            continue
        result.append((closes[index] - closes[index - lookback]) / atr_values[index])
    return result


@dataclass(frozen=True, slots=True)
class IndicatorSnapshot:
    closed_at: datetime
    momentum_reference_closed_at: datetime
    close: Decimal
    momentum_reference_close: Decimal
    ema9: Decimal
    ema20: Decimal
    ema50: Decimal
    ema200: Decimal
    rsi14: Decimal
    momentum_reference_rsi14: Decimal
    atr14: Decimal
    adx14: Decimal
    momentum_reference_adx14: Decimal
    plus_di: Decimal
    minus_di: Decimal
    macd_line: Decimal
    macd_signal_line: Decimal
    macd_hist: Decimal
    prev_macd_line: Decimal
    prev_macd_signal_line: Decimal
    momentum_reference_macd_hist: Decimal
    relative_volume: Decimal
    choppiness: Decimal
    momentum_normalized: Decimal
    is_bullish_candle: bool


@dataclass(frozen=True, slots=True)
class RSIDivergence:
    """A divergence between two *confirmed* price pivots and close-based RSI.

    ``right`` closed bars must exist after each pivot before it can be returned. That makes
    the observation delayed, but prevents the common live/backtest mismatch where an
    apparent pivot disappears as the current candle extends.
    """

    kind: str
    older_closed_at: datetime
    newer_closed_at: datetime
    older_price: Decimal
    newer_price: Decimal
    older_rsi: Decimal
    newer_rsi: Decimal
    age_bars: int


@dataclass(frozen=True, slots=True)
class MACDDivergence:
    """A divergence between two confirmed price pivots and MACD histogram / line."""

    kind: str
    older_closed_at: datetime
    newer_closed_at: datetime
    older_price: Decimal
    newer_price: Decimal
    older_macd: Decimal
    newer_macd: Decimal
    age_bars: int


@dataclass(frozen=True, slots=True)
class PriceLevels:
    support: Decimal
    resistance: Decimal


@dataclass(frozen=True, slots=True)
class ContinuationTrigger:
    """Closed-bar evidence that a trend pullback has resumed."""

    passed: bool
    touched_ema20: bool
    held_ema50: bool
    reclaimed_ema20: bool
    confirmed_previous_bar: bool
    previous_bar_confirmation_required: bool

    @property
    def evidence(self) -> tuple[str, ...]:
        checks: tuple[tuple[bool, str], ...] = (
            (self.touched_ema20, "recent pullback touched the 1h EMA20 zone"),
            (self.held_ema50, "pullback held the 1h EMA50 trend boundary"),
            (self.reclaimed_ema20, "signal closed back on the trend side of 1h EMA20"),
        )
        if self.confirmed_previous_bar:
            checks += ((True, "signal closed through the previous bar in the trend direction"),)
        return tuple(message for passed, message in checks if passed)

    @property
    def failures(self) -> tuple[str, ...]:
        checks: tuple[tuple[bool, str], ...] = (
            (self.touched_ema20, "no recent pullback into the 1h EMA20 zone"),
            (self.held_ema50, "pullback closed beyond the 1h EMA50 trend boundary"),
            (self.reclaimed_ema20, "signal did not reclaim the trend side of 1h EMA20"),
        )
        if self.previous_bar_confirmation_required:
            checks += (
                (
                    self.confirmed_previous_bar,
                    "signal did not confirm through the previous bar",
                ),
            )
        return tuple(message for passed, message in checks if not passed)


def confirmed_rsi_divergences(
    candles: Sequence[Candle],
    *,
    rsi_length: int = 14,
    left: int = 2,
    right: int = 2,
    lookback: int = 72,
    max_age_bars: int = 12,
    min_rsi_delta: Decimal = Decimal("3"),
) -> tuple[RSIDivergence, ...]:
    """Return fresh regular RSI divergences using only confirmed closed-bar pivots.

    Bullish divergence requires a lower price low and a meaningfully higher RSI low.
    Bearish divergence requires a higher price high and a meaningfully lower RSI high.
    Wicks define the price pivot while RSI remains close based. Divergence is deliberately
    descriptive: callers decide whether it is a diagnostic, score penalty, or hard rule.
    """
    if rsi_length <= 0:
        raise ValueError("RSI length must be positive")
    if left <= 0 or right <= 0:
        raise ValueError("divergence pivots require positive left and right windows")
    if lookback < left + right + 2:
        raise ValueError("divergence lookback is too short for two pivots")
    if max_age_bars < right:
        raise ValueError("divergence maximum age must include pivot confirmation bars")
    if min_rsi_delta < 0:
        raise ValueError("divergence RSI delta cannot be negative")

    closed = sorted(
        (candle for candle in candles if candle.closed), key=lambda item: item.closed_at
    )
    minimum = max(rsi_length + left + right + 2, left + right + 3)
    if len(closed) < minimum:
        return ()
    rsi_values = rsi([candle.close for candle in closed], rsi_length)
    start = max(left, len(closed) - lookback)
    stop = len(closed) - right
    pivot_lows: list[int] = []
    pivot_highs: list[int] = []
    for index in range(start, stop):
        low = closed[index].low
        high = closed[index].high
        left_lows = (closed[offset].low for offset in range(index - left, index))
        right_lows = (closed[offset].low for offset in range(index + 1, index + right + 1))
        left_highs = (closed[offset].high for offset in range(index - left, index))
        right_highs = (closed[offset].high for offset in range(index + 1, index + right + 1))
        # Strict on the left and inclusive on the right chooses one deterministic bar on
        # plateaus while still requiring future confirmation.
        if low < min(left_lows) and low <= min(right_lows):
            pivot_lows.append(index)
        if high > max(left_highs) and high >= max(right_highs):
            pivot_highs.append(index)

    divergences: list[RSIDivergence] = []
    if len(pivot_lows) >= 2:
        older, newer = pivot_lows[-2:]
        age = len(closed) - 1 - newer
        if (
            age <= max_age_bars
            and closed[newer].low < closed[older].low
            and rsi_values[newer] >= rsi_values[older] + min_rsi_delta
        ):
            divergences.append(
                RSIDivergence(
                    kind="bullish",
                    older_closed_at=closed[older].closed_at,
                    newer_closed_at=closed[newer].closed_at,
                    older_price=closed[older].low,
                    newer_price=closed[newer].low,
                    older_rsi=rsi_values[older],
                    newer_rsi=rsi_values[newer],
                    age_bars=age,
                )
            )
    if len(pivot_highs) >= 2:
        older, newer = pivot_highs[-2:]
        age = len(closed) - 1 - newer
        if (
            age <= max_age_bars
            and closed[newer].high > closed[older].high
            and rsi_values[newer] <= rsi_values[older] - min_rsi_delta
        ):
            divergences.append(
                RSIDivergence(
                    kind="bearish",
                    older_closed_at=closed[older].closed_at,
                    newer_closed_at=closed[newer].closed_at,
                    older_price=closed[older].high,
                    newer_price=closed[newer].high,
                    older_rsi=rsi_values[older],
                    newer_rsi=rsi_values[newer],
                    age_bars=age,
                )
            )
    return tuple(sorted(divergences, key=lambda item: item.newer_closed_at))


def confirmed_macd_divergences(
    candles: Sequence[Candle],
    *,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    left: int = 2,
    right: int = 2,
    lookback: int = 72,
    max_age_bars: int = 14,
) -> tuple[MACDDivergence, ...]:
    """Return fresh regular MACD divergences using only confirmed closed-bar pivots.

    Bullish divergence requires a lower price low and a higher MACD histogram.
    Bearish divergence requires a higher price high and a lower MACD histogram.
    """
    closed = sorted(
        (candle for candle in candles if candle.closed), key=lambda item: item.closed_at
    )
    minimum = max(slow + signal + left + right + 2, left + right + 3)
    if len(closed) < minimum:
        return ()

    closes = [c.close for c in closed]
    _, _, histogram = macd(closes, fast=fast, slow=slow, signal=signal)
    start = max(left, len(closed) - lookback)
    stop = len(closed) - right
    pivot_lows: list[int] = []
    pivot_highs: list[int] = []
    for index in range(start, stop):
        low = closed[index].low
        high = closed[index].high
        left_lows = (closed[offset].low for offset in range(index - left, index))
        right_lows = (closed[offset].low for offset in range(index + 1, index + right + 1))
        left_highs = (closed[offset].high for offset in range(index - left, index))
        right_highs = (closed[offset].high for offset in range(index + 1, index + right + 1))
        if low < min(left_lows) and low <= min(right_lows):
            pivot_lows.append(index)
        if high > max(left_highs) and high >= max(right_highs):
            pivot_highs.append(index)

    divergences: list[MACDDivergence] = []
    if len(pivot_lows) >= 2:
        older, newer = pivot_lows[-2:]
        age = len(closed) - 1 - newer
        if (
            age <= max_age_bars
            and closed[newer].low < closed[older].low
            and histogram[newer] > histogram[older]
        ):
            divergences.append(
                MACDDivergence(
                    kind="bullish",
                    older_closed_at=closed[older].closed_at,
                    newer_closed_at=closed[newer].closed_at,
                    older_price=closed[older].low,
                    newer_price=closed[newer].low,
                    older_macd=histogram[older],
                    newer_macd=histogram[newer],
                    age_bars=age,
                )
            )
    if len(pivot_highs) >= 2:
        older, newer = pivot_highs[-2:]
        age = len(closed) - 1 - newer
        if (
            age <= max_age_bars
            and closed[newer].high > closed[older].high
            and histogram[newer] < histogram[older]
        ):
            divergences.append(
                MACDDivergence(
                    kind="bearish",
                    older_closed_at=closed[older].closed_at,
                    newer_closed_at=closed[newer].closed_at,
                    older_price=closed[older].high,
                    newer_price=closed[newer].high,
                    older_macd=histogram[older],
                    newer_macd=histogram[newer],
                    age_bars=age,
                )
            )
    return tuple(sorted(divergences, key=lambda item: item.newer_closed_at))


def resample_candles(candles: Sequence[Candle], rule: str = "1d") -> list[Candle]:
    """Resample candles to higher timeframe (e.g. 4h -> 1d, 1d -> 1w)."""
    if not candles:
        return []
    closed = sorted((c for c in candles if c.closed), key=lambda x: x.closed_at)
    if not closed:
        return []

    groups: dict[str, list[Candle]] = {}
    for c in closed:
        if rule == "1d":
            key = c.closed_at.strftime("%Y-%m-%d")
        elif rule == "1w":
            iso_year, iso_week, _ = c.closed_at.isocalendar()
            key = f"{iso_year}-W{iso_week:02d}"
        else:
            key = c.closed_at.strftime("%Y-%m-%d")
        groups.setdefault(key, []).append(c)

    resampled: list[Candle] = []
    for _, group in groups.items():
        if not group:
            continue
        sorted_grp = sorted(group, key=lambda x: x.closed_at)
        resampled.append(
            Candle(
                opened_at=sorted_grp[0].opened_at,
                closed_at=sorted_grp[-1].closed_at,
                open=sorted_grp[0].open,
                high=max(g.high for g in sorted_grp),
                low=min(g.low for g in sorted_grp),
                close=sorted_grp[-1].close,
                volume=sum((g.volume for g in sorted_grp), ZERO),
                quote_volume=sum((g.quote_volume for g in sorted_grp), ZERO),
                closed=True,
            )
        )
    return sorted(resampled, key=lambda x: x.closed_at)


def continuation_trigger(
    candles: Sequence[Candle],
    direction: Direction,
    *,
    lookback: int = 6,
    touch_buffer_atr: Decimal = Decimal("0.25"),
    require_previous_bar_break: bool = False,
) -> ContinuationTrigger:
    """Detect a shallow EMA pullback followed by a closed-bar continuation trigger.

    The latest close must reclaim EMA20. At least one candle in the recent window must
    touch the EMA20 zone, while closes must remain on the trend side of EMA50. A break of
    the previous candle can be required as an additional strictness control. Long and short
    rules are exact mirrors.
    """
    if lookback < 2:
        raise ValueError("pullback lookback must be at least two bars")
    if touch_buffer_atr < 0:
        raise ValueError("pullback touch buffer cannot be negative")
    closed = sorted(
        (candle for candle in candles if candle.closed), key=lambda item: item.closed_at
    )
    minimum = max(50, lookback + 1)
    if len(closed) < minimum:
        raise ValueError(f"at least {minimum} closed candles are required")

    closes = [candle.close for candle in closed]
    ema20_values = ema(closes, 20)
    ema50_values = ema(closes, 50)
    atr_values = atr(closed, 14)
    last = len(closed) - 1
    start = max(0, last - lookback + 1)
    window = range(start, last + 1)
    current = closed[last]
    previous = closed[last - 1]

    if direction is Direction.LONG:
        touched = any(
            closed[index].low <= ema20_values[index] + atr_values[index] * touch_buffer_atr
            for index in window
        )
        held = all(closed[index].close >= ema50_values[index] for index in window)
        reclaimed = current.close > ema20_values[last]
        confirmed = current.close > previous.high and current.close > current.open
    else:
        touched = any(
            closed[index].high >= ema20_values[index] - atr_values[index] * touch_buffer_atr
            for index in window
        )
        held = all(closed[index].close <= ema50_values[index] for index in window)
        reclaimed = current.close < ema20_values[last]
        confirmed = current.close < previous.low and current.close < current.open

    passed = touched and held and reclaimed and (confirmed or not require_previous_bar_break)
    return ContinuationTrigger(
        passed=passed,
        touched_ema20=touched,
        held_ema50=held,
        reclaimed_ema20=reclaimed,
        confirmed_previous_bar=confirmed,
        previous_bar_confirmation_required=require_previous_bar_break,
    )


def support_resistance(candles: Sequence[Candle], lookback: int = 48) -> PriceLevels:
    """Return prior closed-bar range levels without using the current signal bar."""
    if lookback <= 0:
        raise ValueError("support/resistance lookback must be positive")
    closed = sorted(
        (candle for candle in candles if candle.closed), key=lambda item: item.closed_at
    )
    if len(closed) < lookback + 1:
        raise ValueError(f"at least {lookback + 1} closed candles are required for price levels")
    history = closed[-(lookback + 1) : -1]
    return PriceLevels(
        support=min(candle.low for candle in history),
        resistance=max(candle.high for candle in history),
    )


def snapshot(
    candles: Sequence[Candle],
    momentum_lookback: int = 3,
    momentum_displacement_lookback: int = 10,
) -> IndicatorSnapshot:
    if momentum_lookback <= 0:
        raise ValueError("momentum lookback must be positive")
    if momentum_displacement_lookback <= 0:
        raise ValueError("momentum displacement lookback must be positive")
    closed = sorted(
        (candle for candle in candles if candle.closed), key=lambda item: item.closed_at
    )
    if len(closed) < max(210, momentum_lookback + 1):
        raise ValueError("at least 210 closed candles are required")
    closes = [candle.close for candle in closed]
    volumes = [candle.volume for candle in closed]
    ema9 = ema(closes, 9)
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    rsi14 = rsi(closes, 14)
    atr14 = atr(closed, 14)
    adx14, plus_di, minus_di = adx(closed, 14)
    macd_line_values, macd_signal_values, histogram = macd(closes)
    chop = choppiness_index(closed, 14)
    mom_disp = momentum_displacement(closes, atr14, momentum_displacement_lookback)
    volume_average = sma(volumes, 20)[-1]
    relative_volume = (
        volumes[-1] / volume_average if volume_average and volume_average > 0 else Decimal("1")
    )
    reference_index = -(momentum_lookback + 1)
    signal_candle = closed[-1]
    return IndicatorSnapshot(
        closed_at=closed[-1].closed_at,
        momentum_reference_closed_at=closed[reference_index].closed_at,
        close=closes[-1],
        momentum_reference_close=closes[reference_index],
        ema9=ema9[-1],
        ema20=ema20[-1],
        ema50=ema50[-1],
        ema200=ema200[-1],
        rsi14=rsi14[-1],
        momentum_reference_rsi14=rsi14[reference_index],
        atr14=atr14[-1],
        adx14=adx14[-1],
        momentum_reference_adx14=adx14[reference_index],
        plus_di=plus_di[-1],
        minus_di=minus_di[-1],
        macd_line=macd_line_values[-1],
        macd_signal_line=macd_signal_values[-1],
        macd_hist=histogram[-1],
        prev_macd_line=macd_line_values[-2],
        prev_macd_signal_line=macd_signal_values[-2],
        momentum_reference_macd_hist=histogram[reference_index],
        relative_volume=relative_volume,
        choppiness=chop[-1],
        momentum_normalized=mom_disp[-1],
        is_bullish_candle=signal_candle.close > signal_candle.open,
    )


def bollinger_bands(
    values: Sequence[Decimal], length: int = 20, mult: Decimal = Decimal("2.0")
) -> tuple[list[Decimal], list[Decimal], list[Decimal], list[Decimal]]:
    """Calculate Bollinger Bands (upper, middle, lower, bandwidth)."""
    if length <= 0:
        raise ValueError("Bollinger Bands length must be positive")
    if not values:
        return [], [], [], []
    middle_series = sma(values, length)
    upper_series: list[Decimal] = []
    middle_out: list[Decimal] = []
    lower_series: list[Decimal] = []
    bandwidth_series: list[Decimal] = []

    for index in range(len(values)):
        val = values[index]
        if index < length - 1:
            upper_series.append(val)
            middle_out.append(val)
            lower_series.append(val)
            bandwidth_series.append(ZERO)
            continue
        window = values[index - length + 1 : index + 1]
        raw_m = middle_series[index]
        m = raw_m if raw_m is not None else val
        middle_out.append(m)
        variance = sum((x - m) ** 2 for x in window) / Decimal(length)
        stdev = Decimal(str(math.sqrt(float(variance))))
        upper = m + mult * stdev
        lower = m - mult * stdev
        bw = (upper - lower) / m if m > ZERO else ZERO
        upper_series.append(upper)
        lower_series.append(lower)
        bandwidth_series.append(bw)

    return upper_series, middle_out, lower_series, bandwidth_series


def keltner_channels(
    candles: Sequence[Candle], length: int = 20, mult: Decimal = Decimal("1.5")
) -> tuple[list[Decimal], list[Decimal], list[Decimal]]:
    """Calculate Keltner Channels (upper, middle, lower) using EMA and ATR."""
    if length <= 0:
        raise ValueError("Keltner Channels length must be positive")
    if not candles:
        return [], [], []
    closes = [c.close for c in candles]
    middle_series = ema(closes, length)
    atr_series = atr(candles, length)
    upper_series: list[Decimal] = []
    lower_series: list[Decimal] = []

    for m, a in zip(middle_series, atr_series, strict=True):
        upper_series.append(m + mult * a)
        lower_series.append(m - mult * a)

    return upper_series, middle_series, lower_series


def chaikin_money_flow(candles: Sequence[Candle], length: int = 20) -> list[Decimal]:
    """Calculate Chaikin Money Flow (CMF) over a specified rolling window."""
    if length <= 0:
        raise ValueError("CMF length must be positive")
    if not candles:
        return []
    mfv_list: list[Decimal] = []
    vol_list: list[Decimal] = []

    for c in candles:
        high_low = c.high - c.low
        mult = ((c.close - c.low) - (c.high - c.close)) / high_low if high_low > ZERO else ZERO
        mfv = mult * c.volume
        mfv_list.append(mfv)
        vol_list.append(c.volume)

    result: list[Decimal] = []
    for index in range(len(candles)):
        start = max(0, index - length + 1)
        sub_mfv = sum(mfv_list[start : index + 1], ZERO)
        sub_vol = sum(vol_list[start : index + 1], ZERO)
        if sub_vol > ZERO:
            result.append(sub_mfv / sub_vol)
        else:
            result.append(ZERO)
    return result


@dataclass(frozen=True, slots=True)
class VolumeProfileBin:
    price: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class VolumeProfile:
    """Fixed Range Volume Profile across a sequence of price candles."""

    poc_price: Decimal
    vah_price: Decimal
    val_price: Decimal
    total_volume: Decimal
    value_area_volume: Decimal
    poc_volume: Decimal = ZERO
    bins: tuple[VolumeProfileBin, ...] = field(default_factory=tuple)


def _triangular_cdf(x: Decimal, a: Decimal, b: Decimal, c: Decimal) -> Decimal:
    """Cumulative distribution function for triangular distribution on [a, b] with mode c."""
    if x <= a:
        return ZERO
    if x >= b:
        return Decimal("1")
    span = max(b - a, Decimal("1e-8"))
    left_span = max(c - a, Decimal("1e-8"))
    right_span = max(b - c, Decimal("1e-8"))
    if x <= c:
        return ((x - a) ** 2) / (span * left_span)
    return Decimal("1") - ((b - x) ** 2) / (span * right_span)


def _triangular_cdf_f(x: float, a: float, b: float, c: float) -> float:
    """Fast float triangular CDF for high-throughput volume profile computation."""
    if x <= a:
        return 0.0
    if x >= b:
        return 1.0
    span = max(b - a, 1e-8)
    left_span = max(c - a, 1e-8)
    right_span = max(b - c, 1e-8)
    if x <= c:
        return ((x - a) ** 2) / (span * left_span)
    return 1.0 - ((b - x) ** 2) / (span * right_span)


def volume_profile(
    candles: Sequence[Candle],
    bins: int = 50,
    value_area_pct: Decimal = Decimal("0.70"),
) -> VolumeProfile:
    """Compute Fixed Range Triangular Kernel Volume Profile (POC, VAH, VAL) over candles.

    For each candle, distributes quote_volume (or volume) across bins intersecting [low, high]
    using a triangular distribution centered at Typical Price tp = (high + low + close) / 3.0.
    Finds the Point of Control (POC, max-volume level) and Value Area (70% total volume).
    Optimized with fast vectorizable float operations for high-throughput institutional screening.
    """
    if not candles:
        return VolumeProfile(ZERO, ZERO, ZERO, ZERO, ZERO)

    lows = [float(c.low) for c in candles]
    highs = [float(c.high) for c in candles]
    closes = [float(c.close) for c in candles]
    vols = [float(c.quote_volume if c.quote_volume > ZERO else c.volume) for c in candles]

    min_low = min(lows)
    max_high = max(highs)
    total_vol = sum(vols)
    if max_high <= min_low or bins <= 0:
        d_max = Decimal(str(round(max_high, 8)))
        d_min = Decimal(str(round(min_low, 8)))
        d_vol = Decimal(str(round(total_vol, 2)))
        return VolumeProfile(d_max, d_max, d_min, d_vol, d_vol)

    bin_width = (max_high - min_low) / float(bins)
    inv_bin_width = 1.0 / bin_width
    bin_volumes = [0.0] * bins
    bin_mids = [min_low + (float(i) + 0.5) * bin_width for i in range(bins)]

    for c_low, c_high, c_close, c_vol in zip(lows, highs, closes, vols, strict=False):
        if c_vol <= 0.0:
            continue
        span = max(c_high - c_low, 1e-8)
        tp = (c_high + c_low + c_close) / 3.0
        tp = max(c_low, min(c_high, tp))
        left_span = max(tp - c_low, 1e-8)
        right_span = max(c_high - tp, 1e-8)
        inv_left = 1.0 / (span * left_span)
        inv_right = 1.0 / (span * right_span)

        low_idx = min(bins - 1, max(0, int((c_low - min_low) * inv_bin_width)))
        high_idx = min(bins - 1, max(0, int((c_high - min_low) * inv_bin_width)))
        for i in range(low_idx, high_idx + 1):
            b_low = min_low + float(i) * bin_width
            b_high = b_low + bin_width
            x1 = max(c_low, b_low)
            x2 = min(c_high, b_high)
            if x2 > x1:
                cdf1 = ((x1 - c_low) ** 2) * inv_left if x1 <= tp else 1.0 - ((c_high - x1) ** 2) * inv_right
                cdf2 = ((x2 - c_low) ** 2) * inv_left if x2 <= tp else 1.0 - ((c_high - x2) ** 2) * inv_right
                w = cdf2 - cdf1
                if w > 0.0:
                    bin_volumes[i] += c_vol * w

    poc_idx = max(range(bins), key=lambda i: bin_volumes[i])
    target_vol = total_vol * float(value_area_pct)
    va_indices = {poc_idx}
    cur_vol = bin_volumes[poc_idx]

    up = poc_idx + 1
    down = poc_idx - 1
    while cur_vol < target_vol and (up < bins or down >= 0):
        up_vol = bin_volumes[up] if up < bins else -1.0
        down_vol = bin_volumes[down] if down >= 0 else -1.0
        if up_vol >= down_vol and up < bins:
            va_indices.add(up)
            cur_vol += up_vol
            up += 1
        elif down >= 0:
            va_indices.add(down)
            cur_vol += down_vol
            down -= 1
        else:
            break

    poc_price = Decimal(str(round(bin_mids[poc_idx], 8)))
    poc_volume = Decimal(str(round(bin_volumes[poc_idx], 2)))
    vah_price = Decimal(str(round(bin_mids[max(va_indices)], 8)))
    val_price = Decimal(str(round(bin_mids[min(va_indices)], 8)))
    total_volume_d = Decimal(str(round(total_vol, 2)))
    value_area_volume_d = Decimal(str(round(cur_vol, 2)))

    vp_bins = tuple(
        VolumeProfileBin(
            price=Decimal(str(round(bin_mids[i], 8))),
            volume=Decimal(str(round(bin_volumes[i], 2))),
        )
        for i in range(bins)
    )
    return VolumeProfile(
        poc_price=poc_price,
        vah_price=vah_price,
        val_price=val_price,
        total_volume=total_volume_d,
        value_area_volume=value_area_volume_d,
        poc_volume=poc_volume,
        bins=vp_bins,
    )


def volume_contraction_metrics(
    candles_1h: Sequence[Candle],
    days_short: int = 7,
    days_long: int = 14,
) -> tuple[Decimal, Decimal, Decimal]:
    """Compute 7-day ADV, 14-day ADV, and the Volume Contraction Ratio.

    Ratio between 0.45 and 0.85 indicates Wyckoff supply exhaustion (volume dry-up).
    """
    bars_short = days_short * 24
    bars_long = days_long * 24
    if len(candles_1h) < 24:
        return ZERO, ZERO, Decimal("1.0")

    candles_s = candles_1h[-bars_short:] if len(candles_1h) >= bars_short else candles_1h
    candles_l = candles_1h[-bars_long:] if len(candles_1h) >= bars_long else candles_1h

    adv_short = sum((c.volume for c in candles_s), ZERO) / Decimal(str(max(1, len(candles_s) / 24)))
    adv_long = sum((c.volume for c in candles_l), ZERO) / Decimal(str(max(1, len(candles_l) / 24)))
    ratio = adv_short / adv_long if adv_long > ZERO else Decimal("1.0")
    return adv_short, adv_long, ratio


@dataclass(frozen=True, slots=True)
class CVDMetrics:
    """Cumulative Volume Delta (CVD) order flow microstructure metrics."""

    delta_latest: Decimal
    cvd_24h_change: Decimal
    cvd_z_score: Decimal
    is_bullish_absorption: bool
    is_bearish_distribution: bool


def cumulative_volume_delta_metrics(
    candles_1h: Sequence[Candle],
    lookback_window: int = 24,
) -> CVDMetrics:
    """Compute order-flow Cumulative Volume Delta (CVD) and passive limit absorption footprints.

    Detects when aggressive market buyers/sellers are absorbing resting liquidity
    while price remains compressed inside a pre-breakout coil.
    """
    if not candles_1h:
        return CVDMetrics(
            delta_latest=ZERO,
            cvd_24h_change=ZERO,
            cvd_z_score=ZERO,
            is_bullish_absorption=False,
            is_bearish_distribution=False,
        )

    deltas: list[Decimal] = []
    for c in candles_1h:
        if c.taker_buy_volume > ZERO:
            # Exact trade-tape signed delta: Taker Buy - Taker Sell
            taker_sell = max(ZERO, c.volume - c.taker_buy_volume)
            deltas.append(c.taker_buy_volume - taker_sell)
        else:
            # Proxy CLV signed volume if taker tape unavailable
            span = c.high - c.low
            if span > ZERO:
                clv = ((c.close - c.low) - (c.high - c.close)) / span
                deltas.append(clv * c.volume)
            else:
                deltas.append(ZERO)

    window = min(lookback_window, len(deltas))
    recent_deltas = deltas[-window:]
    cvd_24h = sum(recent_deltas, ZERO)
    delta_latest = deltas[-1] if deltas else ZERO

    # Normalized CVD Z-Score relative to 7d ADV
    total_vol = sum((c.volume for c in candles_1h[-window:]), ZERO)
    cvd_norm = cvd_24h / (total_vol or Decimal("1.0"))
    cvd_z_score = (cvd_norm * Decimal("10.0")).quantize(Decimal("0.1"))

    # Price excursion over the window
    p_now = candles_1h[-1].close
    p_start = candles_1h[-window].close if len(candles_1h) >= window else candles_1h[0].close
    p_ret = (p_now - p_start) / p_start if p_start > ZERO else ZERO

    # Hidden Institutional Accumulation (Bullish Absorption):
    # Price is compressed/flat (|return| <= 3.5%) while CVD is strongly positive
    is_bullish_absorption = (abs(p_ret) <= Decimal("0.035")) and (cvd_24h > ZERO) and (cvd_norm > Decimal("0.05"))

    # Hidden Institutional Distribution (Bearish Distribution):
    # Price is compressed/flat (|return| <= 3.5%) while CVD is strongly negative
    is_bearish_distribution = (abs(p_ret) <= Decimal("0.035")) and (cvd_24h < ZERO) and (cvd_norm < Decimal("-0.05"))

    return CVDMetrics(
        delta_latest=delta_latest,
        cvd_24h_change=cvd_24h,
        cvd_z_score=cvd_z_score,
        is_bullish_absorption=is_bullish_absorption,
        is_bearish_distribution=is_bearish_distribution,
    )


def taker_buy_ratio_from_candles(candles: Sequence[Candle]) -> Decimal:
    """Compute taker_buy_sell_ratio directly from Binance kline index [10]:

    taker_buy_vol / max(total_vol - taker_buy_vol, 1e-9) (i.e. buy_vol / sell_vol, centered at 1.00), rounded to 4 decimals.
    Falls back to base asset volume ratio if quote volumes are zero.
    """
    if not candles:
        return Decimal("1.0000")

    total_quote_vol = sum((c.quote_volume for c in candles), ZERO)
    total_taker_quote = sum((c.taker_buy_quote_volume for c in candles), ZERO)
    total_vol = sum((c.volume for c in candles), ZERO)
    total_taker_base = sum((c.taker_buy_volume for c in candles), ZERO)

    if total_taker_quote == ZERO and total_taker_base == ZERO:
        return Decimal("1.0000")

    if total_quote_vol > ZERO and total_taker_quote > ZERO:
        sell_vol = max(total_quote_vol - total_taker_quote, Decimal("1e-9"))
        return round(total_taker_quote / sell_vol, 4)

    if total_vol > ZERO and total_taker_base > ZERO:
        sell_vol = max(total_vol - total_taker_base, Decimal("1e-9"))
        return round(total_taker_base / sell_vol, 4)

    return Decimal("1.0000")



