"""Fixed-alert, offline bracket mechanics audit; never modifies live strategy or source evidence."""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from algotrader.domain import execution_brackets as bracket_module
from algotrader.domain.execution_brackets import build_long_bracket
from algotrader.domain.indicators import atr, ema, volume_profile
from algotrader.domain.precision import ceil_to_increment, floor_to_increment, normalize_price
from algotrader.domain.setup_outcomes import OutcomePolicy, aware, evaluate_setup
from algotrader.infrastructure.rolling_discovery import unpack
from algotrader.infrastructure.scan_archive import LocalScanArchive
from algotrader.infrastructure.setup_outcome_ledger import SetupOutcomeLedger, digest
from scripts.replay_atlas_v3_rolling import sha256_file
from scripts.research_setup_backfill import same_candidate, write_new_or_equal
from scripts.research_setup_backfill_verify import rederive_minutes

D = Decimal
VARIANTS = ('baseline', 'target_1r', 'local_stop_only', 'local_stop_target_1_5r')


def wire(value):
    if isinstance(value, D):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: wire(v) for k,v in value.items()}
    if isinstance(value, (tuple,list)):
        return [wire(v) for v in value]
    return value


def histories(calls, symbol, interval, cutoff):
    histories_found = []
    tick = None
    for call in calls:
        if call.error or call.method != 'candles' or call.arguments['interval'] != interval:
            continue
        inst = call.arguments['instrument']
        if inst.id.symbol != symbol:
            continue
        tick = inst.price_tick
        history = sorted((c for c in call.result if c.closed and c.closed_at <= cutoff),key=lambda c:c.closed_at)
        if len({c.opened_at for c in history}) != len(history):
            raise ValueError('duplicate pre-alert candle')
        histories_found.append(history)
    if not histories_found or tick is None:
        raise ValueError('pre-alert feature input unavailable')
    return max(histories_found,key=len), tick


def pre_alert_features(calls, symbol, cutoff):
    hourly,tick = histories(calls,symbol,'1h',cutoff)
    four,_ = histories(calls,symbol,'4h',cutoff)
    if len(hourly)<15 or len(four)<50:
        raise ValueError('insufficient recorded feature warmup')
    if any(b.opened_at - a.opened_at != timedelta(hours=1) for a,b in zip(hourly,hourly[1:],strict=False)):
        raise ValueError('non-contiguous pre-alert hourly history')
    return {'cutoff':cutoff, 'hourly':hourly, 'four_hour':four, 'tick':tick,
            'atr_1h':atr(hourly,14)[-1], 'atr_4h':atr(four,14)[-1],
            'local_low_6h':min(c.low for c in hourly[-6:])}


