from datetime import timedelta
from decimal import Decimal

from algotrader.domain.models import Candle
from scripts.research_bracket_horizon_audit import (
    aggregate,
    path_metrics,
    score_outcome,
    variant_plan,
)
from tests.test_setup_outcome_ledger import START

D=Decimal


def plan():
    return {'symbol':'TESTUSDT','direction':'long','entry':'100','stop':'90',
            'targets':['120','130','140'],'execution_mode':'CEILING_BREAKOUT',
            'first_visible_at':START.isoformat()}


def features():
    return {'tick':D('.1'),'local_low_6h':D('96.1'),'atr_1h':D('1.5')}


def test_target_only_and_local_stop_variants_keep_entry_and_original_plan():
    original=plan()
    reduced,reason=variant_plan(original,features(),'target_1r')
    assert reason is None and reduced['entry']=='100' and reduced['stop']=='90'
    assert D(reduced['targets'][0])==110
    local,_=variant_plan(original,features(),'local_stop_only')
    assert D(local['stop'])==D('95.8') and D(local['targets'][0])==120
    both,_=variant_plan(original,features(),'local_stop_target_1_5r')
    assert D(both['stop'])==D('95.8') and D(both['targets'][0])==D('106.3')
    assert original==plan()


def test_invalid_local_structure_is_reported_and_local_rule_never_widens_stop():
    unavailable,reason=variant_plan(plan(),{**features(),'local_low_6h':D('110')},'local_stop_only')
    assert unavailable is None and reason=='local_structure_stop_not_below_pending_entry'
    unchanged,_=variant_plan(plan(),{**features(),'local_low_6h':D('80')},'local_stop_only')
    assert unchanged['stop']=='90'


def test_risk_renormalization_cannot_be_misreported_as_price_profit():
    p={**plan(),'stop':'98','targets':['103']}
    outcome={'status':'target_before_stop','net_r':'1.4'}
    score=score_outcome(plan(),p,[],outcome,D('20'))
    assert D(score['net_return_pct'])==D('2.8')
    assert D(score['net_R_original'])==D('.28')
    assert D(score['net_R_variant'])==D('1.4')


def test_missing_variants_remain_in_denominators_and_block_full_mean():
    known={'symbol':'ONE','variant':'local_stop_only','outcome':{'status':'target_before_stop'},
           'score':{'net_return_pct':'2','net_R_original':'.2','net_R_variant':'1'}}
    unknown={'symbol':'TWO','variant':'local_stop_only','outcome':{'status':'variant_unavailable'},
             'score':{'net_return_pct':None,'net_R_original':None,'net_R_variant':None}}
    stats=aggregate([known,unknown])['local_stop_only']
    assert stats['issued_candidates']==2
    assert stats['counts']['variant_unavailable']==1
    assert stats['scores']['net_return_pct']['mean_all_issued'] is None
    assert stats['scores']['net_return_pct']['conditional_mean_known_only']=='2'


def test_excursions_ignore_pre_entry_and_trigger_bar_extremes():
    def bar(i,high,low):
        return Candle(START+timedelta(minutes=i),START+timedelta(minutes=i+1),D(100),D(high),D(low),D(100),D(1))
    candles=[bar(0,500,1),bar(1,400,2),bar(2,105,95)]
    out={'triggered_at':(START+timedelta(minutes=1)).isoformat(),
         'horizon_end':(START+timedelta(hours=24)).isoformat()}
    metrics=path_metrics(plan(),candles,out)
    assert D(metrics['post_trigger_MFE_pct'])==5
    assert D(metrics['post_trigger_MAE_pct'])==-5


def test_unresolved_mark_is_not_a_realized_exit_and_no_entry_is_not_a_loss():
    candle=Candle(START,START+timedelta(minutes=1),D(100),D(101),D(97),D(98),D(1))
    out={'status':'triggered_unresolved','horizon_end':(START+timedelta(hours=24)).isoformat()}
    value=score_outcome(plan(),plan(),[candle],out,D(20))
    assert value['basis']=='diagnostic_horizon_mark_not_realized_exit'
    assert D(value['net_return_pct'])==D('-2.2')
    assert score_outcome(plan(),plan(),[],{'status':'untriggered_expired'},D(20))['net_return_pct']=='0'


def test_pre_alert_features_exclude_future_candles_and_reject_history_gaps():
    import pytest

    from algotrader.domain.scan_observations import RecordedCall
    from scripts.research_bracket_horizon_audit import pre_alert_features
    from tests.test_bearish_continuation import fixture

    inst=fixture()[2]
    def bars(hours):
        return [Candle(START-timedelta(hours=hours*(i+1)),START-timedelta(hours=hours*i),
                       D(100),D(101),D(99),D(100),D(1)) for i in reversed(range(60))]
    hourly,four=bars(1),bars(4)
    future=Candle(START,START+timedelta(hours=1),D(100),D(500),D('.01'),D(100),D(1))
    def calls(h):
        return [RecordedCall('candles',0,{'instrument':inst,'interval':'1h'},h),
                RecordedCall('candles',0,{'instrument':inst,'interval':'4h'},four)]
    baseline=pre_alert_features(calls(hourly),inst.id.symbol,START)
    with_future=pre_alert_features(calls([*hourly,future]),inst.id.symbol,START)
    assert baseline['atr_1h']==with_future['atr_1h']
    assert baseline['local_low_6h']==with_future['local_low_6h']==99
    with pytest.raises(ValueError,match='non-contiguous'):
        pre_alert_features(calls(hourly[:20]+hourly[21:]),inst.id.symbol,START)


def test_trace_records_actual_target_rounding_and_restores_original_builder():
    from algotrader.domain import execution_brackets as module
    from algotrader.domain.execution_brackets import build_long_bracket
    from algotrader.domain.indicators import atr, volume_profile
    from scripts.research_bracket_horizon_audit import trace_original

    tick=D('.1')
    hourly=[Candle(START-timedelta(hours=i+1),START-timedelta(hours=i),
                   D(100),D(101),D(99),D(100),D(1)) for i in reversed(range(60))]
    four=[Candle(START-timedelta(hours=4*(i+1)),START-timedelta(hours=4*i),
                 D(100),D(105),D(95),D(100),D(1)) for i in reversed(range(180))]
    vp=volume_profile(four,bins=50,tick_size=tick)
    b=build_long_bracket(close=D(100),ema50_4h=D(100),poc_30d=vp.poc_price,
                         atr14_4h=atr(four,14)[-1],local_compression_high_4h=D(105),
                         swing_low_4h=D(95),high_30d=D(105),vah_30d=vp.vah_price,
                         tick_size=tick,val_30d=vp.val_price,resistance_absorption_score=D(73),
                         handle_low_4h=D(95))
    p={'symbol':'TESTUSDT','entry':str(b.suggested_entry),'stop':str(b.invalidation_stop),
       'targets':[str(t) for t in b.targets],'execution_mode':b.execution_mode}
    c={'resistance_absorption_score':'73','poc_price':str(vp.poc_price),
       'vah_price':str(vp.vah_price),'val_price':str(vp.val_price)}
    f={'hourly':hourly,'four_hour':four,'tick':tick,'atr_4h':atr(four,14)[-1]}
    before=module.normalize_price
    result=trace_original(p,c,f)
    assert module.normalize_price is before
    assert D(result['initial_T1_before_R_scaling'])<=b.t1
    assert result['bracket']['t1']==str(b.t1)
