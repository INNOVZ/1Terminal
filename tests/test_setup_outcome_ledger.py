from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from algotrader.domain.models import Candle
from algotrader.domain.setup_outcomes import OutcomePolicy, evaluate_setup, freeze_plan
from algotrader.infrastructure.setup_outcome_ledger import SetupOutcomeLedger, digest
from scripts.screener_outcome_ledger import read_document

D = Decimal
START = datetime(2026, 11, 1, tzinfo=UTC)
POLICY = OutcomePolicy(24, 4, D('20'))


def candidate(short=False):
    return {'symbol': 'TESTUSDT', 'direction': 'short' if short else 'long',
            'setup_label': 'TEST_SHORT' if short else 'TEST_LONG',
            'execution_mode': 'BREAKDOWN_TRIGGER' if short else 'CEILING_BREAKOUT',
            'evaluated_at': START.isoformat(), 'first_seen_at': START.isoformat(),
            'score': '85', 'suggested_entry': '95' if short else '105',
            'suggested_stop': '105' if short else '95',
            'suggested_targets': ['85'] if short else ['115'],
            'checklist': [{'passed': True} for _ in range(5)]}


def plan(short=False):
    return freeze_plan(candidate(short), START)


def bars():
    return [Candle(START + timedelta(minutes=i), START + timedelta(minutes=i+1),
                   D(100), D(101), D(99), D(100), D(100)) for i in range(1440)]


def price(bar, o, h, low, c):
    return replace(bar, open=D(o), high=D(h), low=D(low), close=D(c))


def evaluate(p, values, policy=POLICY):
    return evaluate_setup(p, values, now=START + timedelta(hours=24), policy=policy)


def test_long_target_before_stop_costs_and_short_stop():
    values = bars()
    values[0] = price(values[0], 100, 106, 99, 106)
    values[1] = price(values[1], 106, 116, 105, 115)
    out = evaluate(plan(), values)
    assert out['status'] == 'target_before_stop'
    assert D(out['net_r']) == (D(10) - D(105) * D('.002')) / 10
    assert out['is_executed_trade'] is False and out['probability_calibration_permitted'] is False
    values = bars()
    values[0] = price(values[0], 100, 101, 94, 94)
    values[1] = price(values[1], 94, 106, 93, 106)
    assert evaluate(plan(True), values)['status'] == 'stop_before_target'
    values[1] = price(values[1], 94, 95, 84, 85)
    assert evaluate(plan(True), values)['status'] == 'target_before_stop'


def test_same_bar_ambiguities_and_entry_gaps_are_not_manufactured_wins():
    values = bars()
    values[0] = price(values[0], 100, 116, 94, 110)
    assert evaluate(plan(), values)['status'] == 'ambiguous'
    values[0] = price(values[0], 106, 116, 106, 115)
    assert evaluate(plan(), values)['reason'] == 'opening_price_already_beyond_entry'
    values[0] = price(values[0], 100, 106, 99, 106)
    values[1] = price(values[1], 106, 116, 94, 110)
    assert evaluate(plan(), values)['reason'] == 'target_and_stop_in_same_bar'
    p = {**plan(), 'execution_mode': 'RETEST_ACCUMULATION'}
    values[0] = price(values[0], 110, 116, 104, 106)
    assert evaluate(p, values)['reason'] == 'retest_entry_and_target_order_unknown'


def test_unknown_incomplete_or_invalid_history_never_enters_resolved_denominator():
    values = bars()
    assert evaluate(plan(), values[:-1])['status'] == 'missing'
    assert evaluate(plan(), [values[0], *values])['status'] == 'invalid'
    assert evaluate_setup(plan(), (), now=START, policy=POLICY)['status'] == 'not_due'
    values[0] = price(values[0], 100, 90, 99, 100)
    assert evaluate(plan(), values)['status'] == 'invalid'