def trace_original(plan,candidate,features):
    hourly,four,tick = features['hourly'],features['four_hour'],features['tick']
    base = four[-180:]
    profile = volume_profile(base,bins=50,tick_size=tick)
    args = {'close':hourly[-1].close,'ema50_4h':ema([c.close for c in four],50)[-1],
            'poc_30d':profile.poc_price,'vah_30d':profile.vah_price,'val_30d':profile.val_price,
            'atr14_4h':features['atr_4h'],'local_compression_high_4h':max(c.high for c in four[-18:]),
            'swing_low_4h':min(c.low for c in four[-42:]),'high_30d':max(c.high for c in base),
            'tick_size':tick,'resistance_absorption_score':D(candidate['resistance_absorption_score']),
            'handle_low_4h':min(c.low for c in four[-12:])}
    rounding=[]
    original_round=bracket_module.normalize_price
    def recorded_round(price,increment):
        rounded=original_round(price,increment)
        rounding.append({'raw':price,'rounded':rounded})
        return rounded
    # Instrument the existing calculation; do not reimplement target-selection formulas.
    with patch.object(bracket_module,'normalize_price',recorded_round):
        bracket = build_long_bracket(**args)
    if not rounding:
        raise ValueError('missing target trace')
    initial_t1=rounding[0]['rounded']
    if (bracket.suggested_entry != D(plan['entry']) or bracket.invalidation_stop != D(plan['stop'])
            or bracket.targets != tuple(map(D,plan['targets'])) or bracket.execution_mode != plan['execution_mode']):
        raise ValueError('current bracket calculation does not reproduce frozen plan: '+plan['symbol'])
    for key, price in (('poc_price',profile.poc_price),('vah_price',profile.vah_price),('val_price',profile.val_price)):
        if D(candidate[key]) != price:
            raise ValueError('profile reconstruction mismatch')
    entry,stop,target,volatility = bracket.effective_entry,bracket.invalidation_stop,bracket.t1,features['atr_4h']
    risk = entry-stop
    if bracket.execution_mode=='CEILING_BREAKOUT':
        minimum = max(volatility,entry*D('.025'))
        cap = max(3*volatility,entry*D('.08'))
        options={'handle_less_buffer':args['handle_low_4h']-D('.20')*volatility,
                 'minimum_distance_stop':entry-minimum,'maximum_distance_stop':entry-cap}
        inner=min(options['handle_less_buffer'],options['minimum_distance_stop'])
        binding='maximum_distance_stop' if options['maximum_distance_stop']>=inner else 'handle_or_minimum_distance'
    else:
        options={'seven_day_swing_low_less_buffer':args['swing_low_4h']-D('.80')*volatility,
                 'thirty_day_VAL_less_buffer':profile.val_price-D('.60')*volatility,
                 'minimum_distance_stop':bracket.retest_entry-max(D('1.2')*volatility,bracket.retest_entry*D('.035'))}
        binding=min(options,key=options.get)
    floor=normalize_price(entry+D('1.50')*risk,tick)
    target_binding = '1.50R_floor_raised_target' if target==floor and target>initial_t1 else 'initial_structural_or_ATR_target_preserved'
    if target==normalize_price(profile.vah_price,tick):
        target_binding='VAH_after_POC_room_test' if args['close']<profile.poc_price and profile.poc_price-entry<risk else 'VAH'
    return wire({'inputs':args,'bracket':asdict(bracket),'stop_candidates':options,
                 'stop_binding':binding,'T1_binding':target_binding,'initial_T1_before_R_scaling':initial_t1,
                 'target_raised_after_stop_risk_known':target>initial_t1,
                 'risk_ATR_4h':risk/volatility,
                 'T1_ATR_4h':(target-entry)/volatility,'risk_pct':risk/entry*100,
                 'T1_pct':(target-entry)/entry*100,'POC_distance_R':(profile.poc_price-entry)/risk})


def variant_plan(plan,features,name):
    if name not in VARIANTS or plan['direction']!='long':
        raise ValueError('unsupported bracket audit variant/direction')
    p={**plan,'targets':list(plan['targets'])}
    entry,old_stop,target = D(p['entry']),D(p['stop']),D(p['targets'][0])
    tick=features['tick']
    if name.startswith('local_stop'):
        local=floor_to_increment(features['local_low_6h']-D('.20')*features['atr_1h'],tick)
        if local>=entry:
            return None,'local_structure_stop_not_below_pending_entry'
        p['stop']=str(max(old_stop,local))
    if name=='target_1r':
        target=min(target,ceil_to_increment(entry+(entry-old_stop),tick))
    elif name=='local_stop_target_1_5r':
        target=min(target,ceil_to_increment(entry+D('1.50')*(entry-D(p['stop'])),tick))
    p['targets']=[str(target)]
    if not 0<D(p['stop'])<entry<target:
        return None,'invalid_counterfactual_geometry'
    return p,None


def score_outcome(original,p,candles,outcome,cost):
    status=outcome['status']
    if status in ('untriggered_expired','invalidated_before_entry'):
        return {'basis':'no_entry','net_return_pct':'0','net_R_original':'0','net_R_variant':'0'}
    if status not in ('target_before_stop','stop_before_target','triggered_unresolved'):
        return {'basis':'unknown','net_return_pct':None,'net_R_original':None,'net_R_variant':None}
    entry,old_risk,new_risk=D(p['entry']),D(p['entry'])-D(original['stop']),D(p['entry'])-D(p['stop'])
    if status=='triggered_unresolved':
        end=aware(outcome['horizon_end'])
        last=max((c for c in candles if c.opened_at<end),key=lambda c:c.opened_at)
        profit=last.close-entry-entry*cost/10000
        basis='diagnostic_horizon_mark_not_realized_exit'
    else:
        profit=D(outcome['net_r'])*new_risk
        basis='resolved_barrier_model_not_executed_fill'
    return wire({'basis':basis,'net_return_pct':profit/entry*100,'net_R_original':profit/old_risk,
                 'net_R_variant':profit/new_risk})


