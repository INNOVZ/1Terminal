"""Descriptive evaluation of a current-source replay against frozen sampled outcomes.

No provider, fitting, strategy changes, fills, or probability calibration. Entry/exit
observations on a five-minute quote grid cannot establish intrabar ordering or fills.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.replay_atlas_v3_rolling import load_price_grid, sha256_file
from scripts.research_prospective import load, semantic_digest


def rate(values: list[float]) -> dict[str, Any]:
    n, wins = len(values), sum(v > 0 for v in values)
    if not n:
        return {'positive': 0, 'known': 0, 'pct': None, 'nominal_wilson_95_pct': None}
    z, p = 1.959963984540054, wins / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return {'positive': wins, 'known': n, 'pct': 100 * p,
            'nominal_wilson_95_pct': [100 * (center - half), 100 * (center + half)],
            'interval_assumption': 'independent Bernoulli trials; correlated post-selection signals violate this assumption'}


def sampled_bracket(signal: dict[str, Any], points: list[tuple[float, float]], hours: int) -> dict[str, Any]:
    o = signal['opportunity']
    start = datetime.fromisoformat(signal['issued_at']).timestamp()
    end = start + hours * 3600
    entry, stop = o['suggested_entry'], o['suggested_stop']
    targets = o['suggested_targets']
    mode = o.get('execution_mode')
    if not targets or not 0 < stop < entry < min(targets):
        return {'status': 'invalid_or_unsupported_long_bracket', 'trade_win_rate_known': False}
    if mode not in ('BREAKOUT_TRIGGER', 'CEILING_BREAKOUT', 'RETEST_ACCUMULATION', 'WAIT_FOR_RETEST_ONLY'):
        return {'status': 'unsupported_execution_mode', 'mode': mode, 'trade_win_rate_known': False}
    selected = [(start, signal['outcomes']['entry_quote_price']),
                *((t, p) for t, p in points if start < t <= end)]
    endpoint = next((t for t, _ in points if end <= t <= end + 345), None)
    if endpoint is None:
        return {'status': 'unknown_or_censored_horizon', 'trade_win_rate_known': False}
    prior = start
    gap = False
    triggered_at = None
    target_seen = None
    stop_seen = None
    for t, p in selected:
        gap |= t - prior > 345
        prior = t
        if triggered_at is None:
            if p <= stop:
                return {'status': 'sampled_invalidation_before_entry', 'observed_at': t,
                        'gap_over_345s': gap, 'trade_win_rate_known': False}
            trigger = p >= entry if mode in ('BREAKOUT_TRIGGER', 'CEILING_BREAKOUT') else p <= entry
            if not trigger:
                continue
            triggered_at = t
        if p <= stop and stop_seen is None:
            stop_seen = t
        if p >= targets[0] and target_seen is None:
            target_seen = t
    gap |= end - prior > 345
    if triggered_at is None:
        status = 'entry_not_observed'
    elif target_seen is not None and (stop_seen is None or target_seen < stop_seen):
        status = 'sampled_T1_before_sampled_stop'
    elif stop_seen is not None:
        status = 'sampled_stop_before_sampled_T1'
    else:
        status = 'sampled_entry_observed_no_sampled_exit'
    return {'status': status, 'entry_first_observed_at': triggered_at,
            'T1_first_observed_at': target_seen, 'stop_first_observed_at': stop_seen,
            'gap_over_345s': gap, 'entry': entry, 'stop': stop, 'T1': targets[0],
            'trade_win_rate_known': False,
            'reason': 'sampled last prices omit intervening wicks, spread, fills and same-interval ordering; no fees applied'}


def evaluate(replay_path: Path, grid_path: Path, report_path: Path,
             definition_path: Path, snapshots_root: Path) -> dict[str, Any]:
    replay = json.loads(replay_path.read_text())
    grid = json.loads(grid_path.read_text())
    report, definition = load(report_path), load(definition_path)
    if report['study_id'] != definition['study_id'] or report['definition_sha256'] != semantic_digest(definition):
        raise ValueError('report definition binding mismatch')
    expected = replay['input_digests']
    if sha256_file(grid_path) != expected['price_grid_sha256'] or sha256_file(report_path) != expected['report_48h_sha256']:
        raise ValueError('replay outcome input changed')
    for name, digest in expected['all_source_python_sha256'].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError('replay source no longer current: ' + name)
    provenance = grid['provenance']
    if provenance['definition_sha256'] != semantic_digest(definition):
        raise ValueError('grid definition mismatch')
    hashes = provenance['source_snapshot_sha256']
    actual_names = {p.name for p in snapshots_root.glob('*.json')}
    if actual_names != set(hashes):
        raise ValueError('snapshot set changed')
    # Verify the input file hashes that the already-recomputed grid attests.
    for name, digest in hashes.items():
        if sha256_file(snapshots_root / name) != digest:
            raise ValueError('snapshot checksum mismatch: ' + name)
    trajectory, _, first_top10, meta = load_price_grid(grid_path, report_path)
    signals = replay['atlas_v3_0_control_signals']
    challenger = replay['atlas_v3_0_challenger_signals']
    if len({s['symbol'] for s in signals}) != len(signals):
        raise ValueError('duplicate first-alert symbol')
    rows = []
    for signal in signals:
        if not signal['opportunity']['portal_fresh_eligible']:
            raise ValueError('non-five-gate selection in primary results')
        rows.append({**signal, 'sampled_bracket_24h': sampled_bracket(signal, trajectory.get(signal['symbol'], []), 24),
                     'sampled_bracket_48h': sampled_bracket(signal, trajectory.get(signal['symbol'], []), 48)})
    # Use the study's anchor from slot zero, not a score/selection-dependent start.
    enrollment_path = definition_path.parent / 'enrollment.json'
    enrollment = load(enrollment_path)
    if enrollment['definition_sha256'] != semantic_digest(definition):
        raise ValueError('enrollment binding mismatch')
    initial_end = enrollment['anchor'].timestamp() + 48 * 3600
    initial_entrants = {sym for sym, t in first_top10.items() if t <= initial_end}
    early_initial = [s['symbol'] for s in signals if s['outcomes']['early_before_top10']
                     and first_top10[s['symbol']] <= initial_end]
    result = {'schema': 'atlas-current-archived-outcomes-v1', 'study_id': report['study_id'],
              'source_identity': replay['input_digests']['all_source_python_sha256'],
              'verification': {'grid_snapshot_checksums_verified': len(hashes),
                               'grid_snapshot_set_exact': True, 'source_matches_replay': True},
              'scope': 'first alert per unique symbol in frozen recorded deep pools; current production core scoring with discovery_v2; not production scheduler/universe replay',
              'historical_development_evidence': True, 'calibrated_probabilities': False,
              'executed_or_simulated_fill_win_rate': None, 'performance_claims_permitted': False,
              'core_and_recovery_first_alerts_equal': signals == challenger,
              'signals': rows, 'source_price_grid': meta, 'summary': {}}
    for h in (24, 48):
        values = [s['outcomes'][f'ret_{h}h_pct'] for s in signals if s['outcomes'].get(f'ret_{h}h_pct') is not None]
        result['summary'][str(h)] = {
            'quote_directional_positive_rate': rate(values),
            'mean_quote_return_pct': statistics.mean(values) if values else None,
            'median_quote_return_pct': statistics.median(values) if values else None,
            'unknown_or_censored': len(signals) - len(values),
            'sampled_bracket_status_counts': dict(Counter(s[f'sampled_bracket_{h}h']['status'] for s in rows)),
            'leave_one_out_mean_return_pct': {
                s['symbol']: statistics.mean([x['outcomes'][f'ret_{h}h_pct'] for x in signals
                                             if x is not s and x['outcomes'].get(f'ret_{h}h_pct') is not None])
                for s in signals if len(values) > 1},
        }
    result['early_detection'] = {
        'original_48h_certified_new_entrants': len(initial_entrants),
        'original_48h_detected_early': early_initial,
        'observed_recall_pct_in_recorded_pools': 100 * len(early_initial) / len(initial_entrants) if initial_entrants else None,
        'alert_relative_48h_detected_early': [s['symbol'] for s in signals if s['outcomes']['early_before_top10']],
        'semantics': 'first observed membership; exact first entry may precede observation when ranks were unknown'}
    result['input_files_sha256'] = {str(p): sha256_file(p) for p in
                                    (replay_path, grid_path, report_path, definition_path, enrollment_path, Path(__file__))}
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('replay', 'grid', 'report', 'definition', 'snapshots', 'output'):
        p.add_argument('--' + key, type=Path, required=True)
    args = p.parse_args()
    result = evaluate(args.replay, args.grid, args.report, args.definition, args.snapshots)
    with args.output.open('x') as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
    print(json.dumps({'summary': result['summary'], 'early_detection': result['early_detection']}, indent=2))


if __name__ == '__main__':
    main()
