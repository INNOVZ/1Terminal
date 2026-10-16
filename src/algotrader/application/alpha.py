"""Pure alpha policies.

This module decides whether market observations express a setup. It cannot access an
account, persist state, or place orders. Portfolio risk and execution remain separate
authority boundaries.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from algotrader.config import EmpiricalSetup, Settings, StrategyProfile
from algotrader.domain.indicators import (
    ContinuationTrigger,
    IndicatorSnapshot,
    atr,
    confirmed_rsi_divergences,
    macd,
)
from algotrader.domain.models import Candle, Direction, Quote


@dataclass(frozen=True, slots=True)
class AlphaDecision:
    eligible: bool
    intrinsic_score: Decimal
    evidence: tuple[str, ...]
    failures: tuple[str, ...]
    market_regime: str
    breakout_level: Decimal
    return_7d: Decimal
    return_30d: Decimal
    volatility_percentile: Decimal
    return_90d: Decimal | None = None
    return_180d: Decimal | None = None
    return_6h: Decimal | None = None
    return_24h: Decimal | None = None
    indicator_confirmations: tuple[str, ...] = ()
    indicator_conflicts: tuple[str, ...] = ()
    macd_cross_1h: str | None = None
    rsi_divergence_1h: str | None = None
    setup_signal: str | None = None
    setup_strength: Decimal | None = None
    aligned_momentum_horizons: int | None = None
    directional_momentum_score: Decimal | None = None
    technical_quality_score: Decimal | None = None
    breakout_relative_volume: Decimal | None = None
    breakout_age_bars: int | None = None


@dataclass(frozen=True, slots=True)
class TechnicalQuality:
    confirmations: tuple[str, ...]
    conflicts: tuple[str, ...]
    macd_cross_1h: str | None
    opposing_rsi_divergence: str | None

    @property
    def score(self) -> Decimal:
        """Bounded composite; correlated indicators rank but cannot create eligibility."""
        raw = Decimal(len(self.confirmations) * 2 - len(self.conflicts))
        return min(Decimal("8"), max(Decimal("-4"), raw))


@dataclass(frozen=True, slots=True)
class ImpulseRetest:
    passed: bool
    breakout_level: Decimal | None
    breakout_relative_volume: Decimal | None
    breakout_age_bars: int | None
    event_atr: Decimal | None
    retest_distance_atr: Decimal | None
    failures: tuple[str, ...]


def _directional(value: Decimal, direction: Direction) -> Decimal:
    return value if direction is Direction.LONG else -value


def normalized_directional_momentum_score(
    returns: tuple[Decimal, Decimal, Decimal],
    scales: tuple[Decimal, Decimal, Decimal],
    direction: Direction,
) -> Decimal:
    """Normalize three returns into one direction-symmetric [-1, 1] feature."""

    if any(scale <= 0 for scale in scales):
        raise ValueError("momentum normalization scales must be positive")

    def clipped(value: Decimal) -> Decimal:
        return min(Decimal("1"), max(Decimal("-1"), value))

    normalized = (
        clipped(_directional(value, direction) / scale)
        for value, scale in zip(returns, scales, strict=True)
    )
    return sum(normalized, Decimal("0")) / Decimal("3")


def directional_momentum_score(
    returns: tuple[Decimal, Decimal, Decimal], direction: Direction
) -> Decimal:
    """Normalize 30d/90d/180d returns into one symmetric [-1, 1] feature.

    A positive value always means momentum in the proposed trade direction. Mirroring an
    uptrend into an equal downtrend and changing LONG to SHORT therefore produces the same
    score. The fixed horizon scales bound outliers without adding optimizable thresholds.
    """

    return normalized_directional_momentum_score(
        returns,
        (Decimal("0.15"), Decimal("0.30"), Decimal("0.50")),
        direction,
    )


def _return_over(candles: Sequence[Candle], bars: int) -> Decimal:
    closed = [candle for candle in candles if candle.closed]
    if len(closed) <= bars or closed[-bars - 1].close <= 0:
        raise ValueError(f"{bars + 1} closed candles required for return calculation")
    return closed[-1].close / closed[-bars - 1].close - Decimal("1")


def _volatility_percentile(candles: Sequence[Candle], lookback: int) -> Decimal:
    closed = [candle for candle in candles if candle.closed]
    values = atr(closed, 14)
    normalized = [
        value / candle.close
        for value, candle in zip(values, closed, strict=True)
        if candle.close > 0
    ]
    window = normalized[-lookback:]
    if len(window) < lookback:
        raise ValueError(f"{lookback} volatility observations required")
    current = window[-1]
    return Decimal(sum(value <= current for value in window)) / Decimal(len(window))


def impulse_retest(
    candles: Sequence[Candle], direction: Direction, settings: Settings
) -> ImpulseRetest:
    """Find a closed-bar volume breakout, held retest, and current resumption.

    The current bar can confirm resumption but can never serve as the breakout or retest.
    This prevents same-bar look-ahead and ensures every prerequisite existed before entry.
    """

    closed = [candle for candle in candles if candle.closed]
    lookback = settings.early_breakout_lookback_hours
    max_age = settings.early_breakout_max_age_hours
    if len(closed) < lookback + max_age + 2:
        raise ValueError(
            f"{lookback + max_age + 2} closed hourly candles required for impulse retest"
        )
    atr_values = atr(closed, 14)
    current = closed[-1]
    previous = closed[-2]
    first_event = max(lookback, len(closed) - 1 - max_age)
    price_breakout_seen = False
    selected: tuple[int, Decimal, Decimal, Decimal] | None = None
    for event_index in range(len(closed) - 2, first_event - 1, -1):
        event = closed[event_index]
        prior = closed[event_index - lookback : event_index]
        level = (
            max(candle.high for candle in prior)
            if direction is Direction.LONG
            else min(candle.low for candle in prior)
        )
        broke = event.close > level if direction is Direction.LONG else event.close < level
        if not broke:
            continue
        price_breakout_seen = True
        average_volume = sum((candle.volume for candle in prior), Decimal("0")) / Decimal(
            len(prior)
        )
        relative_volume = event.volume / average_volume if average_volume > 0 else Decimal("0")
        event_atr = atr_values[event_index]
        if relative_volume >= settings.early_breakout_min_relative_volume and event_atr > 0:
            selected = (event_index, level, relative_volume, event_atr)
            break

    if selected is None:
        reason = (
            "recent channel breakout volume is below the ignition threshold"
            if price_breakout_seen
            else "no recent closed-bar price breakout beyond the prior channel"
        )
        return ImpulseRetest(False, None, None, None, None, None, (reason,))

    event_index, level, relative_volume, event_atr = selected
    event = closed[event_index]
    retest_bars = closed[event_index + 1 : -1]
    touch_buffer = event_atr * settings.early_retest_touch_buffer_atr
    hold_buffer = event_atr * settings.early_retest_hold_buffer_atr
    if direction is Direction.LONG:
        touched = any(candle.low <= level + touch_buffer for candle in retest_bars)
        held = bool(retest_bars) and all(
            candle.close >= level - hold_buffer for candle in retest_bars
        )
        resumed = current.close > max(level, previous.high, event.high)
        distances = [abs(candle.low - level) / event_atr for candle in retest_bars]
    else:
        touched = any(candle.high >= level - touch_buffer for candle in retest_bars)
        held = bool(retest_bars) and all(
            candle.close <= level + hold_buffer for candle in retest_bars
        )
        resumed = current.close < min(level, previous.low, event.low)
        distances = [abs(candle.high - level) / event_atr for candle in retest_bars]
    failures = tuple(
        message
        for passed, message in (
            (touched, "breakout level has not been retested"),
            (held, "retest failed to hold the breakout level"),
            (resumed, "current closed bar has not resumed beyond the prior bar"),
        )
        if not passed
    )
    return ImpulseRetest(
        passed=not failures,
        breakout_level=level,
        breakout_relative_volume=relative_volume,
        breakout_age_bars=len(closed) - 1 - event_index,
        event_atr=event_atr,
        retest_distance_atr=min(distances) if distances else None,
        failures=failures,
    )


class RegimeBreakoutAlpha:
    """Longer-horizon trend regime plus an hourly channel breakout.

    MACD, RSI and ADX are intentionally absent from the hard checks. They are derived from
    the same price history and are retained only as bounded, low-weight ranking evidence.
    """

    profile = StrategyProfile.REGIME_BREAKOUT

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _core_score(
        self,
        *,
        one: IndicatorSnapshot,
        quote: Quote,
        direction: Direction,
        directional_30d: Decimal,
        breakout_level: Decimal,
    ) -> Decimal:
        def bounded(value: Decimal) -> Decimal:
            return min(Decimal("1"), max(Decimal("0"), value))

        trend_score = bounded(directional_30d / Decimal("0.20")) * Decimal("8")
        breakout_distance = _directional(one.close - breakout_level, direction)
        breakout_score = bounded(
            breakout_distance / max(one.atr14, Decimal("0.00000001"))
        ) * Decimal("5")
        volume_score = bounded(one.relative_volume / Decimal("2")) * Decimal("3")
        spread_score = bounded(
            Decimal("1") - quote.spread_bps / self._settings.max_spread_bps
        ) * Decimal("2")
        return Decimal("70") + trend_score + breakout_score + volume_score + spread_score

    def evaluate(
        self,
        one_hour: Sequence[Candle],
        four_hour: Sequence[Candle],
        one: IndicatorSnapshot,
        four: IndicatorSnapshot,
        quote: Quote,
        direction: Direction,
    ) -> AlphaDecision:
        closed_1h = [candle for candle in one_hour if candle.closed]
        lookback = self._settings.breakout_lookback_hours
        if len(closed_1h) <= lookback:
            raise ValueError(f"{lookback + 1} closed hourly candles required for breakout")
        prior = closed_1h[-lookback - 1 : -1]
        breakout_level = (
            max(candle.high for candle in prior)
            if direction is Direction.LONG
            else min(candle.low for candle in prior)
        )
        breakout = (
            one.close > breakout_level
            if direction is Direction.LONG
            else one.close < breakout_level
        )

        # Six 4h bars per day. These horizons are deliberately materially longer than the
        # legacy three-hour endpoint comparison.
        return_7d = _return_over(four_hour, 42)
        return_30d = _return_over(four_hour, 180)
        directional_7d = _directional(return_7d, direction)
        directional_30d = _directional(return_30d, direction)
        macro_aligned = (
            four.close > four.ema200 if direction is Direction.LONG else four.close < four.ema200
        )
        volatility_percentile = _volatility_percentile(
            closed_1h, self._settings.volatility_regime_lookback_hours
        )
        volatility_normal = (
            self._settings.min_volatility_percentile
            <= volatility_percentile
            <= self._settings.max_volatility_percentile
        )
        volume_confirmed = (
            self._settings.breakout_min_relative_volume <= one.relative_volume <= Decimal("4")
        )
        entry_price = quote.ask if direction is Direction.LONG else quote.bid
        quote_not_chased = (
            entry_price <= one.close + one.atr14 * self._settings.max_entry_chase_atr
            if direction is Direction.LONG
            else entry_price >= one.close - one.atr14 * self._settings.max_entry_chase_atr
        )

        checks = (
            (macro_aligned, "4h price is not aligned with the EMA200 market regime"),
            (directional_7d > 0, "7-day momentum is not aligned"),
            (directional_30d > 0, "30-day momentum is not aligned"),
            (breakout, f"price has not closed beyond the prior {lookback}h channel"),
            (volatility_normal, "token volatility is outside its own permitted percentile regime"),
            (volume_confirmed, "breakout volume is not credible"),
            (one.atr14 > 0, "ATR is not positive"),
            (quote_not_chased, "entry quote moved beyond the anti-chase boundary"),
        )
        failures = tuple(message for passed, message in checks if not passed)

        # Price-derived oscillators contribute at most seven points in total and never
        # decide eligibility. This preserves useful diagnostics without double-counting
        # them as independent confirmations.
        def bounded(value: Decimal) -> Decimal:
            return min(Decimal("1"), max(Decimal("0"), value))

        core_score = self._core_score(
            one=one,
            quote=quote,
            direction=direction,
            directional_30d=directional_30d,
            breakout_level=breakout_level,
        )
        adx_score = bounded((one.adx14 - Decimal("15")) / Decimal("35")) * Decimal("3")
        macd_supports = one.macd_hist > 0 if direction is Direction.LONG else one.macd_hist < 0
        macd_score = Decimal("2") if macd_supports else Decimal("0")
        rsi_supports = one.rsi14 > 50 if direction is Direction.LONG else one.rsi14 < 50
        rsi_score = Decimal("2") if rsi_supports else Decimal("0")
        intrinsic_score = min(
            Decimal("95"),
            core_score + adx_score + macd_score + rsi_score,
        )
        regime_direction = "bull" if direction is Direction.LONG else "bear"
        return AlphaDecision(
            eligible=not failures,
            intrinsic_score=intrinsic_score if not failures else Decimal("0"),
            evidence=(
                f"4h/daily-proxy {regime_direction} regime",
                "7-day and 30-day directional momentum",
                f"confirmed {lookback}h channel breakout",
                "token-relative volatility regime",
                "volume and spread quality",
                "MACD/RSI/ADX retained as low-weight diagnostics only",
            ),
            failures=failures,
            market_regime=f"trend_{regime_direction}",
            breakout_level=breakout_level,
            return_7d=return_7d,
            return_30d=return_30d,
            volatility_percentile=volatility_percentile,
        )


class SlowTimeSeriesMomentumAlpha:
    """Research-only 4h entry timing inside a genuine daily momentum regime."""

    profile = StrategyProfile.SLOW_TREND

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def evaluate(
        self,
        four_hour: Sequence[Candle],
        daily: Sequence[Candle],
        four: IndicatorSnapshot,
        daily_snapshot: IndicatorSnapshot,
        quote: Quote,
        direction: Direction,
    ) -> AlphaDecision:
        closed_4h = [candle for candle in four_hour if candle.closed]
        lookback = self._settings.slow_breakout_4h_bars
        prior = closed_4h[-lookback - 1 : -1]
        if len(prior) < lookback:
            raise ValueError(f"{lookback + 1} closed 4h candles required")
        breakout_level = (
            max(candle.high for candle in prior)
            if direction is Direction.LONG
            else min(candle.low for candle in prior)
        )
        breakout = (
            four.close > breakout_level
            if direction is Direction.LONG
            else four.close < breakout_level
        )
        return_30d = _return_over(daily, 30)
        return_90d = _return_over(daily, 90)
        return_180d = _return_over(daily, 180)
        aligned_horizons = sum(
            _directional(value, direction) > 0 for value in (return_30d, return_90d, return_180d)
        )
        daily_macro = (
            daily_snapshot.close > daily_snapshot.ema200
            if direction is Direction.LONG
            else daily_snapshot.close < daily_snapshot.ema200
        )
        four_trend = (
            four.ema20 > four.ema50 if direction is Direction.LONG else four.ema20 < four.ema50
        )
        volatility_percentile = _volatility_percentile(closed_4h, 120)
        entry = quote.ask if direction is Direction.LONG else quote.bid
        quote_not_chased = (
            abs(entry - four.close) <= four.atr14 * self._settings.max_entry_chase_atr
        )
        checks = (
            (daily_macro, "daily price is not aligned with daily EMA200"),
            (four_trend, "4h EMA20/EMA50 trend is not aligned"),
            (aligned_horizons >= 2, "fewer than two of 30d/90d/180d momentum horizons align"),
            (four.atr14 > 0, "4h ATR is not positive"),
            (quote_not_chased, "entry quote moved beyond the 4h anti-chase boundary"),
        )
        failures = tuple(message for passed, message in checks if not passed)
        directional_90d = max(Decimal("0"), _directional(return_90d, direction))
        breakout_bonus = Decimal("3") if breakout else Decimal("0")
        score = min(
            Decimal("95"),
            Decimal("72") + directional_90d * Decimal("40") + breakout_bonus,
        )
        side = "bull" if direction is Direction.LONG else "bear"
        return AlphaDecision(
            eligible=not failures,
            intrinsic_score=score if not failures else Decimal("0"),
            evidence=(
                "daily EMA200 macro regime",
                "two-of-three 30d/90d/180d time-series momentum",
                (
                    f"confirmed {lookback}-bar 4h channel breakout"
                    if breakout
                    else f"inside the prior {lookback}-bar 4h channel"
                ),
            ),
            failures=failures,
            market_regime=f"slow_{side}_trend",
            breakout_level=breakout_level,
            return_7d=_return_over(daily, 7),
            return_30d=return_30d,
            return_90d=return_90d,
            return_180d=return_180d,
            volatility_percentile=volatility_percentile,
        )


class TrendQualityAlpha(RegimeBreakoutAlpha):
    """Breakout alpha with an auditable consensus of familiar trend indicators.

    The primary hypothesis remains longer-horizon momentum plus a confirmed breakout.
    EMA, MACD, RSI and ADX/DMI are allowed to confirm that hypothesis, but no individual
    oscillator has authority to create a trade. RSI divergence is a delayed warning and
    score penalty, never a standalone reversal forecast.
    """

    profile = StrategyProfile.TREND_QUALITY

    def technical_quality(
        self,
        one_hour: Sequence[Candle],
        four_hour: Sequence[Candle],
        one: IndicatorSnapshot,
        four: IndicatorSnapshot,
        direction: Direction,
    ) -> TechnicalQuality:
        long = direction is Direction.LONG
        confirmations: list[str] = []
        conflicts: list[str] = []

        ema_1h = (
            one.close > one.ema200 and one.ema20 > one.ema50
            if long
            else one.close < one.ema200 and one.ema20 < one.ema50
        )
        ema_4h = (
            four.close > four.ema200 and four.ema20 > four.ema50
            if long
            else four.close < four.ema200 and four.ema20 < four.ema50
        )
        ema_opposed = (
            one.ema20 < one.ema50 and four.ema20 < four.ema50
            if long
            else one.ema20 > one.ema50 and four.ema20 > four.ema50
        )
        if ema_1h and ema_4h:
            confirmations.append("EMA: 1h/4h EMA20-EMA50 stacks and EMA200 regime align")
        elif ema_opposed:
            conflicts.append("EMA: 1h/4h fast-slow stacks oppose the trade")

        closed_1h = [candle for candle in one_hour if candle.closed]
        closed_4h = [candle for candle in four_hour if candle.closed]
        one_hist = macd([candle.close for candle in closed_1h])[2]
        four_hist = macd([candle.close for candle in closed_4h])[2]
        one_macd_aligned = one_hist[-1] > 0 if long else one_hist[-1] < 0
        four_macd_aligned = four_hist[-1] > 0 if long else four_hist[-1] < 0
        one_macd_opposed = one_hist[-1] < 0 if long else one_hist[-1] > 0
        four_macd_opposed = four_hist[-1] < 0 if long else four_hist[-1] > 0
        macd_cross: str | None = None
        if len(one_hist) >= 2:
            if one_hist[-1] > 0 >= one_hist[-2]:
                macd_cross = "bullish"
            elif one_hist[-1] < 0 <= one_hist[-2]:
                macd_cross = "bearish"
        if one_macd_aligned and four_macd_aligned:
            confirmations.append("MACD: 1h/4h histograms are on the directional side")
        elif one_macd_opposed and four_macd_opposed:
            conflicts.append("MACD: 1h/4h histograms oppose the trade")

        rsi_aligned = (
            Decimal("50") < one.rsi14 <= self._settings.rsi_long_max
            and Decimal("50") < four.rsi14 <= self._settings.four_hour_rsi_long_max
            if long
            else self._settings.rsi_short_min <= one.rsi14 < Decimal("50")
            and self._settings.four_hour_rsi_short_min <= four.rsi14 < Decimal("50")
        )
        rsi_opposed = (
            one.rsi14 < Decimal("45") and four.rsi14 < Decimal("50")
            if long
            else one.rsi14 > Decimal("55") and four.rsi14 > Decimal("50")
        )
        if rsi_aligned:
            confirmations.append("RSI: 1h/4h directional regime aligns without exhaustion")
        elif rsi_opposed:
            conflicts.append("RSI: momentum regime opposes the trade")

        di_aligned = one.plus_di > one.minus_di if long else one.minus_di > one.plus_di
        four_di_aligned = four.plus_di > four.minus_di if long else four.minus_di > four.plus_di
        di_opposed = one.plus_di < one.minus_di if long else one.minus_di < one.plus_di
        four_di_opposed = four.plus_di < four.minus_di if long else four.minus_di < four.plus_di
        if one.adx14 >= Decimal("20") and di_aligned and four_di_aligned:
            confirmations.append("ADX/DMI: trend strength and 1h/4h direction align")
        elif one.adx14 >= Decimal("20") and di_opposed and four_di_opposed:
            conflicts.append("ADX/DMI: a measurable directional trend opposes the trade")

        divergences = confirmed_rsi_divergences(
            closed_1h,
            lookback=self._settings.rsi_divergence_lookback_hours,
            max_age_bars=self._settings.rsi_divergence_max_age_hours,
            min_rsi_delta=self._settings.rsi_divergence_min_delta,
        )
        opposing_kind = "bearish" if long else "bullish"
        opposing = next(
            (item for item in reversed(divergences) if item.kind == opposing_kind), None
        )
        divergence = None
        if opposing is not None:
            divergence = f"{opposing.kind} RSI divergence confirmed {opposing.age_bars} bars ago"
            conflicts.append("RSI divergence: " + divergence)

        return TechnicalQuality(
            confirmations=tuple(confirmations),
            conflicts=tuple(conflicts),
            macd_cross_1h=macd_cross,
            opposing_rsi_divergence=divergence,
        )

    def evaluate(
        self,
        one_hour: Sequence[Candle],
        four_hour: Sequence[Candle],
        one: IndicatorSnapshot,
        four: IndicatorSnapshot,
        quote: Quote,
        direction: Direction,
    ) -> AlphaDecision:
        base = super().evaluate(one_hour, four_hour, one, four, quote, direction)
        quality = self.technical_quality(one_hour, four_hour, one, four, direction)
        failures = list(base.failures)
        if len(quality.confirmations) < self._settings.trend_quality_min_confirmations:
            failures.append(
                "technical quality consensus is below "
                f"{self._settings.trend_quality_min_confirmations}-of-4 confirmations"
            )

        directional_30d = _directional(base.return_30d, direction)
        core_score = self._core_score(
            one=one,
            quote=quote,
            direction=direction,
            directional_30d=directional_30d,
            breakout_level=base.breakout_level,
        )
        quality_score = Decimal(len(quality.confirmations)) * Decimal("2.5")
        conflict_penalty = Decimal(len(quality.conflicts)) * Decimal("1.5")
        fresh_cross_bonus = (
            Decimal("0.5")
            if quality.macd_cross_1h == ("bullish" if direction is Direction.LONG else "bearish")
            else Decimal("0")
        )
        score = min(
            Decimal("100"),
            max(Decimal("0"), core_score + quality_score + fresh_cross_bonus - conflict_penalty),
        )
        eligible = not failures
        return AlphaDecision(
            eligible=eligible,
            intrinsic_score=score if eligible else Decimal("0"),
            evidence=(
                *base.evidence[:-1],
                f"{len(quality.confirmations)}-of-4 EMA/MACD/RSI/ADX quality confirmations",
                "fresh MACD cross is timing evidence, not a mandatory trigger",
                "confirmed RSI divergence is a warning penalty, not a reversal forecast",
            ),
            failures=tuple(failures),
            market_regime=base.market_regime,
            breakout_level=base.breakout_level,
            return_7d=base.return_7d,
            return_30d=base.return_30d,
            volatility_percentile=base.volatility_percentile,
            indicator_confirmations=quality.confirmations,
            indicator_conflicts=quality.conflicts,
            macd_cross_1h=quality.macd_cross_1h,
            rsi_divergence_1h=quality.opposing_rsi_divergence,
        )


class EmpiricalTrendAlpha:
    """Parsimonious, research-only trend hypothesis with separately measured layers.

    Eligibility comes from one slow regime and one configured setup. EMA, MACD, RSI and
    ADX/DMI are deliberately compressed into a bounded ranking feature; they cannot veto
    an otherwise valid regime/setup. This makes each layer independently observable in the
    opportunity store instead of hiding it inside a large conjunction of price transforms.
    """

    profile = StrategyProfile.EMPIRICAL_TREND

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._quality = TrendQualityAlpha(settings)

    @property
    def _setup(self) -> EmpiricalSetup:
        return self._settings.empirical_setup

    @property
    def _breakout_lookback(self) -> int:
        return self._settings.empirical_breakout_4h_bars

    @property
    def _regime_prefix(self) -> str:
        return "empirical"

    def evaluate(
        self,
        one_hour: Sequence[Candle],
        four_hour: Sequence[Candle],
        daily: Sequence[Candle],
        one: IndicatorSnapshot,
        four: IndicatorSnapshot,
        daily_snapshot: IndicatorSnapshot,
        quote: Quote,
        direction: Direction,
        pullback: ContinuationTrigger,
    ) -> AlphaDecision:
        closed_4h = [candle for candle in four_hour if candle.closed]
        lookback = self._breakout_lookback
        prior = closed_4h[-lookback - 1 : -1]
        if len(prior) < lookback:
            raise ValueError(f"{lookback + 1} closed 4h candles required")
        breakout_level = (
            max(candle.high for candle in prior)
            if direction is Direction.LONG
            else min(candle.low for candle in prior)
        )
        breakout_distance = _directional(four.close - breakout_level, direction)
        breakout = breakout_distance > 0

        return_30d = _return_over(daily, 30)
        return_90d = _return_over(daily, 90)
        return_180d = _return_over(daily, 180)
        horizon_returns = (return_30d, return_90d, return_180d)
        aligned_horizons = sum(_directional(value, direction) > 0 for value in horizon_returns)
        momentum_direction_score = directional_momentum_score(horizon_returns, direction)
        daily_regime = (
            daily_snapshot.close > daily_snapshot.ema200
            if direction is Direction.LONG
            else daily_snapshot.close < daily_snapshot.ema200
        )
        setup = self._setup
        setup_passed = breakout if setup is EmpiricalSetup.BREAKOUT else pullback.passed
        setup_strength = (
            max(Decimal("0"), breakout_distance / four.atr14)
            if setup is EmpiricalSetup.BREAKOUT and four.atr14 > 0
            else Decimal("1")
            if pullback.passed
            else Decimal("0")
        )
        entry = quote.ask if direction is Direction.LONG else quote.bid
        quote_not_chased = (
            four.atr14 > 0
            and abs(entry - four.close) <= four.atr14 * self._settings.max_entry_chase_atr
        )
        checks = (
            (daily_regime, "daily price is not aligned with the daily EMA200 regime"),
            (aligned_horizons >= 2, "fewer than two 30d/90d/180d momentum horizons align"),
            (setup_passed, f"configured {setup.value} setup is not confirmed"),
            (four.atr14 > 0, "4h ATR is not positive"),
            (quote_not_chased, "entry quote moved beyond the 4h anti-chase boundary"),
        )
        failures = tuple(message for passed, message in checks if not passed)

        quality = self._quality.technical_quality(one_hour, four_hour, one, four, direction)

        def bounded(value: Decimal) -> Decimal:
            return min(Decimal("1"), max(Decimal("0"), value))

        if self.profile is StrategyProfile.LAYERED_MOMENTUM:
            momentum_score = max(Decimal("0"), momentum_direction_score) * Decimal("8")
        else:
            # Preserve empirical_tsmom_v5's historical ranking for reproducible comparisons.
            directional_90d = max(Decimal("0"), _directional(return_90d, direction))
            momentum_score = bounded(directional_90d / Decimal("0.50")) * Decimal("8")
        horizon_score = Decimal(max(0, aligned_horizons - 2)) * Decimal("3")
        setup_score = bounded(setup_strength / Decimal("2")) * Decimal("3")
        spread_score = bounded(
            Decimal("1") - quote.spread_bps / self._settings.max_spread_bps
        ) * Decimal("2")
        # Floor an eligible candidate at the configured ranking baseline. Technical
        # conflicts can reduce priority, never convert this layer back into a hard gate.
        score = min(
            Decimal("100"),
            max(
                self._settings.min_candidate_score,
                Decimal("70")
                + momentum_score
                + horizon_score
                + setup_score
                + spread_score
                + quality.score,
            ),
        )
        side = "bull" if direction is Direction.LONG else "bear"
        return AlphaDecision(
            eligible=not failures,
            intrinsic_score=score if not failures else Decimal("0"),
            evidence=(
                "daily EMA200 primary regime",
                f"{aligned_horizons}-of-3 30d/90d/180d time-series momentum horizons",
                f"one configured setup: {setup.value}",
                "EMA/MACD/RSI/ADX compressed into one bounded ranking feature",
            ),
            failures=failures,
            market_regime=f"{self._regime_prefix}_{side}_trend",
            breakout_level=breakout_level,
            return_7d=_return_over(daily, 7),
            return_30d=return_30d,
            return_90d=return_90d,
            return_180d=return_180d,
            volatility_percentile=_volatility_percentile(closed_4h, 120),
            indicator_confirmations=quality.confirmations,
            indicator_conflicts=quality.conflicts,
            macd_cross_1h=quality.macd_cross_1h,
            rsi_divergence_1h=quality.opposing_rsi_divergence,
            setup_signal=setup.value,
            setup_strength=setup_strength,
            aligned_momentum_horizons=aligned_horizons,
            directional_momentum_score=momentum_direction_score,
            technical_quality_score=quality.score,
        )


class LayeredMomentumAlpha(EmpiricalTrendAlpha):
    """V6 hard/soft-gate policy with symmetric long and short momentum math.

    Hard eligibility is deliberately limited to tradability, one slow directional regime,
    one configured timing setup, positive volatility, and an anti-chase boundary. Correlated
    price indicators remain bounded ranking evidence and cannot veto a valid hypothesis.
    """

    profile = StrategyProfile.LAYERED_MOMENTUM

    @property
    def _setup(self) -> EmpiricalSetup:
        return self._settings.layered_setup

    @property
    def _breakout_lookback(self) -> int:
        return self._settings.layered_breakout_4h_bars

    @property
    def _regime_prefix(self) -> str:
        return "layered"


class EarlyMomentumAlpha:
    """Hourly momentum ignition followed by a held retest and resumption."""

    profile = StrategyProfile.EARLY_MOMENTUM

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._quality = TrendQualityAlpha(settings)

    def evaluate(
        self,
        one_hour: Sequence[Candle],
        four_hour: Sequence[Candle],
        one: IndicatorSnapshot,
        four: IndicatorSnapshot,
        quote: Quote,
        direction: Direction,
    ) -> AlphaDecision:
        return_6h = _return_over(one_hour, 6)
        return_24h = _return_over(one_hour, 24)
        return_7d = _return_over(one_hour, 168)
        horizon_returns = (return_6h, return_24h, return_7d)
        aligned_horizons = sum(_directional(value, direction) > 0 for value in horizon_returns)
        momentum_score = normalized_directional_momentum_score(
            horizon_returns,
            (Decimal("0.02"), Decimal("0.05"), Decimal("0.15")),
            direction,
        )
        setup = impulse_retest(one_hour, direction, self._settings)
        medium_regime = (
            four.close > four.ema50 if direction is Direction.LONG else four.close < four.ema50
        )
        entry = quote.ask if direction is Direction.LONG else quote.bid
        quote_not_chased = (
            one.atr14 > 0
            and abs(entry - one.close) <= one.atr14 * self._settings.max_entry_chase_atr
        )
        checks = (
            (medium_regime, "4h price is not aligned with the EMA50 regime"),
            (aligned_horizons >= 2, "fewer than two 6h/24h/7d momentum horizons align"),
            (setup.passed, "; ".join(setup.failures)),
            (one.atr14 > 0, "1h ATR is not positive"),
            (quote_not_chased, "entry quote moved beyond the 1h anti-chase boundary"),
        )
        failures = tuple(message for passed, message in checks if not passed)
        quality = self._quality.technical_quality(one_hour, four_hour, one, four, direction)

        def bounded(value: Decimal) -> Decimal:
            return min(Decimal("1"), max(Decimal("0"), value))

        relative_volume = setup.breakout_relative_volume or Decimal("0")
        volume_score = bounded(relative_volume / Decimal("2")) * Decimal("4")
        retest_distance = setup.retest_distance_atr
        retest_score = (
            bounded(Decimal("1") - retest_distance / Decimal("2")) * Decimal("3")
            if retest_distance is not None
            else Decimal("0")
        )
        spread_score = bounded(
            Decimal("1") - quote.spread_bps / self._settings.max_spread_bps
        ) * Decimal("2")
        score = min(
            Decimal("100"),
            max(
                self._settings.min_candidate_score,
                Decimal("70")
                + max(Decimal("0"), momentum_score) * Decimal("8")
                + volume_score
                + retest_score
                + spread_score
                + quality.score,
            ),
        )
        side = "bull" if direction is Direction.LONG else "bear"
        breakout_level = setup.breakout_level or one.close
        return AlphaDecision(
            eligible=not failures,
            intrinsic_score=score if not failures else Decimal("0"),
            evidence=(
                "4h price/EMA50 medium-term regime",
                f"{aligned_horizons}-of-3 6h/24h/7d directional momentum horizons",
                "volume-confirmed 20h channel breakout",
                "held breakout retest and current closed-bar resumption",
                "EMA/MACD/RSI/ADX compressed into bounded ranking evidence",
            ),
            failures=failures,
            market_regime=f"early_{side}_momentum",
            breakout_level=breakout_level,
            return_6h=return_6h,
            return_24h=return_24h,
            return_7d=return_7d,
            return_30d=_return_over(four_hour, 180),
            volatility_percentile=_volatility_percentile(one_hour, 120),
            indicator_confirmations=quality.confirmations,
            indicator_conflicts=quality.conflicts,
            macd_cross_1h=quality.macd_cross_1h,
            rsi_divergence_1h=quality.opposing_rsi_divergence,
            setup_signal="volume_breakout_retest",
            setup_strength=volume_score + retest_score,
            aligned_momentum_horizons=aligned_horizons,
            directional_momentum_score=momentum_score,
            technical_quality_score=quality.score,
            breakout_relative_volume=setup.breakout_relative_volume,
            breakout_age_bars=setup.breakout_age_bars,
        )
