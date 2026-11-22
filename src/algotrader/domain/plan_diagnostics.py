"""Descriptive published-plan geometry. No selection, price changes or horizon forecasts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from algotrader.domain.models import Direction

DIAGNOSTICS_VERSION = 'plan-geometry-v1'


@dataclass(frozen=True, slots=True)
class BracketContext:
    atr_value: Decimal | None = None
    atr_timeframe: str | None = None
    stop_anchor: str | None = None
    t1_before_risk_sizing: Decimal | None = None
    levels: tuple[tuple[str, Decimal], ...] = ()


def _describe_plan(*, direction: Direction, execution_mode: str, entry: Decimal, stop: Decimal,
                  targets: tuple[Decimal, ...], reference_price: Decimal, evaluated_at: datetime,
                  rr_target_index: int, context: BracketContext | None = None,
                  fallback_levels: tuple[tuple[str, Decimal], ...] = ()) -> dict[str, Any]:
    base: dict[str, Any] = {'version': DIAGNOSTICS_VERSION, 'selection_affected': False,
        'horizon_validation': {'24h': 'not_validated', '48h': 'not_validated'},
        'semantics': 'published plan geometry, not observed fills or target-time probabilities',
        'snapshot_evaluated_at': evaluated_at.astimezone(UTC).isoformat() if evaluated_at.utcoffset() is not None else None,
        'reference_source': 'published_scan_price_may_be_closed_candle',
        'entry_mode': 'conditional_trigger' if execution_mode in (
            'CEILING_BREAKOUT','BREAKOUT_TRIGGER','BREAKDOWN_TRIGGER','BREAKOUT_EXPANSION','BREAKDOWN_EXPANSION')
            else 'retest' if execution_mode in ('RETEST_ACCUMULATION','WAIT_FOR_RETEST_ONLY') else 'unclassified',
        'published_rr_target_index': rr_target_index}
    if not targets or any(not p.is_finite() or p <= 0 for p in (entry,stop,*targets)):
        return {**base, 'status':'unavailable','reason':'invalid_plan_prices'}
    sign = Decimal(1) if direction is Direction.LONG else Decimal(-1)
    risk, reward = sign*(entry-stop), sign*(targets[0]-entry)
    if risk <= 0 or reward <= 0:
        return {**base,'status':'unavailable','reason':'invalid_plan_direction'}

    def scalar(value: Decimal) -> str | None:
        if not value.is_finite() or value.adjusted() > 15:
            return None
        return str(value.quantize(Decimal('.0001')))

    result = {**base, 'status':'available', 'entry':str(entry), 'stop':str(stop), 't1':str(targets[0]),
              'risk_pct':scalar(risk/entry*100), 't1_distance_pct':scalar(reward/entry*100),
              't1_gross_r':scalar(reward/risk),
              'entry_change_from_scan_price_pct':scalar((entry-reference_price)/reference_price*100)
                  if reference_price.is_finite() and reference_price>0 else None,
              'context_status':'recorded' if context else 'not_recorded',
              'stop_anchor':context.stop_anchor if context else None,
              'risk_atr':None,'t1_distance_atr':None,'atr_timeframe':None,
              'initial_t1':None,'t1_extended_after_risk_sizing':None,'nearest_recorded_reference':None}
    if context:
        vol=context.atr_value
        if vol is not None and vol.is_finite() and vol>0 and context.atr_timeframe in ('1h','4h','1d'):
            result.update(risk_atr=scalar(risk/vol),t1_distance_atr=scalar(reward/vol),atr_timeframe=context.atr_timeframe)
        initial=context.t1_before_risk_sizing
        if initial is not None and initial.is_finite() and initial>0:
            result.update(initial_t1=str(initial),t1_extended_after_risk_sizing=sign*(targets[0]-initial)>0)
    levels=context.levels if context else fallback_levels
    relevant=[(sign*(price-entry),name,price) for name,price in levels
              if price.is_finite() and price>0 and sign*(price-entry)>0]
    if relevant:
        distance,name,price=min(relevant,key=lambda row:(row[0],row[1]))
        result['nearest_recorded_reference']={'label':name,'price':str(price),
            'distance_pct':scalar(distance/entry*100),'gross_r':scalar(distance/risk),
            'before_t1':distance<reward}
    return result


def describe_plan(*, direction: Direction, execution_mode: str, entry: Decimal, stop: Decimal,
                  targets: tuple[Decimal, ...], reference_price: Decimal, evaluated_at: datetime,
                  rr_target_index: int, context: BracketContext | None = None,
                  fallback_levels: tuple[tuple[str, Decimal], ...] = ()) -> dict[str, Any]:
    try:
        return _describe_plan(direction=direction, execution_mode=execution_mode, entry=entry,
            stop=stop, targets=targets, reference_price=reference_price, evaluated_at=evaluated_at,
            rr_target_index=rr_target_index, context=context, fallback_levels=fallback_levels)
    except ArithmeticError:
        # Optional display diagnostics must never prevent an otherwise valid publication.
        return {'version':DIAGNOSTICS_VERSION, 'status':'unavailable',
                'reason':'geometry_calculation_unavailable', 'selection_affected':False,
                'horizon_validation':{'24h':'not_validated','48h':'not_validated'}}
