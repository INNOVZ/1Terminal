from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from algotrader.domain.rank_observations import RankPolicy, normalize_ranks
from algotrader.domain.rank_timeline import initial_rank_state, membership_timeline
from algotrader.domain.scan_observations import RecordedCall
from algotrader.infrastructure.rank_archive import digest
from tests.test_rank_archive import snapshot_fixture
from tests.test_shadow_archive import record_fixture
from tests.test_shadow_discovery import NOW, parent_fixture, ticker_fixture


def timeline_child():
    parent = parent_fixture()
    return replace(
        record_fixture(),
        cohort=parent.cohort,
        parent_checksum=digest(parent),
        inputs=(RecordedCall("decision_tickers", 0, {}, ticker_fixture(parent)),),
        timing={"snapshot_requested_at": NOW, "snapshot_arrived_at": NOW, "classified_at": NOW},
        results=({"symbol": "T0USDT", "qualified": True, "direction": "long"},),
    )


def followup(minutes, *, inside=False, milliseconds=0):
    base = snapshot_fixture()
    stamp = NOW + timedelta(minutes=minutes, milliseconds=milliseconds)
    rows = ticker_fixture(parent_fixture())
    for row in rows:
        row["closeTime"] = int(stamp.timestamp() * 1000)
    if inside:
        rows[0]["priceChangePercent"] = "100"
    return replace(
        base,
        snapshot_id=str(uuid4()),
        rows=tuple(rows),
        requested_at=stamp,
        arrived_at=stamp,
        completed_at=stamp,
        normalized=normalize_ranks(base.cohort, tuple(rows), stamp, RankPolicy()),
    )


def timeline(child, snapshots, hours=24, as_of=None):
    return membership_timeline(
        child,
        initial_rank_state(child, RankPolicy()),
        tuple(snapshots),
        hours,
        as_of or NOW + timedelta(hours=hours),
        RankPolicy(),
    )


def test_inside_unknown_and_observed_are_distinct():
    child = timeline_child()
    result = timeline(child, [followup(5), followup(20, inside=True)])
    assert result["members"]["T0USDT"]["status"] == "observed_membership"
    first = result["members"]["T0USDT"]["first_observed_membership"]
    assert first["arrived_at"] == NOW + timedelta(minutes=20)
    assert first["lag_from_child_seconds"] == 1200
    assert first["previous_outside"]["arrived_at"] == NOW + timedelta(minutes=5)
    assert result["sampling"]["sufficient"] is False
    assert result["members"]["T19USDT"]["status"] == "already_inside"
    unknown = timeline(replace(child, inputs=()), [followup(20, inside=True)])
    assert unknown["members"]["T0USDT"]["status"] == "unavailable_initial"
    assert unknown["members"]["T0USDT"]["first_observed_membership"] is None
    assert result["original_cohort_size"] == 20


def test_exact_boundaries_and_sampling_gap():
    child = timeline_child()
    samples = [followup(i) for i in range(10, 1440, 10)]
    result = timeline(child, samples)
    assert result["sampling"]["sufficient"] is True
    assert result["members"]["T0USDT"]["status"] == "not_observed_in_sufficient_samples"
    samples[0] = followup(10, milliseconds=1)
    assert timeline(child, samples)["sampling"]["sufficient"] is False
    assert (
        timeline(child, [], as_of=NOW + timedelta(hours=1))["members"]["T0USDT"]["status"]
        == "not_due"
    )
    assert timeline(child, [], hours=48)["members"]["T0USDT"]["status"] == "insufficient_sampling"


def test_duplicate_nonadvancing_partial_and_late_samples_do_not_fill_gaps():
    child = timeline_child()
    first = followup(5)
    duplicate = replace(
        first,
        snapshot_id=str(uuid4()),
        arrived_at=NOW + timedelta(minutes=6),
        completed_at=NOW + timedelta(minutes=6),
    )
    partial = followup(7)
    partial = replace(
        partial,
        rows=partial.rows[:-1],
        normalized=normalize_ranks(
            partial.cohort, partial.rows[:-1], partial.arrived_at, RankPolicy()
        ),
        status="partial",
    )
    result = timeline(
        child,
        [followup(1441, inside=True), duplicate, partial, first],
        as_of=NOW + timedelta(hours=25),
    )
    assert result["sampling"]["accepted_count"] == 1
    assert {d["reason"] for d in result["sampling"]["excluded"]} == {
        "outside_window",
        "nonadvancing_cutoffs",
        "incomplete",
    }
    assert result["members"]["T0USDT"]["status"] == "insufficient_sampling"
    assert (
        timeline(child, [followup(10, inside=True)], as_of=NOW + timedelta(minutes=9))["sampling"][
            "accepted_count"
        ]
        == 0
    )
    assert (
        timeline(child, [followup(1440, inside=True)])["members"]["T0USDT"]["status"]
        == "observed_membership"
    )


def test_initial_uses_latest_final_raw_evidence_not_labels():
    child = timeline_child()
    child = replace(child, labels=({"symbol": "T0USDT", "early_candidate": False},))
    assert initial_rank_state(child, RankPolicy())["states"]["T0USDT"] == "outside"
    failed = RecordedCall("decision_tickers", 1, {}, error="failed")
    assert (
        initial_rank_state(replace(child, inputs=child.inputs + (failed,)), RankPolicy())["states"][
            "T0USDT"
        ]
        == "unknown"
    )
    with pytest.raises(ValueError):
        timeline(child, [], hours=12)


def test_followup_must_satisfy_declared_report_freshness_policy():
    child = timeline_child()
    sample = followup(5, inside=True)
    rows = tuple(dict(row, closeTime=row["closeTime"] - 59000) for row in sample.rows)
    sample = replace(
        sample,
        rows=rows,
        normalized=normalize_ranks(sample.cohort, rows, sample.arrived_at, RankPolicy()),
    )
    assert sample.normalized["complete"] is True
    strict = RankPolicy(snapshot_max_age_seconds=1)
    result = membership_timeline(
        child, initial_rank_state(child, strict), (sample,), 24, NOW + timedelta(hours=24), strict
    )
    assert result["sampling"]["accepted_count"] == 0
    assert result["sampling"]["excluded"][0]["reason"] == "incomplete_under_report_policy"
    assert result["members"]["T0USDT"]["first_observed_membership"] is None