def path_metrics(plan,candles,outcome):
    if not outcome.get('triggered_at'):
        return {'available':False,'reason':'no_unambiguous_original_trigger'}
    trigger=aware(outcome['triggered_at'])
    end=aware(outcome['horizon_end'])
    # Exclude the trigger minute: its pre/post-fill high/low ordering is unknown.
    bars=[c for c in candles if trigger<c.opened_at<end]
    if not bars:
        return {'available':False,'reason':'no_complete_post_trigger_minute'}
    peak=max(bars,key=lambda c:c.high)
    trough=min(bars,key=lambda c:c.low)
    entry=D(plan['entry'])
    return wire({'available':True,'scope':'market path after trigger minute through horizon, including after any simulated exit',
                 'entry_latency_hours':D(str((trigger-aware(plan['first_visible_at'])).total_seconds()))/3600,
                 'post_trigger_MFE_pct':max(D(0),(peak.high/entry-1)*100),
                 'post_trigger_MAE_pct':min(D(0),(trough.low/entry-1)*100),
                 'peak_bar_open':peak.opened_at,'hours_to_peak':D(str((peak.opened_at-trigger).total_seconds()))/3600})


def aggregate(rows):
    result={}
    for variant in VARIANTS:
        values=[r for r in rows if r['variant']==variant]
        statuses=Counter(r['outcome']['status'] for r in values)
        complete=[r for r in values if r['score']['net_return_pct'] is not None]
        means={}
        for field in ('net_return_pct','net_R_original','net_R_variant'):
            means[field]={'known_count':len(complete),'total_count':len(values),
                          'mean_all_issued':str(statistics.mean(D(r['score'][field]) for r in complete)) if complete and len(complete)==len(values) else None,
                          'conditional_mean_known_only':str(statistics.mean(D(r['score'][field]) for r in complete)) if complete else None}
        resolved=[r for r in values if r['outcome']['status'] in ('target_before_stop','stop_before_target')]
        leave_one_out={}
        for symbol in sorted({r['symbol'] for r in values}):
            remaining=[r for r in values if r['symbol']!=symbol]
            known=[r for r in remaining if r['score']['net_return_pct'] is not None]
            leave_one_out[symbol]=str(statistics.mean(D(r['score']['net_return_pct']) for r in known)) if known and len(known)==len(remaining) else None
        result[variant]={'leave_one_symbol_out_marked_mean_return_pct':leave_one_out,'issued_candidates':len(values),'counts':dict(statuses),'scores':means,
                         'resolved_exit_count':len(resolved),
                         'resolved_exit_net_R_sum':str(sum((D(r['score']['net_R_variant']) for r in resolved),D(0))),
                         'notes':'horizon marks on unresolved plans are diagnostic; no unqualified overall win rate or automatic selection'}
    return result


