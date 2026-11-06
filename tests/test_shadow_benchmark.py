from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from algotrader.application.shadow_benchmark import ShadowBenchmark
from algotrader.application.shadow_observer import _measurement_identity
from algotrader.domain.forward_outcomes import ForwardObservation, outcome_anchor
from algotrader.domain.rank_observations import RankPolicy
from algotrader.domain.rank_timeline import initial_rank_state
from algotrader.domain.scan_observations import RecordedCall
from algotrader.domain.shadow_benchmark import freeze_benchmark_arms
from algotrader.infrastructure.rank_archive import LocalRankArchive, digest
from algotrader.infrastructure.scan_archive import ArchiveIntegrityError, LocalScanArchive
from algotrader.infrastructure.shadow_archive import LocalShadowArchive
from tests.test_rank_timeline import followup, timeline_child
from tests.test_shadow_discovery import NOW, parent_fixture


def benchmark_fixture(root):
    parent = replace(
        parent_fixture(),
        opportunities=(
            {"symbol": "T1USDT", "direction": "long"},
            {"symbol": "T19USDT", "direction": "short"},
        ),
    )
    child = timeline_child()
    rows = [
        dict(r, quoteVolume="1500000", openTime=int((NOW - timedelta(days=1)).timestamp() * 1000))
        for r in child.inputs[0].result
    ]
    child = replace(
        child,
        parent_checksum=digest(parent),
        inputs=(RecordedCall("decision_tickers", 0, {}, rows),),
        results=child.results + ({"symbol": "T2USDT", "qualified": False, "direction": "long"},),
    )
    LocalScanArchive(root).write_scan(parent)
    LocalShadowArchive(root).write_run(child)
    sample = replace(followup(5, inside=True), parent_checksum=digest(parent))
    LocalRankArchive(root).write_snapshot(sample)
    return parent, child


def save_outcome(root, child, symbol="T0USDT", **changes):
    anchor = outcome_anchor(child.completed_at)
    observation = ForwardObservation(
        "complete",
        anchor,
        24,
        baseline=Decimal("100"),
        end_price=Decimal("110"),
        raw_return_pct=Decimal("10"),
    )
    payload = {
        "attempt_id": str(uuid4()),
        "shadow_id": child.shadow_id,
        "symbol": symbol,
        "anchor": anchor,
        "horizon_hours": 24,
        "observed_at": NOW + timedelta(hours=25),
        "measurement_identity": _measurement_identity(
            child.parent_checksum, child.shadow_id, child.completed_at
        ),
        "observation": observation.as_dict(),
        "candles": (),
        "touches": None,
        **changes,
    }
    LocalShadowArchive(root).write_outcome(
        child.shadow_id, 24, symbol, payload["attempt_id"], payload
    )
    return payload


def test_frozen_inputs_exclude_late_commits_and_reuse_reserved_report(tmp_path):
    parent, child = benchmark_fixture(tmp_path)
    benchmark = ShadowBenchmark(tmp_path, RankPolicy())
    as_of = NOW + timedelta(hours=26)
    inputs = benchmark.freeze_inputs(child.shadow_id, 24, as_of=as_of)
    assert not (tmp_path / "benchmarks").exists()
    assert len(inputs["rank_snapshots"]) == 1
    save_outcome(tmp_path, child, symbol="T1USDT")
    late = replace(
        followup(10, inside=False), parent_checksum=digest(parent), snapshot_id=str(uuid4())
    )
    LocalRankArchive(tmp_path).write_snapshot(late)
    identifier = str(uuid4())
    frozen = benchmark.build(child.shadow_id, 24, as_of=as_of, report_id=identifier, inputs=inputs)
    assert frozen.report_id == identifier
    assert frozen.manifest == inputs
    assert frozen.payload["outcomes"]["T1USDT"]["status"] == "unavailable"
    assert (
        benchmark.build(child.shadow_id, 24, as_of=as_of, report_id=identifier, inputs=inputs)
        == frozen
    )
    fresh = benchmark.build(child.shadow_id, 24, as_of=as_of)
    assert fresh.payload["outcomes"]["T1USDT"]["status"] == "complete"
    assert len(fresh.manifest["rank_snapshots"]) == 2


