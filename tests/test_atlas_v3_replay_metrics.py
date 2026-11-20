"""Regression coverage for causal, bounded counterfactual replay measurement."""
from scripts.replay_atlas_v3_rolling import compute_forward_metrics


def outcome(points, entry=100.0):
    return compute_forward_metrics('TESTUSDT', 1000.0, entry, {'TESTUSDT': points}, set(), {})


def test_entry_is_recorded_quote_and_pre_alert_prices_do_not_enter_path():
    result = outcome([(999, 1), (1001, 200), (87400, 110), (173800, 120)])
    assert result['entry_quote_price'] == 100
    assert result['ret_24h_pct'] == 10.000000000000009
    assert result['mae_24h_pct'] == 0
    assert result['mfe_24h_pct'] == 100


def test_endpoint_uses_first_known_after_exact_horizon_with_bounded_delay():
    result = outcome([(1000, 100), (87399, 50), (87405, 110), (88600, 200), (173800, 120)])
    assert round(result['ret_24h_pct'], 6) == 10
    assert result['endpoint_24h_offset_seconds'] == 5
    # The +20 minute price cannot replace the +24h endpoint.
    assert round(result['ret_48h_pct'], 6) == 20


def test_immature_or_missing_endpoints_remain_unknown_and_censored():
    immature = outcome([(1000, 100), (87400 - 1, 110)])
    assert immature['outcome_24h_status'] == 'censored'
    assert immature['ret_24h_pct'] is None
    assert immature['mfe_24h_pct'] is None
    missing = outcome([(1000, 100), (87400 + 346, 110), (173800, 120)])
    assert missing['outcome_24h_status'] == 'unknown'
    assert missing['ret_24h_pct'] is None


def test_unobserved_initial_membership_does_not_become_certified_early(tmp_path):
    import json

    from scripts.replay_atlas_v3_rolling import load_price_grid
    from scripts.research_prospective import save

    report = tmp_path / 'report.json'
    save(report, {'study_id': 'test-study', 'symbols': [
        {'symbol': 'UNKNOWN', 'initial_inside': None, 'first_observed_top10_at': None},
        {'symbol': 'OUTSIDE', 'initial_inside': False, 'first_observed_top10_at': None},
    ]})
    grid = tmp_path / 'grid.json'
    grid.write_text(json.dumps({'snapshots': [{
        'arrived_at': '2026-11-15T16:00:00+00:00',
        'prices': {'UNKNOWN': 1, 'OUTSIDE': 1}, 'top10': ['UNKNOWN', 'OUTSIDE'],
    }]}))
    _, _, first, _ = load_price_grid(grid, report)
    assert set(first) == {'OUTSIDE'}


def test_prior_observed_leader_is_excluded_without_certifying_unknown_initial_membership():
    result = compute_forward_metrics(
        'TESTUSDT', 1000, 100, {'TESTUSDT': [(1000, 100), (87400, 110)]},
        set(), {}, {'TESTUSDT': 900},
    )
    assert result['already_top10_at_signal'] is True
    assert result['early_before_top10'] is False


def test_gate_checklist_and_conviction_checklist_are_distinct():
    from scripts.replay_atlas_v3_rolling import compact_opportunity

    result = compact_opportunity({
        'checklist_verification': {'passed_count': 4},
        'checklist': [{'passed': True} for _ in range(5)],
    })
    assert result['checklist_passed'] == 4
    assert result['portal_fresh_eligible'] is True