def test_expiry_unresolved_and_continuation_deadline():
    values = bars()
    assert evaluate(plan(), values)['status'] == 'untriggered_expired'
    values[0] = price(values[0], 100, 106, 99, 106)
    assert evaluate(plan(), values)['status'] == 'triggered_unresolved'
    # A strategy's earlier deadline supersedes the measurement policy's wait.
    p = {**plan(), 'strategy_entry_deadline': START.isoformat()}
    assert evaluate(p, values)['status'] == 'untriggered_expired'


def audit(c=None):
    return {'schema': 'atlas-radar-events-v2', 'scan_id': 'original-scan', 'at': START.isoformat(),
            'events': [{'state': 'FRESH_COIL', 'retained': True, 'at': START.isoformat(),
                        'candidate': c or candidate()}]}


def test_ledger_deduplicates_and_preserves_original_plan(tmp_path):
    ledger = SetupOutcomeLedger(tmp_path / 'ledger.sqlite')
    doc = audit()
    first = ledger.ingest(doc, source_sha256=digest(doc))
    key = first['accepted_episode_ids'][0]
    assert ledger.ingest(doc, source_sha256=digest(doc))['duplicate_import'] is True
    changed = audit({**candidate(), 'suggested_entry': '106'})
    result = ledger.ingest(changed, source_sha256=digest(changed))
    assert result['rejected'][0]['reason'] == 'immutable_episode_plan_conflict'
    assert ledger.setups()[0]['plan']['entry'] == '105'
    assert ledger.setups()[0]['engine_identity'] is None
    assert ledger.report()['episode_count'] == 1
    assert ledger.report()['groups'][0]['counts']['awaiting_price_evidence'] == 1
    assert ledger.setups()[0]['episode_id'] == key


def test_orphan_retention_and_unknown_gates_do_not_enroll(tmp_path):
    ledger = SetupOutcomeLedger(tmp_path / 'ledger.sqlite')
    doc = audit()
    doc['events'][0]['state'] = 'ACTIVE_COIL'
    assert ledger.ingest(doc, source_sha256=digest(doc))['ignored_nonfresh_events'] == 1
    doc = audit()
    doc['events'][0]['candidate']['checklist'][0]['status'] = 'unknown'
    assert ledger.ingest(doc, source_sha256=digest(doc))['rejected']
    assert ledger.setups() == []


def test_assessments_idempotent_separate_policy_and_preserve_mature_result(tmp_path):
    ledger = SetupOutcomeLedger(tmp_path / 'ledger.sqlite')
    doc = audit()
    key = ledger.ingest(doc, source_sha256=digest(doc))['accepted_episode_ids'][0]
    values = bars()
    values[0] = price(values[0], 100, 106, 99, 106)
    values[1] = price(values[1], 106, 116, 105, 115)
    out = evaluate(plan(), values)
    evidence = {'source': 'test', 'hash': 'one'}
    first = ledger.assess(key, POLICY, out, evidence=evidence, observed_at=START + timedelta(days=1))
    assert first == ledger.assess(key, POLICY, out, evidence=evidence, observed_at=START + timedelta(days=1))
    ledger.assess(key, POLICY, {**out, 'status': 'missing'}, evidence={'hash': 'two'},
                  observed_at=START + timedelta(days=2))
    report = ledger.report()
    assert report['groups'][0]['counts']['target_before_stop'] == 1
    assert report['groups'][0]['resolved_barrier_positive_rate'] == 1
    second_policy = OutcomePolicy(24, 4, D('30'))
    ledger.assess(key, second_policy, evaluate(plan(), values, second_policy),
                  evidence=evidence, observed_at=START + timedelta(days=1))
    report = ledger.report()
    assert report['episode_count'] == 1 and len(report['groups']) == 2
    assert report['calibrated_probabilities'] is False