def run(root,backfill,replay_path,archive_root):
    policy_plan=json.loads((root/'analysis-plan.json').read_text())
    for name,expected in policy_plan['input_sha256'].items():
        if sha256_file(Path(name))!=expected:
            raise ValueError('analysis input/source changed: '+name)
    saved=json.loads((backfill/'verification.json').read_text())
    replay=json.loads(replay_path.read_text())
    cfg=json.loads(Path(replay['config_path']).read_text())
    database=Path(cfg['inputs']['episodes_sqlite_local'])
    if sha256_file(database)!=replay['input_digests']['episodes_sqlite_sha256']:
        raise ValueError('rolling frame database changed')
    replay_members={s['plan']['symbol']:s for s in SetupOutcomeLedger(backfill/'replay.sqlite').setups()}
    decision_ids={s['symbol']:s['decision_id'] for s in replay['atlas_v3_0_control_signals']}
    feature_sets={}
    traces=[]
    with sqlite3.connect(f'file:{database}?mode=ro',uri=True) as con:
        for symbol,s in replay_members.items():
            row=con.execute('SELECT payload,sha256 FROM discovery_frames WHERE decision_id=?',(decision_ids[symbol],)).fetchone()
            frame=unpack(row[0],row[1])
            features=pre_alert_features(frame['inputs'],symbol,frame['evaluation_at'])
            trace=trace_original(s['plan'],s['candidate'],features)
            trace.update({'symbol':symbol,'episode_id':s['episode_id'],'decision_id':decision_ids[symbol],
                          'pre_alert_atr_1h':str(features['atr_1h']),'pre_alert_local_low_6h':str(features['local_low_6h'])})
            traces.append(trace)
            feature_sets[s['episode_id']]=features
    archive=LocalScanArchive(archive_root)
    legacy=SetupOutcomeLedger(backfill/'legacy.sqlite').setups()
    grouped=defaultdict(list)
    for s in legacy:
        grouped[s['scan_id']].append(s)
    for scan_id,members in grouped.items():
        record=archive.read_scan(scan_id)
        for s in members:
            if not any(same_candidate(c,s['candidate']) for c in record.opportunities):
                raise ValueError('legacy original plan no longer bound')
            feature_sets[s['episode_id']]=pre_alert_features(record.inputs,s['plan']['symbol'],record.started_at)
    rows=[]
    prices={}
    for saved_row in saved['rows']:
        key,plan=saved_row['episode_id'],saved_row['plan']
        path=backfill/'minutes'/(key+'.json')
        if sha256_file(path)!=saved_row['evidence_sha256']:
            raise ValueError('minute evidence changed')
        candles=rederive_minutes(json.loads(path.read_text()))
        prices[key]=candles
        features=feature_sets[key]
        for h in policy_plan['horizons_hours']:
            policy=OutcomePolicy(h,policy_plan['entry_wait_hours'],D(policy_plan['cost_bps']))
            for variant in VARIANTS:
                p,reason=variant_plan(plan,features,variant)
                if p is None:
                    outcome={'status':'variant_unavailable','reason':reason}
                    score={'basis':'unknown','net_return_pct':None,'net_R_original':None,'net_R_variant':None}
                else:
                    outcome=evaluate_setup(p,candles,now=datetime.now(UTC),policy=policy)
                    if variant=='baseline' and outcome!=saved_row['observations'][str(h)]:
                        raise ValueError('baseline outcome does not match prior backfill')
                    score=score_outcome(plan,p,candles,outcome,D(policy_plan['cost_bps']))
                rows.append({'kind':saved_row['kind'],'symbol':plan['symbol'],'episode_id':key,'horizon':h,
                             'variant':variant,'counterfactual_plan':p,'outcome':outcome,'score':score})
    unique_legacy={}
    for r in sorted((r for r in saved['rows'] if r['kind']=='legacy'),key=lambda r:r['plan']['first_visible_at']):
        p=r['plan']
        geometry={k:p[k] for k in ('symbol','direction','setup_label','execution_mode','entry','stop','targets')}
        unique_legacy.setdefault(digest(geometry),r['episode_id'])
    summary={}
    for kind,keys in [('replay',None),('legacy',None),('legacy_geometry_earliest',set(unique_legacy.values()))]:
        summary[kind]={str(h):aggregate([r for r in rows if r['horizon']==h and
                                       (r['kind']==kind if keys is None else r['episode_id'] in keys)]) for h in policy_plan['horizons_hours']}
    paths=[]
    for r in rows:
        if r['variant']=='baseline' and r['kind']=='replay':
            saved_row=next(s for s in saved['rows'] if s['episode_id']==r['episode_id'])
            paths.append({'symbol':r['symbol'],'horizon':r['horizon'],**path_metrics(saved_row['plan'],prices[r['episode_id']],r['outcome'])})
    output={'schema':'atlas-bracket-horizon-audit-output-v1','network_requests':0,'production_rules_changed':False,
            'calibrated_probabilities':False,'performance_claims_permitted':False,'analysis_plan_sha256':sha256_file(root/'analysis-plan.json'),
            'script_sha256':sha256_file(Path(__file__)),'source_input_sha256':policy_plan['input_sha256'],
            'original_bracket_traces':traces,'rows':rows,'summary':summary,'original_post_entry_market_paths':paths,
            'sample_sizes':{'replay':7,'legacy_publications':26,'legacy_exact_geometries':len(unique_legacy)},
            'scope':'fixed historical alerts; no reselection, no later gate/lifecycle replay, no fills or prospective performance claims'}
    write_new_or_equal(root/'audit.json',output)
    print(json.dumps({'sample_sizes':output['sample_sizes'],'replay':summary['replay']},indent=2))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--backfill',type=Path,required=True)
    p.add_argument('--replay',type=Path,required=True)
    p.add_argument('--archive',type=Path,required=True)
    args=p.parse_args()
    run(args.root,args.backfill,args.replay,args.archive)


if __name__=='__main__':
    main()
