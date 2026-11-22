"""Manual offline ledger import/evaluate/report. No network clients or scheduling."""
from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from algotrader.domain.models import Candle
from algotrader.domain.setup_outcomes import OutcomePolicy, aware, evaluate_setup
from algotrader.infrastructure.setup_outcome_ledger import SetupOutcomeLedger


def read_document(path: Path, *, limit: int = 4 * 1024 * 1024):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError('input must be a bounded regular file, not a symlink')
    with path.open('rb') as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('input exceeds byte limit')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('nonfinite JSON number: ' + value)
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid), hashlib.sha256(raw).hexdigest()


def decode_candles(document):
    if document.get('schema') != 'atlas-setup-minute-evidence-v1' or document.get('market') != 'binance_usdm':
        raise ValueError('unsupported minute evidence schema/market')
    rows = document['candles']
    if not isinstance(rows, list) or len(rows) > 3000:
        raise ValueError('minute evidence exceeds 3000 bars')
    return tuple(Candle(opened_at=aware(r['opened_at']), closed_at=aware(r['closed_at']),
                        open=Decimal(str(r['open'])), high=Decimal(str(r['high'])),
                        low=Decimal(str(r['low'])), close=Decimal(str(r['close'])),
                        volume=Decimal(str(r['volume'])), closed=r.get('closed') is True) for r in rows)



def ingest_directory(ledger: SetupOutcomeLedger, directory: Path):
    if not directory.is_dir():
        raise ValueError('audit directory does not exist')
    files = list(directory.glob('*.json'))
    if len(files) > 1000:
        raise ValueError('more than 1000 audit files; choose a bounded input directory')
    indexed = []
    for path in files:
        document, checksum = read_document(path)
        indexed.append((aware(document['at']), str(path), checksum))
    result = {'imports': []}
    # File names are UUIDs, not chronology. Freeze the earliest available fresh audit.
    for _, name, checksum in sorted(indexed):
        path = Path(name)
        document, current = read_document(path)
        if current != checksum:
            raise ValueError('audit changed during import')
        result['imports'].append({'file': name, **ledger.ingest(document, source_sha256=checksum)})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    ingest = sub.add_parser('import-radar')
    ingest.add_argument('--audit-dir', type=Path, required=True)
    evaluate = sub.add_parser('evaluate')
    evaluate.add_argument('--episode-id', required=True)
    evaluate.add_argument('--evidence', type=Path, required=True)
    evaluate.add_argument('--now', required=True, help='Explicit UTC/as-of timestamp, never inferred from a candle')
    evaluate.add_argument('--horizon-hours', type=int, choices=(24, 48), required=True)
    evaluate.add_argument('--entry-wait-hours', type=int, required=True)
    evaluate.add_argument('--round-trip-cost-bps', type=Decimal, required=True)
    sub.add_parser('report')
    args = parser.parse_args()
    ledger = SetupOutcomeLedger(args.ledger)
    if args.command == 'import-radar':
        result = ingest_directory(ledger, args.audit_dir)
    elif args.command == 'evaluate':
        doc, checksum = read_document(args.evidence)
        setup = next((s for s in ledger.setups() if s['episode_id'] == args.episode_id), None)
        if setup is None or setup['plan']['symbol'] != doc.get('symbol'):
            parser.error('unknown episode or evidence symbol mismatch')
        now = aware(args.now)
        policy = OutcomePolicy(args.horizon_hours, args.entry_wait_hours, args.round_trip_cost_bps)
        outcome = evaluate_setup(setup['plan'], decode_candles(doc), now=now, policy=policy)
        assessment = ledger.assess(args.episode_id, policy, outcome,
                                   evidence={'file_sha256': checksum, 'source_file': str(args.evidence),
                                             'document': doc, 'provenance': 'locally_supplied_not_provider_receipt_attested'},
                                   observed_at=now)
        result = {'assessment_id': assessment, 'outcome': outcome}
    else:
        result = ledger.report()
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
