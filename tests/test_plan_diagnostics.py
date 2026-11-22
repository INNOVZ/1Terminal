import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from algotrader.application.bearish_continuation import score_continuation
from algotrader.domain.execution_brackets import build_long_bracket
from algotrader.domain.models import Direction
from algotrader.domain.plan_diagnostics import BracketContext, describe_plan
from tests.test_bearish_continuation import fixture

D=Decimal
CASES=json.loads((Path(__file__).parent/'fixtures/plan_geometry_frozen_seven.json').read_text())


def wire(value):
    if isinstance(value,D):
        return str(value)
    if isinstance(value,dict):
        return {k:wire(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):
        return [wire(v) for v in value]
    return value


@pytest.mark.parametrize('case',CASES,ids=[r['symbol'] for r in CASES])
def test_recorded_bracket_numbers_unchanged_and_diagnostics_match_real_trace(case):
    args={k:D(v) for k,v in case['inputs'].items()}
    bracket=build_long_bracket(**args)
    actual=wire(asdict(bracket))
    context=actual.pop('diagnostic_context')
    assert actual==case['expected_bracket']
    assert context['t1_before_risk_sizing']==case['expected_initial_t1']
    assert (bracket.t1>D(case['expected_initial_t1'])) is case['target_extended']
    assert context['atr_timeframe']=='4h'


def describe(**changes):
    values=dict(direction=Direction.LONG, execution_mode='RETEST_ACCUMULATION',
                entry=D(100),stop=D(90),targets=(D(120),D(130)),reference_price=D(105),
                evaluated_at=datetime(2026,11,22,tzinfo=UTC),rr_target_index=2)
    return describe_plan(**{**values,**changes})


def test_long_geometry_has_explicit_units_reference_and_uncalibrated_horizons():
    ctx=BracketContext(D(2),'4h','swing_low_7d',D(110),(('30d POC',D(108)),('30d VAH',D(115))))
    result=describe(context=ctx)
    assert D(result['risk_pct'])==10
    assert D(result['t1_distance_pct'])==20
    assert D(result['t1_gross_r'])==2
    assert D(result['risk_atr'])==5
    assert result['entry_mode']=='retest'
    assert D(result['entry_change_from_scan_price_pct'])<0
    assert result['t1_extended_after_risk_sizing'] is True
    assert result['nearest_recorded_reference']['label']=='30d POC'
    assert D(result['nearest_recorded_reference']['gross_r'])==D('.8')
    assert result['horizon_validation']=={'24h':'not_validated','48h':'not_validated'}
    assert result['selection_affected'] is False


def test_short_direction_and_missing_context_do_not_fabricate_volatility():
    result=describe(direction=Direction.SHORT,execution_mode='BREAKDOWN_TRIGGER',stop=D(110),targets=(D(80),),
                    fallback_levels=(('30d VAL',D(90)),('30d POC',D(105))))
    assert D(result['risk_pct'])==10 and D(result['t1_distance_pct'])==20
    assert result['entry_mode']=='conditional_trigger'
    assert result['atr_timeframe'] is None and result['risk_atr'] is None
    assert result['t1_extended_after_risk_sizing'] is None
    assert result['nearest_recorded_reference']['label']=='30d VAL'
    assert result['context_status']=='not_recorded'


def test_bad_optional_context_and_prices_never_become_safe_or_calibrated():
    result=describe(context=BracketContext(D('NaN'),'4h'),reference_price=D(0))
    assert result['risk_atr'] is None and result['entry_change_from_scan_price_pct'] is None
    assert describe(stop=D(100))['status']=='unavailable'
    assert describe(targets=())['status']=='unavailable'
    assert describe(entry=D('NaN'))['status']=='unavailable'


def test_serialization_only_adds_diagnostics_and_keeps_unknown_context():
    h,q,inst,quote,d=fixture()
    opp=score_continuation(inst,quote,h,q,now=d.observed_at,derivatives=d,note=lambda *a,**kw:None,
                           macro_narrative='test',beta=D(1),residual=D(0),is_discovery_tier=False)
    assert opp is not None
    baseline=opp.as_dict()
    with_context=replace(opp,bracket_context=BracketContext(opp.atr14,'4h','failed_retest')).as_dict()
    assert baseline.pop('plan_diagnostics')['context_status']=='not_recorded'
    with_context.pop('plan_diagnostics')
    assert baseline==with_context


def test_extreme_optional_volatility_cannot_break_candidate_serialization():
    result=describe(context=BracketContext(D('1e-100'),'4h'))
    assert result['risk_atr'] is None
    json.dumps(result,allow_nan=False)
    result=describe(context=BracketContext(D('1e-1000000'),'4h'))
    assert result['status']=='unavailable'
    assert result['selection_affected'] is False
