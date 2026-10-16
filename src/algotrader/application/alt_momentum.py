from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from algotrader.application.research import beta_correlation
from algotrader.domain.indicators import adx, ema, macd, rsi
from algotrader.domain.models import Candle, Instrument, Quote

ZERO = Decimal("0")
ONE = Decimal("1")


def _return_over(candles: Sequence[Candle], bars: int) -> Decimal:
    if bars <= 0 or len(candles) <= bars:
        raise ValueError(f"{bars + 1} closed candles required")
    earlier = candles[-(bars + 1)].close
    if earlier <= 0:
        raise ValueError("return reference close must be positive")
    return candles[-1].close / earlier - ONE


def _quote_volume(candle: Candle) -> Decimal:
    return candle.quote_volume if candle.quote_volume > 0 else candle.close * candle.volume


def _participation_ratio(candles: Sequence[Candle]) -> Decimal:
    recent = [_quote_volume(candle) for candle in candles[-6:]]
    baseline = [_quote_volume(candle) for candle in candles[-30:-6]]
    baseline_mean = sum(baseline, ZERO) / Decimal(len(baseline))
    if baseline_mean <= 0:
        return ZERO
    return (sum(recent, ZERO) / Decimal(len(recent))) / baseline_mean


@dataclass(frozen=True, slots=True)
class AltMomentumRow:
    instrument: Instrument
    observed_at: str
    close: Decimal
    quote_volume_24h: Decimal
    spread_bps: Decimal
    return_6h: Decimal
    previous_return_6h: Decimal
    return_24h: Decimal
    return_7d: Decimal
    acceleration_6h: Decimal
    btc_beta: Decimal
    btc_correlation: Decimal
    residual_return_6h: Decimal
    residual_return_24h: Decimal
    residual_return_7d: Decimal
    participation_ratio: Decimal
    ema20: Decimal
    ema50: Decimal
    rsi14: Decimal
    macd_histogram: Decimal
    adx14: Decimal
    plus_di: Decimal
    minus_di: Decimal
    phase: str
    long_score: Decimal = ZERO
    short_score: Decimal = ZERO

    def as_dict(self) -> dict[str, object]:
        return {
            "instrument": str(self.instrument.id),
            "phase": self.phase,
            "long_score": str(self.long_score),
            "short_score": str(self.short_score),
            "close": str(self.close),
            "quote_volume_24h": str(self.quote_volume_24h),
            "spread_bps": str(self.spread_bps),
            "return_6h": str(self.return_6h),
            "previous_return_6h": str(self.previous_return_6h),
            "return_24h": str(self.return_24h),
            "return_7d": str(self.return_7d),
            "acceleration_6h": str(self.acceleration_6h),
            "btc_beta_7d": str(self.btc_beta),
            "btc_correlation_7d": str(self.btc_correlation),
            "residual_return_6h": str(self.residual_return_6h),
            "residual_return_24h": str(self.residual_return_24h),
            "residual_return_7d": str(self.residual_return_7d),
            "participation_ratio_6h_vs_24h": str(self.participation_ratio),
            "above_ema20": self.close > self.ema20,
            "above_ema50": self.close > self.ema50,
            "rsi14": str(self.rsi14),
            "macd_histogram": str(self.macd_histogram),
            "adx14": str(self.adx14),
            "plus_di": str(self.plus_di),
            "minus_di": str(self.minus_di),
            "observed_at": self.observed_at,
        }


