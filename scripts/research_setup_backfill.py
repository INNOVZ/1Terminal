"""Bounded retrospective public-minute backfill. No strategy changes or study enrollment."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import structlog

from algotrader.application.rolling_discovery import SnapshotTape, engine
from algotrader.application.scan_replay import ReplayMarket
from algotrader.config import Settings
from algotrader.domain.forward_outcomes import outcome_anchor
from algotrader.domain.models import Direction
from algotrader.domain.scan_observations import ScanDecisionJournal
from algotrader.domain.setup_outcomes import OutcomePolicy, aware, evaluate_setup
from algotrader.infrastructure.rolling_discovery import unpack
from algotrader.infrastructure.scan_archive import LocalScanArchive
from algotrader.infrastructure.setup_outcome_ledger import SetupOutcomeLedger, digest
from scripts.replay_atlas_v3_rolling import compact_opportunity, sha256_file
from scripts.research_verified_replay import offline
from scripts.screener_outcome_ledger import decode_candles

ENDPOINT = 'https://fapi.binance.com/fapi/v1/klines'
MINUTE_MS = 60000


def write_new_or_equal(path: Path, value: Any) -> None:
    raw = json.dumps(value, sort_keys=True, indent=2, allow_nan=False)
    if path.exists():
        if json.loads(path.read_text()) != value:
            raise ValueError('immutable artifact conflict: ' + str(path))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as handle:
        handle.write(raw)


def same_candidate(left, right):
    for key in ('symbol', 'direction', 'setup_label', 'execution_mode'):
        if left.get(key) != right.get(key):
            return False
    for key in ('score', 'suggested_entry', 'suggested_stop'):
        if Decimal(str(left[key])) != Decimal(str(right[key])):
            return False
    return tuple(map(Decimal, left['suggested_targets'])) == tuple(map(Decimal, right['suggested_targets']))


async def prepare(root: Path, replay_path: Path, legacy_root: Path, archive_root: Path):
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))
    replay = json.loads(await asyncio.to_thread(replay_path.read_text))
    config = json.loads(await asyncio.to_thread(Path(replay['config_path']).read_text))
    if sha256_file(Path(replay['config_path'])) != replay['config_sha256']:
        raise ValueError('replay configuration changed')
    for name, expected in replay['input_digests']['all_source_python_sha256'].items():
        if sha256_file(Path(name)) != expected:
            raise ValueError('previous replay source file changed: ' + name)
    database = Path(config['inputs']['episodes_sqlite_local'])
    if sha256_file(database) != replay['input_digests']['episodes_sqlite_sha256']:
        raise ValueError('rolling database checksum changed')
    source_manifest = digest(replay['input_digests']['all_source_python_sha256'])
    ledgers = {kind: SetupOutcomeLedger(root / (kind + '.sqlite')) for kind in ('replay', 'legacy')}
    proof = {'replay_file_sha256': sha256_file(replay_path), 'source_manifest_sha256': source_manifest,
             'original_source_files_match': True, 'legacy_bindings': [], 'replay_matches': []}
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as con:
        for signal in replay['atlas_v3_0_control_signals']:
            row = con.execute('SELECT payload,sha256 FROM discovery_frames WHERE decision_id=?', (signal['decision_id'],)).fetchone()
            frame = unpack(row[0], row[1])
            if frame['issued_at'].isoformat() != signal['issued_at'] or frame['evaluation_at'] > frame['issued_at']:
                raise ValueError('replay decision binding mismatch')
            market = ReplayMarket(SnapshotTape(frame['inputs']))
            settings = Settings.model_construct(**{
                'excluded_base_assets': tuple(config['screener_settings']['excluded_base_assets']),
                'support_resistance_lookback_bars': config['screener_settings']['support_resistance_lookback_bars'],
                'bearish_continuation_enabled': False, 'bearish_continuation_canary_enabled': False})
            journal = ScanDecisionJournal()
            screener = engine(settings, market, frame['evaluation_at'], False, decision_at=frame['issued_at'], journal=journal)
            ranked = sorted(((q.price_change_pct_24h, q.instrument_id.symbol) for q in frame['source_quotes']
                             if q.price_change_pct_24h is not None and q.quote_volume_24h >= 500000), reverse=True)
            top = frozenset(sym for change, sym in ranked[:10] if change >= 10)
            with offline():
                result = await screener.screen(market, market, max_symbols=320, target_direction=Direction.LONG,
                                               enforce_quality=True, top_gainer_symbols=top)
            selected = next((o.as_dict() for o in result if o.instrument.id.symbol == signal['symbol']), None)
            if selected is None or compact_opportunity(selected) != signal['opportunity']:
                raise ValueError('full original replay candidate did not reproduce: ' + signal['symbol'])
            document = {'schema': 'atlas-radar-events-v2', 'scan_id': signal['decision_id'],
                        'engine_identity': {'kind': 'counterfactual_replay', 'source_manifest_sha256': source_manifest,
                                            'replay_sha256': proof['replay_file_sha256']},
                        'screening_policy_version': config['policy_version'],
                        'at': signal['issued_at'], 'events': [{'state': 'FRESH_COIL', 'retained': True,
                          'at': signal['issued_at'], 'candidate': selected}]}
            admitted = ledgers['replay'].ingest(document, source_sha256=digest(document))
            if admitted['rejected']:
                raise ValueError(admitted['rejected'])
            proof['replay_matches'].append({'symbol': signal['symbol'], 'decision_id': signal['decision_id'],
                                           'episode_id': admitted['accepted_episode_ids'][0]})
    # Rebind fresh legacy events only if their original published plan matches a checksummed scan.
    archive = LocalScanArchive(archive_root)
    files = await asyncio.to_thread(lambda: sorted(legacy_root.glob('*.json'), key=lambda p: aware(json.loads(p.read_text())['at'])))
    for path in files:
        document = json.loads(await asyncio.to_thread(path.read_text))
        fresh = [(index, e) for index, e in enumerate(document['events'])
                 if e.get('state') == 'FRESH_COIL' and e.get('retained') is True]
        if not fresh:
            continue
        record = archive.read_scan(document['scan_id'])
        for index, event in fresh:
            candidates = [c for c in record.opportunities if same_candidate(c, event['candidate'])]
            bound = record.status == 'complete' and len(candidates) == 1
            identity = {'kind': 'original_radar', 'scan_id': record.scan_id, 'identity': record.identity,
                        'manifest_sha256': sha256_file(archive_root / 'scans' / record.scan_id / 'manifest.json')} if bound else None
            enriched = {**document, 'engine_identity': identity,
                        'screening_policy_version': record.identity.get('policy') if bound else None, 'events': [event]}
            source = {'audit_sha256': sha256_file(path), 'event_index': index, 'verified_scan_binding': identity}
            admitted = ledgers['legacy'].ingest(enriched, source_sha256=digest(source))
            proof['legacy_bindings'].append({'symbol': event['candidate']['symbol'], 'binding_verified': bound,
                                             'audit': str(path), 'scan_id': record.scan_id, 'import': admitted})
    manifest = {'schema': 'atlas-retrospective-setup-backfill-v1', 'identity_proofs': proof,
                'measurement': {'horizons_hours': [24, 48], 'entry_wait_hours': 24, 'round_trip_cost_bps': '20',
                                'development_data': True, 'prospective_study': False},
                'limits': {'max_requests': 110, 'max_bytes': 32 * 1024**2, 'max_seconds': 600,
                           'per_response_bytes': 512 * 1024, 'interval': '1m', 'page_limit': 1000},
                'members': [{'kind': kind, 'episode_id': s['episode_id'], 'plan': s['plan']}
                            for kind, ledger in ledgers.items() for s in ledger.setups()]}
    write_new_or_equal(root / 'manifest.json', manifest)
    print(json.dumps({'prepared': Counter(m['kind'] for m in manifest['members']),
                      'legacy_bound': sum(p['binding_verified'] for p in proof['legacy_bindings'])}), flush=True)


def validate_rows(rows, start_ms: int, end_ms: int, limit: int):
    if not isinstance(rows, list) or len(rows) > limit:
        raise ValueError('unexpected kline response type/count')
    previous = None
    for r in rows:
        if (not isinstance(r, list) or len(r) < 12 or not isinstance(r[0], int)
                or not isinstance(r[6], int) or not start_ms <= r[0] < end_ms
                or r[0] % MINUTE_MS or r[6] != r[0] + MINUTE_MS - 1
                or (previous is not None and r[0] <= previous)):
            raise ValueError('invalid, out-of-window, or unordered historical candle')
        prices = [Decimal(str(v)) for v in r[1:5]]
        volume = Decimal(str(r[5]))
        if (not all(v.is_finite() and v > 0 for v in prices) or not volume.is_finite() or volume < 0
                or not prices[2] <= min(prices[0], prices[3]) <= max(prices[0], prices[3]) <= prices[1]):
            raise ValueError('invalid historical OHLCV')
        previous = r[0]
    return rows


class PublicHistory:
    def __init__(self, root: Path, limits, client):
        self.root, self.limits, self.client = root, limits, client
        self.requests, self.bytes, self.start = 0, 0, time.monotonic()

    def page(self, params):
        key = digest(params)
        raw_path = self.root / 'raw' / (key + '.json')
        receipt_path = raw_path.with_suffix('.receipt.json')
        if receipt_path.exists():
            if raw_path.stat().st_size > self.limits['per_response_bytes']:
                raise ValueError('cached response exceeds byte budget')
            receipt = json.loads(receipt_path.read_text())
            if (receipt['params'] != params or receipt['endpoint'] != ENDPOINT
                    or sha256_file(raw_path) != receipt['response_sha256']):
                raise ValueError('cached HTTP receipt mismatch')
            raw = raw_path.read_bytes()
        else:
            if self.requests >= self.limits['max_requests'] or time.monotonic() - self.start > self.limits['max_seconds']:
                raise RuntimeError('public backfill request/time budget exhausted')
            self.requests += 1
            began = datetime.now(UTC).isoformat()
            with self.client.stream('GET', ENDPOINT, params=params) as response:
                raw = b''
                for chunk in response.iter_bytes():
                    raw += chunk
                    if len(raw) > self.limits['per_response_bytes']:
                        raise RuntimeError('public response byte budget exceeded')
                self.bytes += len(raw)
                if self.bytes > self.limits['max_bytes']:
                    raise RuntimeError('public total byte budget exceeded')
                receipt = {'endpoint': ENDPOINT, 'params': params, 'status': response.status_code,
                           'request_started_at': began, 'response_completed_at': datetime.now(UTC).isoformat(),
                           'response_sha256': hashlib.sha256(raw).hexdigest(),
                           'provider_date': response.headers.get('date')}
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            with raw_path.open('xb') as handle:
                handle.write(raw)
            write_new_or_equal(receipt_path, receipt)
            time.sleep(.2)
        if receipt['status'] in (418, 429, 403, 451):
            raise RuntimeError('provider access/rate restriction; acquisition stopped')
        if receipt['status'] != 200:
            raise ValueError('public historical response HTTP ' + str(receipt['status']))
        return json.loads(raw), {'path': str(raw_path), 'receipt': str(receipt_path), **receipt}

    def history(self, symbol, start, end):
        if not re.fullmatch('[A-Z0-9]{2,40}', symbol):
            raise ValueError('invalid public symbol')
        cursor, finish = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        rows, receipts = [], []
        while cursor < finish:
            params = {'symbol': symbol, 'interval': '1m', 'startTime': cursor, 'endTime': finish - 1, 'limit': 1000}
            page, receipt = self.page(params)
            page = validate_rows(page, cursor, finish, 1000)
            receipts.append(receipt)
            if not page:
                break
            rows.extend(page)
            cursor = page[-1][0] + MINUTE_MS
        candles = [{'opened_at': datetime.fromtimestamp(r[0] / 1000, UTC).isoformat(),
                    'closed_at': datetime.fromtimestamp(r[6] / 1000, UTC).isoformat(),
                    'open': r[1], 'high': r[2], 'low': r[3], 'close': r[4], 'volume': r[5], 'closed': True} for r in rows]
        return {'schema': 'atlas-setup-minute-evidence-v1', 'market': 'binance_usdm',
                'symbol': symbol, 'candles': candles, 'raw_http_receipts': receipts,
                'start': start.isoformat(), 'end': end.isoformat(),
                'provenance': 'retrospectively_acquired_public_HTTP_not_contemporaneous_signal_input'}


def collect(root: Path):
    manifest = json.loads((root / 'manifest.json').read_text())
    ledgers = {kind: SetupOutcomeLedger(root / (kind + '.sqlite')) for kind in ('legacy', 'replay')}
    results = []
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        public = PublicHistory(root, manifest['limits'], client)
        for member in manifest['members']:
            plan = member['plan']
            start = outcome_anchor(aware(plan['first_visible_at']))
            end = start + timedelta(hours=48)
            if datetime.now(UTC) < end:
                raise ValueError('future outcome window cannot be retrospectively backfilled')
            try:
                evidence = public.history(plan['symbol'], start, end)
                write_new_or_equal(root / 'minutes' / (member['episode_id'] + '.json'), evidence)
                values = decode_candles(evidence)
                observations = {}
                for h in manifest['measurement']['horizons_hours']:
                    policy = OutcomePolicy(h, min(h, manifest['measurement']['entry_wait_hours']),
                                           Decimal(manifest['measurement']['round_trip_cost_bps']))
                    now = datetime.now(UTC)
                    outcome = evaluate_setup(plan, values, now=now, policy=policy)
                    ledgers[member['kind']].assess(member['episode_id'], policy, outcome,
                                                   evidence=evidence, observed_at=now)
                    observations[str(h)] = outcome
                result = {'kind': member['kind'], 'symbol': plan['symbol'], 'episode_id': member['episode_id'],
                          'candles': len(values), 'observations': observations}
            except (ValueError, httpx.HTTPError) as exc:
                result = {'kind': member['kind'], 'symbol': plan['symbol'], 'episode_id': member['episode_id'],
                          'unavailable': str(exc)}
            results.append(result)
            print(json.dumps({'progress': len(results), 'total': len(manifest['members']), 'symbol': plan['symbol'],
                              'kind': member['kind'], 'statuses': {k: v['status'] for k, v in result.get('observations', {}).items()},
                              'unavailable': result.get('unavailable')}), flush=True)
    summary = {'schema': 'atlas-retrospective-setup-results-v1', 'manifest_sha256': sha256_file(root / 'manifest.json'),
               'results': results,
               'calibrated_win_rate': None, 'performance_claims_permitted': False}
    write_new_or_equal(root / 'results.json', summary)
    write_new_or_equal(root / 'runs' / (str(uuid4()) + '.json'), {
        'completed_at': datetime.now(UTC).isoformat(), 'new_requests': public.requests,
        'new_response_bytes': public.bytes, 'script_sha256': sha256_file(Path(__file__)),
        'classifier_sha256': sha256_file(Path('src/algotrader/domain/setup_outcomes.py')),
        'manifest_sha256': summary['manifest_sha256']})
    for kind, ledger in ledgers.items():
        (root / (kind + '-report.json')).write_text(json.dumps(ledger.report(), indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    commands = p.add_subparsers(dest='command', required=True)
    prepare_cmd = commands.add_parser('prepare')
    prepare_cmd.add_argument('--replay', type=Path, required=True)
    prepare_cmd.add_argument('--legacy-audits', type=Path, required=True)
    prepare_cmd.add_argument('--archive', type=Path, required=True)
    commands.add_parser('collect')
    args = p.parse_args()
    if args.command == 'prepare':
        asyncio.run(prepare(args.root, args.replay, args.legacy_audits, args.archive))
    else:
        collect(args.root)


if __name__ == '__main__':
    main()