@pytest.mark.parametrize("frozen", [False, True])
def test_report_does_not_retain_decoded_rank_snapshots(tmp_path, monkeypatch, frozen):
    import weakref

    parent, child = benchmark_fixture(tmp_path)
    for minute in range(10, 100, 5):
        LocalRankArchive(tmp_path).write_snapshot(
            replace(followup(minute, inside=True), parent_checksum=digest(parent))
        )
    builder = ShadowBenchmark(tmp_path, RankPolicy())
    inputs = (
        builder.freeze_inputs(child.shadow_id, 24, as_of=NOW + timedelta(hours=26))
        if frozen
        else None
    )
    references = []
    original = LocalRankArchive.read_snapshot

    def tracked_read(self, *args):
        record = original(self, *args)
        references.append(weakref.ref(record))
        # A streaming reader/consumer may hold the current and previous item,
        # never the full horizon of raw rows and repeated instrument objects.
        assert sum(ref() is not None for ref in references) <= 2
        return record

    monkeypatch.setattr(LocalRankArchive, "read_snapshot", tracked_read)
    report = builder.build(child.shadow_id, 24, as_of=NOW + timedelta(hours=26), inputs=inputs)
    assert len(report.manifest["rank_snapshots"]) == 19
    assert report.payload["timeline"]["sampling"]["accepted_count"] == 19


@pytest.mark.parametrize("mutation", ["checksum", "policy", "source", "as_of"])
def test_frozen_input_tampering_fails_closed(tmp_path, mutation):
    import copy

    _, child = benchmark_fixture(tmp_path)
    benchmark = ShadowBenchmark(tmp_path, RankPolicy())
    as_of = NOW + timedelta(hours=26)
    inputs = copy.deepcopy(benchmark.freeze_inputs(child.shadow_id, 24, as_of=as_of))
    if mutation == "checksum":
        inputs["rank_snapshots"][0]["checksum"] = "0" * 64
    elif mutation == "policy":
        inputs["policy"]["max_attempts"] = 1
    elif mutation == "source":
        inputs["identity"]["source"] = "0" * 64
    else:
        inputs["as_of"] = as_of - timedelta(seconds=1)
    with pytest.raises(ArchiveIntegrityError):
        benchmark.build(child.shadow_id, 24, as_of=as_of, inputs=inputs)
    assert not (tmp_path / "benchmarks").exists()


def test_benchmark_never_constructs_network_and_uses_child_anchor(tmp_path, monkeypatch):
    parent, child = benchmark_fixture(tmp_path)
    attempt = save_outcome(tmp_path, child)

    def forbidden(*args, **kwargs):
        raise AssertionError("network construction forbidden")

    monkeypatch.setattr("httpx.AsyncClient", forbidden)
    monkeypatch.setattr("httpx.Client", forbidden)
    benchmark = ShadowBenchmark(tmp_path, RankPolicy())
    first = benchmark.build(child.shadow_id, 24, as_of=NOW + timedelta(hours=26))
    second = benchmark.build(child.shadow_id, 24, as_of=first.as_of)
    assert first.payload == second.payload
    assert first.manifest == second.manifest
    assert first.report_id != second.report_id
    assert first.payload["comparison"] == "matched_forward_anchor_staggered_decisions"
    assert first.payload["anchor"] == NOW
    assert first.payload["original_cohort_size"] == 20
    assert first.payload["arms"]["core_longs"]["symbols"] == ("T1USDT",)
    assert first.payload["arms"]["child_longs"]["raw_forward_returns"] == {"T0USDT": Decimal("10")}
    assert first.payload["arms"]["core_longs"]["unknown_outcome_count"] == 1
    assert first.payload["core_shorts"] == ({"symbol": "T19USDT", "direction": "short"},)
    assert first.payload["forward_cohort"]["full_coverage"] is False
    assert first.payload["forward_cohort"]["ranks"] == {}  # no reduced-cohort rank claims
    assert first.manifest["selected_outcomes"]["T0USDT"]["attempt_id"] == attempt["attempt_id"]
    assert LocalRankArchive(tmp_path).read_report(parent.scan_id, first.report_id) == first


@pytest.mark.parametrize(
    "changes",
    [{"anchor": NOW - timedelta(minutes=1)}, {"horizon_hours": 48}, {"shadow_id": str(uuid4())}],
)
def test_wrong_compatible_outcome_identity_is_rejected(tmp_path, changes):
    _, child = benchmark_fixture(tmp_path)
    save_outcome(tmp_path, child, **changes)
    with pytest.raises(ArchiveIntegrityError):
        ShadowBenchmark(tmp_path, RankPolicy()).build(
            child.shadow_id, 24, as_of=NOW + timedelta(hours=26)
        )


def test_incompatible_and_late_paths_remain_unavailable(tmp_path):
    _, child = benchmark_fixture(tmp_path)
    save_outcome(tmp_path, child, measurement_identity="old-source")
    save_outcome(tmp_path, child, observed_at=NOW + timedelta(hours=27))
    report = ShadowBenchmark(tmp_path, RankPolicy()).build(
        child.shadow_id, 24, as_of=NOW + timedelta(hours=26)
    )
    assert report.payload["arms"]["child_longs"]["unknown_outcome_count"] == 1
    assert report.manifest["selected_outcomes"] == {}