def alt_momentum_row(
    instrument: Instrument,
    quote: Quote,
    asset_candles: Sequence[Candle],
    btc_candles: Sequence[Candle],
) -> AltMomentumRow:
    """Build one point-in-time altcoin observation from closed hourly bars."""

    asset = sorted((bar for bar in asset_candles if bar.closed), key=lambda bar: bar.closed_at)
    benchmark = sorted((bar for bar in btc_candles if bar.closed), key=lambda bar: bar.closed_at)
    if len(asset) < 169 or len(benchmark) < 169:
        raise ValueError("169 closed hourly candles required for alt momentum")
    beta, correlation = beta_correlation(asset, benchmark, lookback=168)
    if beta is None or correlation is None:
        raise ValueError("aligned BTC history cannot produce beta and correlation")

    return_6h = _return_over(asset, 6)
    previous_6h_reference = asset[-13].close
    previous_6h_end = asset[-7].close
    if previous_6h_reference <= 0:
        raise ValueError("previous 6h return reference must be positive")
    previous_return_6h = previous_6h_end / previous_6h_reference - ONE
    return_24h = _return_over(asset, 24)
    return_7d = _return_over(asset, 168)
    btc_return_6h = _return_over(benchmark, 6)
    btc_return_24h = _return_over(benchmark, 24)
    btc_return_7d = _return_over(benchmark, 168)
    residual_6h = return_6h - beta * btc_return_6h
    residual_24h = return_24h - beta * btc_return_24h
    residual_7d = return_7d - beta * btc_return_7d
    acceleration = return_6h - previous_return_6h

    closes = [bar.close for bar in asset]
    ema20 = ema(closes, 20)[-1]
    ema50 = ema(closes, 50)[-1]
    rsi14 = rsi(closes, 14)[-1]
    histogram = macd(closes)[2][-1]
    adx_values, plus_values, minus_values = adx(asset, 14)
    close = asset[-1].close
    absolute_positive = sum(value > 0 for value in (return_6h, return_24h, return_7d))
    residual_positive = sum(value > 0 for value in (residual_6h, residual_24h, residual_7d))
    absolute_negative = 3 - absolute_positive
    residual_negative = 3 - residual_positive
    if absolute_positive >= 2 and residual_positive >= 2 and close > ema50:
        phase = "confirmed_up"
    elif return_6h > 0 and residual_6h > 0 and acceleration > 0 and close > ema20:
        phase = "emerging_up"
    elif absolute_negative >= 2 and residual_negative >= 2 and close < ema50:
        phase = "confirmed_down"
    elif return_6h < 0 and residual_6h < 0 and acceleration < 0 and close < ema20:
        phase = "emerging_down"
    else:
        phase = "mixed"

    return AltMomentumRow(
        instrument=instrument,
        observed_at=asset[-1].closed_at.isoformat(),
        close=close,
        quote_volume_24h=quote.quote_volume_24h,
        spread_bps=quote.spread_bps,
        return_6h=return_6h,
        previous_return_6h=previous_return_6h,
        return_24h=return_24h,
        return_7d=return_7d,
        acceleration_6h=acceleration,
        btc_beta=beta,
        btc_correlation=correlation,
        residual_return_6h=residual_6h,
        residual_return_24h=residual_24h,
        residual_return_7d=residual_7d,
        participation_ratio=_participation_ratio(asset),
        ema20=ema20,
        ema50=ema50,
        rsi14=rsi14,
        macd_histogram=histogram,
        adx14=adx_values[-1],
        plus_di=plus_values[-1],
        minus_di=minus_values[-1],
        phase=phase,
    )


def _percentile_ranks(values: Sequence[Decimal]) -> list[Decimal]:
    if len(values) == 1:
        return [Decimal("0.5")]
    result: list[Decimal] = []
    denominator = Decimal(len(values) - 1)
    for value in values:
        less = sum(item < value for item in values)
        equal = sum(item == value for item in values)
        average_index = Decimal(less) + Decimal(equal - 1) / Decimal("2")
        result.append(average_index / denominator)
    return result


def rank_alt_momentum(rows: Sequence[AltMomentumRow]) -> list[AltMomentumRow]:
    """Apply the preregistered symmetric cross-sectional percentile score."""

    if not rows:
        return []
    feature_names = (
        "residual_return_6h",
        "residual_return_24h",
        "residual_return_7d",
        "acceleration_6h",
        "participation_ratio",
    )
    ranks = {
        name: _percentile_ranks([getattr(row, name) for row in rows]) for name in feature_names
    }
    weights = (Decimal("30"), Decimal("25"), Decimal("15"), Decimal("20"), Decimal("10"))
    ranked: list[AltMomentumRow] = []
    for index, row in enumerate(rows):
        percentiles = [ranks[name][index] for name in feature_names]
        long_score = sum(
            (percentile * weight for percentile, weight in zip(percentiles, weights, strict=True)),
            ZERO,
        )
        short_percentiles = [ONE - value for value in percentiles[:4]] + [percentiles[4]]
        short_score = sum(
            (
                percentile * weight
                for percentile, weight in zip(short_percentiles, weights, strict=True)
            ),
            ZERO,
        )
        ranked.append(
            replace(
                row,
                long_score=long_score.quantize(Decimal("0.01")),
                short_score=short_score.quantize(Decimal("0.01")),
            )
        )
    return ranked
