"""Offline raw-receipt re-derivation and outcome verification for setup backfills."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from algotrader.domain.forward_outcomes import outcome_anchor
from algotrader.domain.setup_outcomes import OutcomePolicy, aware, evaluate_setup
from algotrader.infrastructure.setup_outcome_ledger import SetupOutcomeLedger, digest
from scripts.replay_atlas_v3_rolling import sha256_file
from scripts.research_setup_backfill import ENDPOINT, validate_rows, write_new_or_equal
from scripts.screener_outcome_ledger import decode_candles


def rederive_minutes(evidence):
    cursor = int(aware(evidence['start']).timestamp() * 1000)
    finish = int(aware(evidence['end']).timestamp() * 1000)
    rows = []
    for saved in evidence['raw_http_receipts']:
        receipt = json.loads(Path(saved['receipt']).read_text())
        if any(saved[k] != receipt[k] for k in receipt):
            raise ValueError('receipt metadata changed')
        params = {'symbol': evidence['symbol'], 'interval': '1m', 'limit': 1000,
                  'startTime': cursor, 'endTime': finish - 1}
        if (receipt['params'] != params or receipt['endpoint'] != ENDPOINT or receipt['status'] != 200
                or sha256_file(Path(saved['path'])) != receipt['response_sha256']):
            raise ValueError('historical request/raw receipt binding mismatch')
        raw = validate_rows(json.loads(Path(saved['path']).read_text()), cursor, finish, 1000)
        rows.extend(raw)
        if raw:
            cursor = raw[-1][0] + 60000
    expected = [{'opened_at': datetime.fromtimestamp(r[0]/1000, UTC).isoformat(),
                 'closed_at': datetime.fromtimestamp(r[6]/1000, UTC).isoformat(),
                 'open': r[1], 'high': r[2], 'low': r[3], 'close': r[4], 'volume': r[5], 'closed': True}
                for r in rows]
    if expected != evidence['candles']:
        raise ValueError('normalized minute evidence differs from raw HTTP')
    return decode_candles(evidence)


def verify(root: Path):
    manifest = json.loads((root/'manifest.json').read_text())
    result = json.loads((root/'results.json').read_text())
    if result['manifest_sha256'] != sha256_file(root/'manifest.json'):
        raise ValueError('manifest changed')
    runs = [json.loads(p.read_text()) for p in (root/'runs').glob('*.json')]
    if not runs or any(r['classifier_sha256'] != sha256_file(Path('src/algotrader/domain/setup_outcomes.py')) for r in runs):
        raise ValueError('measurement classifier changed')
    results_by_id = {r['episode_id']: r for r in result['results']}
    all_rows, statuses = [], {}
    for member in manifest['members']:
        plan, key = member['plan'], member['episode_id']
        recorded = results_by_id[key]
        evidence_path = root/'minutes'/(key+'.json')
        evidence = json.loads(evidence_path.read_text())
        anchor = outcome_anchor(aware(plan['first_visible_at']))
        if (evidence['symbol'] != plan['symbol'] or aware(evidence['start']) != anchor
                or aware(evidence['end']) != anchor + timedelta(hours=48)):
            raise ValueError('minute window does not belong to original setup')
        candles = rederive_minutes(evidence)
        if len(candles) != 2880:
            raise ValueError('incomplete 48h minute window')
        for h in (24, 48):
            policy = OutcomePolicy(h, manifest['measurement']['entry_wait_hours'],
                                   Decimal(manifest['measurement']['round_trip_cost_bps']))
            actual = evaluate_setup(plan, candles, now=datetime.now(UTC), policy=policy)
            if actual != recorded['observations'][str(h)]:
                raise ValueError('outcome re-derivation mismatch')
            statuses.setdefault(member['kind'], {}).setdefault(str(h), Counter())[actual['status']] += 1
        all_rows.append({**recorded, 'plan': plan, 'evidence_sha256': sha256_file(evidence_path)})
    exact_plans = defaultdict(list)
    for row in sorted((r for r in all_rows if r['kind']=='legacy'), key=lambda r: r['plan']['first_visible_at']):
        p = row['plan']
        geometry = {k:p[k] for k in ('symbol','direction','setup_label','execution_mode','entry','stop','targets')}
        exact_plans[digest(geometry)].append(row)
    geometry_view = {
        'unique_geometries': len(exact_plans),
        'additional_repeated_publications': sum(len(v)-1 for v in exact_plans.values()),
        'deduplication_semantics': 'earliest original visibility per identical plan geometry; diagnostic only; still correlated',
        'counts': {str(h):dict(Counter(v[0]['observations'][str(h)]['status'] for v in exact_plans.values())) for h in (24,48)},
        'repeated_groups': [[r['episode_id'] for r in values] for values in exact_plans.values() if len(values)>1]}
    for kind in ('legacy','replay'):
        # Also validate persisted plan, assessment and evidence checksums.
        SetupOutcomeLedger(root/(kind+'.sqlite')).report()
    verified = {'schema':'atlas-backfill-verification-v1', 'network_requests':0,
                'windows_verified':len(all_rows), 'minute_observations_verified':len(all_rows)*2880,
                'outcomes_recomputed':len(all_rows)*2, 'status_counts':statuses,
                'legacy_identical_geometry_view':geometry_view, 'rows':all_rows,
                'calibrated_probabilities':False,'performance_claims_permitted':False,
                'input_sha256':{'manifest':sha256_file(root/'manifest.json'),'results':sha256_file(root/'results.json')},
                'verifier_sha256':sha256_file(Path(__file__))}
    write_new_or_equal(root/'verification.json', verified)
    return verified


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    args=p.parse_args()
    output=verify(args.root)
    print(json.dumps({k:output[k] for k in ('windows_verified','outcomes_recomputed','status_counts','legacy_identical_geometry_view')},indent=2))


if __name__ == '__main__':
    main()
