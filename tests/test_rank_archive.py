import hashlib
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
from uuid import uuid4

import pytest

from algotrader.domain.rank_observations import (
    RankPolicy,
    RankSnapshotRecord,
    normalize_ranks,
)
from algotrader.infrastructure.rank_archive import LocalRankArchive
from algotrader.infrastructure.scan_archive import (
    ArchiveConflict,
    ArchiveIntegrityError,
    canonical_bytes,
)
from tests.test_shadow_discovery import NOW, parent_fixture, ticker_fixture


def snapshot_fixture():
    parent = parent_fixture()
    rows = tuple(ticker_fixture(parent))
    return RankSnapshotRecord(
        str(uuid4()),
        parent.scan_id,
        hashlib.sha256(canonical_bytes(parent)).hexdigest(),
        parent.cohort,
        hashlib.sha256(canonical_bytes(parent.cohort)).hexdigest(),
        asdict(RankPolicy()),
        {"source": "test-v1"},
        rows,
        normalize_ranks(parent.cohort, rows, NOW, RankPolicy()),
        NOW,
        NOW,
        NOW,
        {"attempts": 1},
        "complete",
    )


def test_rank_archive_conflicts_and_offline_reconstruction(tmp_path):
    archive = LocalRankArchive(tmp_path)
    record = snapshot_fixture()
    archive.write_snapshot(record)
    archive.write_snapshot(record)
    restored = archive.read_snapshot(record.parent_id, record.snapshot_id)
    assert restored == record
    assert (
        normalize_ranks(
            restored.cohort, restored.rows, restored.arrived_at, RankPolicy(**restored.policy)
        )
        == restored.normalized
    )
    with pytest.raises(ArchiveConflict):
        archive.write_snapshot(replace(record, identity={"source": "changed"}))
    directory = tmp_path / "ranks" / record.parent_id / record.snapshot_id
    (directory / "payload.json").write_bytes(b"corrupt")
    with pytest.raises(ArchiveIntegrityError):
        archive.read_snapshot(record.parent_id, record.snapshot_id)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": 2},
        {"snapshot_id": "../unsafe"},
        {"cohort_hash": "bad"},
        {"normalized": {}},
        {"arrived_at": NOW.replace(tzinfo=None)},
        {"completed_at": NOW - timedelta(seconds=1)},
        {"status": "failed"},
    ],
)
def test_rank_archive_rejects_inconsistent_records(tmp_path, changes):
    with pytest.raises(ValueError):
        LocalRankArchive(tmp_path).write_snapshot(replace(snapshot_fixture(), **changes))


def test_partial_failed_concurrent_and_report_roundtrip(tmp_path):
    archive = LocalRankArchive(tmp_path)
    good = snapshot_fixture()
    failed = replace(
        good,
        snapshot_id=str(uuid4()),
        rows=(),
        normalized={},
        arrived_at=None,
        status="failed",
        error="timeout",
    )
    partial_rows = good.rows[:-1]
    partial = replace(
        good,
        snapshot_id=str(uuid4()),
        rows=partial_rows,
        normalized=normalize_ranks(good.cohort, partial_rows, NOW, RankPolicy()),
        status="partial",
    )
    with ThreadPoolExecutor() as pool:
        list(pool.map(archive.write_snapshot, [good, failed, partial]))
    assert {r.status for r in archive.snapshots(good.parent_id)} == {
        "complete",
        "partial",
        "failed",
    }
    report = complete_report_fixture(tmp_path / "report-source")
    archive.write_report(report)
    assert archive.read_report(report.parent_id, report.report_id) == report
    with pytest.raises(ValueError):
        archive.write_report(replace(report, horizon_hours=12))


def test_atomic_failure_does_not_publish_snapshot(tmp_path, monkeypatch):
    import algotrader.infrastructure.scan_archive as module

    archive = LocalRankArchive(tmp_path)
    record = snapshot_fixture()

    def interrupted(*args):
        raise OSError("interrupted")

    monkeypatch.setattr(module.os, "rename", interrupted)
    with pytest.raises(OSError):
        archive.write_snapshot(record)
    assert archive.snapshots(record.parent_id) == ()


def test_checksums_do_not_excuse_semantically_tampered_ranks(tmp_path):
    archive = LocalRankArchive(tmp_path)
    record = snapshot_fixture()
    payload = {"kind": "rank_snapshot", "record": asdict(record)}
    # Keep instrument dataclasses intact, as the explicit archive codec expects.
    payload["record"]["cohort"] = record.cohort
    payload["record"]["normalized"] = {**record.normalized, "complete": False}
    archive._store._write(tmp_path / "ranks" / record.parent_id / record.snapshot_id, payload)
    with pytest.raises(ArchiveIntegrityError):
        archive.read_snapshot(record.parent_id, record.snapshot_id)


def complete_report_fixture(root):
    from algotrader.application.shadow_benchmark import ShadowBenchmark
    from tests.test_shadow_benchmark import benchmark_fixture

    _, child = benchmark_fixture(root)
    return ShadowBenchmark(root, RankPolicy()).build(child.shadow_id, 24, as_of=NOW)


@pytest.mark.parametrize(
    "mutation",
    [
        "identity",
        "manifest",
        "payload",
        "parent",
        "child",
        "horizon",
        "policy",
        "anchor",
        "as_of",
        "checksum",
        "selected",
        "source_version",
    ],
)
def test_report_write_rejects_missing_or_cross_inconsistent_provenance(tmp_path, mutation):
    record = complete_report_fixture(tmp_path)
    identity, manifest, payload = (
        deepcopy(record.identity),
        deepcopy(record.manifest),
        deepcopy(record.payload),
    )
    if mutation == "identity":
        identity = {}
    elif mutation == "manifest":
        manifest = {}
    elif mutation == "payload":
        payload = {}
    elif mutation == "parent":
        manifest["parent_id"] = str(uuid4())
    elif mutation == "child":
        manifest["shadow_id"] = str(uuid4())
    elif mutation == "horizon":
        payload["horizon_hours"] = 48
    elif mutation == "policy":
        manifest["policy"]["snapshot_max_age_seconds"] = 1
    elif mutation == "anchor":
        payload["anchor"] += timedelta(minutes=1)
    elif mutation == "as_of":
        manifest["as_of"] += timedelta(minutes=1)
    elif mutation == "checksum":
        manifest["rank_snapshots"] = ({"snapshot_id": str(uuid4()), "checksum": "invalid"},)
    elif mutation == "selected":
        manifest["selected_outcomes"] = {
            "T0USDT": {"attempt_id": str(uuid4()), "checksum": "a" * 64}
        }
    elif mutation == "source_version":
        identity["report_policy"] = "unknown-report-v2"
        manifest["identity"] = identity
    changed = replace(
        record, report_id=str(uuid4()), identity=identity, manifest=manifest, payload=payload
    )
    with pytest.raises(ValueError):
        LocalRankArchive(tmp_path).write_report(changed)


def test_checksummed_but_semantically_invalid_report_is_rejected_on_read(tmp_path):
    from algotrader.infrastructure.rank_archive import envelope

    record = complete_report_fixture(tmp_path)
    changed = replace(
        record, report_id=str(uuid4()), manifest={**record.manifest, "parent_id": str(uuid4())}
    )
    archive = LocalRankArchive(tmp_path)
    archive._store._write(
        tmp_path / "benchmarks" / changed.parent_id / changed.report_id,
        envelope("rank_benchmark", changed),
    )
    with pytest.raises(ArchiveIntegrityError):
        archive.read_report(changed.parent_id, changed.report_id)