def test_ledger_detects_evidence_and_plan_tampering(tmp_path):
    ledger = SetupOutcomeLedger(tmp_path / 'ledger.sqlite')
    doc = audit()
    ledger.ingest(doc, source_sha256=digest(doc))
    with ledger.connect() as con:
        con.execute("UPDATE setups SET payload='{}'")
    with pytest.raises(ValueError, match='checksum'):
        ledger.report()


def test_bounded_strict_input_and_symlink_rejection(tmp_path):
    p = tmp_path / 'a.json'
    p.write_text('{"x":1,"x":2}')
    with pytest.raises(ValueError, match='duplicate'):
        read_document(p)
    p.write_text('{"x":NaN}')
    with pytest.raises(ValueError, match='nonfinite'):
        read_document(p)
    p.write_text(json.dumps(audit()))
    with pytest.raises(ValueError, match='bounded'):
        read_document(p, limit=1)
    alias = tmp_path / 'alias'
    alias.symlink_to(p)
    with pytest.raises(ValueError, match='symlink'):
        read_document(alias)


def test_deadline_inside_trigger_minute_is_ambiguous():
    values = bars()
    values[0] = price(values[0], 100, 106, 99, 106)
    p = {**plan(), 'strategy_entry_deadline': (START + timedelta(seconds=30)).isoformat()}
    assert evaluate(p, values)['reason'] == 'entry_bar_straddles_strategy_deadline'


def test_ledger_rejects_mismatched_costs_or_pre_maturity_assessment(tmp_path):
    ledger = SetupOutcomeLedger(tmp_path / 'ledger.sqlite')
    doc = audit()
    key = ledger.ingest(doc, source_sha256=digest(doc))['accepted_episode_ids'][0]
    values = bars()
    values[0] = price(values[0], 100, 106, 99, 106)
    values[1] = price(values[1], 106, 116, 105, 115)
    out = evaluate(plan(), values)
    with pytest.raises(ValueError, match='maturity'):
        ledger.assess(key, POLICY, out, evidence={}, observed_at=START)
    with pytest.raises(ValueError, match='cost policy'):
        ledger.assess(key, OutcomePolicy(24, 4, D('30')), out,
                      evidence={}, observed_at=START + timedelta(days=1))


def test_barrier_target_does_not_mean_net_positive_after_configured_costs(tmp_path):
    ledger = SetupOutcomeLedger(tmp_path / 'ledger.sqlite')
    doc = audit()
    key = ledger.ingest(doc, source_sha256=digest(doc))['accepted_episode_ids'][0]
    values = bars()
    values[0] = price(values[0], 100, 106, 99, 106)
    values[1] = price(values[1], 106, 116, 105, 115)
    costly = OutcomePolicy(24, 4, D('1000'))
    out = evaluate(plan(), values, costly)
    ledger.assess(key, costly, out, evidence={}, observed_at=START + timedelta(days=1))
    group = ledger.report()['groups'][0]
    assert group['resolved_barrier_positive_rate'] == 1
    assert group['resolved_model_net_positive_rate'] == 0


def test_directory_import_uses_event_chronology_not_uuid_file_order(tmp_path):
    from scripts.screener_outcome_ledger import ingest_directory

    directory = tmp_path / 'audits'
    directory.mkdir()
    early = audit()
    early['at'] = START.isoformat()
    late = audit()
    late['at'] = (START + timedelta(minutes=1)).isoformat()
    late['events'][0]['at'] = late['at']
    (directory / 'a-late.json').write_text(json.dumps(late))
    (directory / 'z-early.json').write_text(json.dumps(early))
    ledger = SetupOutcomeLedger(tmp_path / 'ledger.sqlite')
    result = ingest_directory(ledger, directory)
    assert result['imports'][0]['file'].endswith('z-early.json')
    assert ledger.setups()[0]['plan']['first_visible_at'] == START.isoformat()
    assert result['imports'][1]['rejected'][0]['reason'] == 'immutable_episode_plan_conflict'
