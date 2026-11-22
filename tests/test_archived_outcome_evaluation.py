"""Frozen-outcome evaluation does not equate positive prices with trade wins."""
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from scripts.research_archived_outcome_evaluation import rate, sampled_bracket
from scripts.research_historical_aggregate_replay import legacy_aggregate_projection
from tests.test_bearish_continuation import fixture


def signal(mode='BREAKOUT_TRIGGER', quote=100):
    return {'issued_at': datetime.fromtimestamp(1000, UTC).isoformat(),
            'opportunity': {'suggested_entry': 105, 'suggested_stop': 95,
                            'suggested_targets': [115], 'execution_mode': mode},
            'outcomes': {'entry_quote_price': quote}}


def test_positive_price_can_finish_without_entry_or_target():
    points = [(1300, 101), (1600, 104), (87400, 103)]
    assert sampled_bracket(signal(), points, 24)['status'] == 'entry_not_observed'
    assert sampled_bracket(signal('RETEST_ACCUMULATION', quote=110),
                           [(1300, 111), (87400, 112)], 24)['status'] == 'entry_not_observed'


def test_sampled_target_and_stop_order_is_labelled_without_claiming_fills():
    a = sampled_bracket(signal(), [(1300, 106), (1600, 116), (1900, 94), (87400, 110)], 24)
    assert a['status'] == 'sampled_T1_before_sampled_stop'
    assert a['trade_win_rate_known'] is False
    b = sampled_bracket(signal(), [(1300, 106), (1600, 94), (1900, 116), (87400, 110)], 24)
    assert b['status'] == 'sampled_stop_before_sampled_T1'
    assert b['gap_over_345s'] is True


def test_no_pre_alert_price_or_late_endpoint_used_and_initial_quote_counts():
    assert sampled_bracket(signal(), [(999, 116), (87400, 104)], 24)['status'] == 'entry_not_observed'
    assert sampled_bracket(signal(), [(87746, 116)], 24)['status'] == 'unknown_or_censored_horizon'
    a = sampled_bracket(signal(quote=106), [(1300, 116), (87400, 110)], 24)
    assert a['entry_first_observed_at'] == 1000
    assert a['status'] == 'sampled_T1_before_sampled_stop'


def test_invalidation_before_sampled_entry_is_not_a_losing_filled_trade():
    a = sampled_bracket(signal(), [(1300, 94), (1600, 116), (87400, 110)], 24)
    assert a['status'] == 'sampled_invalidation_before_entry'
    assert a['trade_win_rate_known'] is False


def test_nominal_uncertainty_is_explicit_and_small_samples_are_wide():
    r = rate([1, 1, 1, 1, -1, -1, -1])
    assert r['positive'] == 4 and r['known'] == 7
    assert r['nominal_wilson_95_pct'][0] < 26
    assert r['nominal_wilson_95_pct'][1] > 84
    assert rate([])['pct'] is None


def test_legacy_projection_removes_only_unarchived_metadata():
    _, _, _, _, d = fixture()
    a = legacy_aggregate_projection(d)
    assert a.continuation_inputs is None
    assert a == replace(d, continuation_inputs=None)
    changed = replace(d, funding_rate=d.funding_rate + Decimal('.001'),
                      open_interest_change_4h=Decimal('.8'))
    assert legacy_aggregate_projection(changed) != a
    assert legacy_aggregate_projection([d])[0] == a


def test_stream_digest_uses_canonical_optional_field_compatibility():
    import hashlib

    from algotrader.infrastructure.scan_archive import canonical_bytes
    from scripts.research_evidence_stream import semantic_digest

    _, _, _, _, d = fixture()
    for snapshot in (d, replace(d, continuation_inputs=None)):
        assert semantic_digest(snapshot) == hashlib.sha256(canonical_bytes(snapshot)).hexdigest()


def test_ceiling_breakout_uses_the_breakout_trigger_direction():
    a = sampled_bracket(signal('CEILING_BREAKOUT'), [(1300, 106), (1600, 116), (87400, 110)], 24)
    assert a['status'] == 'sampled_T1_before_sampled_stop'


async def test_legacy_receipt_projection_still_rejects_changed_aggregate(monkeypatch):
    import pytest

    from algotrader.domain.models import DerivativesSnapshot
    from scripts import research_evidence_contract as contract
    from scripts.replay_contract import ReplayUnavailable, digest
    from tests.test_capture_evidence import bundle_fixture

    bundle = await bundle_fixture()
    original_call = next(c for c in (*bundle['record'].inputs, *bundle['supplemental'])
                         if isinstance(c.result, DerivativesSnapshot))
    original_frame = next(f for f in bundle['frames'] if f['call_sha256'] == digest(original_call))
    call = replace(original_call, result=legacy_aggregate_projection(original_call.result))
    original = contract.normalized
    monkeypatch.setattr(contract, 'normalized', lambda value: original(legacy_aggregate_projection(value)))
    frame = {**original_frame, 'call_sha256': digest(call)}
    await contract.verify_call(call, frame, bundle['receipts'], bundle['completed_at'])
    bad = replace(call, result=replace(call.result, open_interest=call.result.open_interest + 1))
    with pytest.raises(ReplayUnavailable, match='adapter_result_mismatch'):
        await contract.verify_call(bad, {**frame, 'call_sha256': digest(bad)},
                                   bundle['receipts'], bundle['completed_at'])
