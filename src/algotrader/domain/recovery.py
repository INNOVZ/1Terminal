"""Declared recovery-v1 research hypothesis and structural price plan."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from algotrader.domain.execution_brackets import (
    ExecutionBracket,
    InvalidExecutionBracket,
    valid_execution_prices,
)
from algotrader.domain.models import Candle, DerivativesSnapshot, Direction, Quote
from algotrader.domain.precision import ceil_to_increment, floor_to_increment

RECOVERY_POLICY_VERSION = "recovery-reclaim-v1-hourly-local-risk"


def recovery_flow_confirmed(derivatives: DerivativesSnapshot | None, at: datetime) -> bool:
    """Shared initial qualification and subsequent confirmation flow contract."""
    if (derivatives is None or derivatives.degraded_telemetry
            or derivatives.observed_at.tzinfo is None or derivatives.observed_at.utcoffset() is None):
        return False
    values = (derivatives.funding_rate, derivatives.open_interest_change_1h,
              derivatives.open_interest_change_4h, derivatives.taker_buy_sell_ratio_4h)
    if (not all(value is not None and value.is_finite() for value in values)
            or not -5 <= (at - derivatives.observed_at).total_seconds() <= 300):
        return False
    funding, oi1, oi4, taker = values
    assert funding is not None and oi1 is not None and oi4 is not None and taker is not None
    return funding <= Decimal("0.00025") and oi1 >= Decimal("-0.025") and oi4 >= 0 and taker >= 1


@dataclass(frozen=True, slots=True)
class RecoveryAssessment:
    gates: tuple[tuple[str, bool], ...]
    bracket: ExecutionBracket
    turnover_acceleration: Decimal
    pivot: Decimal

    @property
    def ready(self) -> bool:
        return all(passed for _, passed in self.gates[:4])


def recovery_bracket(
    *, close: Decimal, pivot: Decimal, local_low: Decimal, atr: Decimal,
    tick_size: Decimal, older_resistance: Sequence[Decimal],
) -> ExecutionBracket:
    if any(not value.is_finite() or value <= 0
           for value in (close, pivot, local_low, atr, tick_size, *older_resistance)):
        raise InvalidExecutionBracket("recovery bracket inputs must be positive and finite")
    entry = ceil_to_increment(max(close, pivot) + atr * Decimal("0.05"), tick_size)
    stop = floor_to_increment(min(local_low - atr * Decimal("0.25"), entry - atr), tick_size)
    risk = entry - stop
    if risk > min(atr * 3, entry * Decimal("0.08")):
        raise InvalidExecutionBracket("recovery structural risk exceeds three ATR or 8%")
    overhead = [level for level in older_resistance if level > entry]
    if overhead and min(overhead) - entry < risk * 2:
        raise InvalidExecutionBracket("recovery older resistance leaves less than 2R")
    t1, t2, t3 = (ceil_to_increment(entry + risk * multiple, tick_size) for multiple in (2, 3, 4))
    targets = (t1, t2, t3)
    if not valid_execution_prices(Direction.LONG, entry, stop, targets):
        raise InvalidExecutionBracket("recovery requires positive ordered prices")
    r1, r2, r3 = ((target - entry) / risk for target in targets)
    return ExecutionBracket(
        retest_entry=floor_to_increment(pivot, tick_size), breakout_trigger=entry,
        invalidation_stop=stop, effective_entry=entry, suggested_entry=entry,
        t1=targets[0], t2=targets[1], t3=targets[2], targets=targets,
        risk_unit=risk, r_t1=r1, r_t2=r2, r_t3=r3, asymmetric_rr=r1,
        execution_mode="CEILING_BREAKOUT", retest_stop=stop, breakout_stop=stop,
    )


def assess_recovery(
    hourly: Sequence[Candle], *, quote: Quote, atr: Decimal, ema20: Decimal,
    ema50_4h: Decimal, poc_30d: Decimal, tick_size: Decimal, emerging: bool,
    derivatives: DerivativesSnapshot | None, cutoff: datetime, decision_at: datetime,
) -> RecoveryAssessment:
    if len(hourly) < 24 or not atr.is_finite() or atr <= 0:
        raise InvalidExecutionBracket("recovery requires positive ATR and 24 closed hours")
    window = hourly[-24:]
    if (decision_at < cutoff
            or any(not c.closed or c.closed_at > cutoff
                   or not 3599 <= (c.closed_at - c.opened_at).total_seconds() <= 3600.001 for c in window)
            or any(b.opened_at - a.opened_at != timedelta(hours=1)
                   for a, b in zip(window, window[1:], strict=False))
            or not 0 <= (cutoff - window[-1].closed_at).total_seconds() <= 3601):
        raise InvalidExecutionBracket("recovery requires contiguous point-in-time closed history")
    values = [ema20, ema50_4h, poc_30d, tick_size, quote.bid, quote.ask, quote.last,
              quote.quote_volume_24h]
    values.extend(value for c in window for value in (c.open, c.high, c.low, c.close))
    if (any(not value.is_finite() or value <= 0 for value in values)
            or any(not value.is_finite() or value < 0 for c in window for value in (c.volume, c.quote_volume))
            or any(not c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high for c in window)):
        raise InvalidExecutionBracket("recovery input prices and turnover must be positive and finite")
    close = window[-1].close
    pivot = max(c.high for c in window[-4:-1])
    six_bar_low = min(c.low for c in window[-6:])
    recent_low = min(c.low for c in window[-3:])
    prior_low = min(c.low for c in window[-6:-3])
    previous_turnover = sum((c.quote_volume for c in window[:-6]), Decimal("0")) / 18
    if previous_turnover <= 0:
        raise InvalidExecutionBracket("recovery requires positive turnover baseline")
    acceleration = sum((c.quote_volume for c in window[-6:]), Decimal("0")) / 6 / previous_turnover
    flow = recovery_flow_confirmed(derivatives, decision_at)
    older_res: Sequence[Decimal] = () if (acceleration >= Decimal("1.5") and flow) else (ema50_4h, poc_30d)
    try:
        bracket = recovery_bracket(close=close, pivot=pivot, local_low=six_bar_low, atr=atr,
                                   tick_size=tick_size, older_resistance=older_res)
    except InvalidExecutionBracket:
        if recent_low > prior_low:
            bracket = recovery_bracket(close=close, pivot=pivot, local_low=recent_low, atr=atr,
                                       tick_size=tick_size, older_resistance=older_res)
        else:
            raise
    local_reclaim = (
        close > pivot and close >= ema20
        and min(c.low for c in window[-3:]) > min(c.low for c in window[-6:-3])
        and (close < ema50_4h or close < poc_30d)
    )
    controlled_extension = (
        0 <= close - ema20 <= atr * Decimal("1.5")
        and close - pivot <= atr
        and quote.last - pivot <= atr
        and quote.last - ema20 <= atr * Decimal("1.5")
        and window[-1].high - window[-1].low <= atr * Decimal("1.5")
    )
    participation = emerging and acceleration > 1
    liquidity = (
        quote.quote_volume_24h >= Decimal("500000")
        and quote.last > bracket.invalidation_stop
        and 0 <= quote.spread_bps <= 25
        and -5 <= (decision_at - quote.observed_at).total_seconds() <= 600
    )
    flow = recovery_flow_confirmed(derivatives, decision_at)
    return RecoveryAssessment(
        gates=(("R1_LOCAL_RECOVERY", local_reclaim), ("R2_CONTROLLED_EXTENSION", controlled_extension),
               ("R3_RELATIVE_PARTICIPATION", participation), ("R4_LIQUIDITY_AND_RISK", liquidity),
               ("R5_DERIVATIVES_CONFIRMATION", flow)),
        bracket=bracket, turnover_acceleration=acceleration, pivot=pivot,
    )