def test_momentum_reference_is_frozen_volume_filtered_and_not_backfilled(tmp_path):
    parent, child = benchmark_fixture(tmp_path)
    initial = initial_rank_state(child, RankPolicy())
    arms = freeze_benchmark_arms(parent, child, initial, RankPolicy())
    assert arms["momentum_reference"]["symbols"] == (
        "T9USDT",
        "T8USDT",
        "T7USDT",
        "T6USDT",
        "T5USDT",
    )
    rows = [dict(r) for r in child.inputs[0].result]
    rows[9]["quoteVolume"] = "1499999"
    rows[8]["priceChangePercent"] = "7"
    rows[8]["quoteVolume"] = "2000000"
    changed = replace(child, inputs=(RecordedCall("decision_tickers", 0, {}, rows),))
    arms = freeze_benchmark_arms(
        parent, changed, initial_rank_state(changed, RankPolicy()), RankPolicy()
    )
    assert arms["momentum_reference"]["symbols"] == (
        "T8USDT",
        "T7USDT",
        "T6USDT",
        "T5USDT",
        "T4USDT",
    )
    rows[0].pop("quoteVolume")
    changed = replace(child, inputs=(RecordedCall("decision_tickers", 0, {}, rows),))
    unavailable = freeze_benchmark_arms(
        parent, changed, initial_rank_state(changed, RankPolicy()), RankPolicy()
    )
    assert unavailable["momentum_reference"]["available"] is False


def test_parent_drift_and_horizon_validation(tmp_path):
    parent, child = benchmark_fixture(tmp_path)
    with pytest.raises(ValueError):
        ShadowBenchmark(tmp_path, RankPolicy()).build(child.shadow_id, 12, as_of=NOW)
    # A checksummed but mismatching immutable parent must still be rejected.
    bad_root = tmp_path / "other"
    LocalScanArchive(bad_root).write_scan(replace(parent, counts={"changed": 1}))
    LocalShadowArchive(bad_root).write_run(child)
    with pytest.raises(ArchiveIntegrityError):
        ShadowBenchmark(bad_root, RankPolicy()).build(child.shadow_id, 24, as_of=NOW)


def test_unequal_arm_top_five_does_not_drop_missing_paths(tmp_path):
    _, child = benchmark_fixture(tmp_path)
    save_outcome(tmp_path, child, symbol="T9USDT")
    report = ShadowBenchmark(tmp_path, RankPolicy()).build(
        child.shadow_id, 24, as_of=NOW + timedelta(hours=26)
    )
    assert report.payload["arms"]["core_longs"]["top_five"] == {"status": "insufficient_candidates"}
    reference = report.payload["arms"]["momentum_reference"]
    assert reference["top_five"]["symbols"] == ("T9USDT", "T8USDT", "T7USDT", "T6USDT", "T5USDT")
    assert reference["unknown_outcome_count"] == 4
    assert reference["top_five"]["raw_forward_returns"]["T8USDT"] is None


def test_as_of_before_child_decision_cannot_publish_future_arms(tmp_path):
    _, child = benchmark_fixture(tmp_path)
    with pytest.raises(ValueError, match="as-of"):
        ShadowBenchmark(tmp_path, RankPolicy()).build(
            child.shadow_id, 24, as_of=child.completed_at - timedelta(microseconds=1)
        )
    assert not (tmp_path / "benchmarks").exists()
    report = ShadowBenchmark(tmp_path, RankPolicy()).build(
        child.shadow_id, 24, as_of=child.completed_at
    )
    assert report.payload["timeline"]["sampling"]["accepted_count"] == 0


@pytest.mark.parametrize("dependency", ["rank_observations.py", "shadow_discovery.py"])
def test_report_source_identity_covers_rank_semantic_dependencies(
    tmp_path, monkeypatch, dependency
):
    _, child = benchmark_fixture(tmp_path)
    service = ShadowBenchmark(tmp_path, RankPolicy())
    first = service.build(child.shadow_id, 24, as_of=NOW)
    read_text = Path.read_text

    def drifted(path, *args, **kwargs):
        content = read_text(path, *args, **kwargs)
        return content + "\n# semantic dependency drift\n" if path.name == dependency else content

    monkeypatch.setattr(Path, "read_text", drifted)
    second = service.build(child.shadow_id, 24, as_of=NOW)
    assert first.identity["source"] != second.identity["source"]


def test_collector_provenance_covers_classifier_dependency(tmp_path, monkeypatch):
    from algotrader.application.rank_collector import RankCollector

    first = RankCollector(tmp_path, RankPolicy()).identity
    read_text = Path.read_text

    def drifted(path, *args, **kwargs):
        content = read_text(path, *args, **kwargs)
        return content + "\n# classifier drift\n" if path.name == "shadow_discovery.py" else content

    monkeypatch.setattr(Path, "read_text", drifted)
    second = RankCollector(tmp_path, RankPolicy()).identity
    assert first != second
