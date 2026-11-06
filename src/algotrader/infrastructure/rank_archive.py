"""Immutable rank and benchmark envelopes, isolated from core scan schemas."""

from __future__ import annotations

import hashlib
import re
from dataclasses import fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from algotrader.domain.forward_outcomes import outcome_anchor
from algotrader.domain.rank_observations import (
    BenchmarkRecord,
    RankPolicy,
    RankSnapshotRecord,
    normalize_ranks,
    validate_cohort,
)
from algotrader.domain.scan_observations import ScanReceipt
from algotrader.infrastructure.scan_archive import (
    ArchiveIntegrityError,
    LocalScanArchive,
    canonical_bytes,
    validated_id,
)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def envelope(kind: str, record: RankSnapshotRecord | BenchmarkRecord) -> dict[str, Any]:
    return {"kind": kind, "record": {f.name: getattr(record, f.name) for f in fields(record)}}


def aware(value: Any) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("rank timestamps must be aware")


def validate_snapshot(record: RankSnapshotRecord) -> None:
    validated_id(record.parent_id)
    validated_id(record.snapshot_id)
    if type(record.schema_version) is not int or record.schema_version != 1:
        raise ValueError("unsupported rank schema")
    policy = RankPolicy(**record.policy)
    validate_cohort(record.cohort, policy)
    if (
        record.cohort_hash != digest(record.cohort)
        or re.fullmatch(r"[a-f0-9]{64}", record.parent_checksum) is None
    ):
        raise ValueError("rank cohort/parent checksum mismatch")
    for required_time in (record.requested_at, record.completed_at):
        aware(required_time)
    if record.completed_at < record.requested_at:
        raise ValueError("rank clock order invalid")
    for time in (record.arrived_at, record.provider_requested_at, record.provider_finished_at):
        if time is not None:
            aware(time)
    if (
        not isinstance(record.identity, dict)
        or not record.identity
        or not isinstance(record.accounting, dict)
    ):
        raise ValueError("missing rank provenance")
    if record.status == "failed":
        if not record.error or record.normalized or record.rows:
            raise ValueError("invalid failed rank snapshot")
        return
    if (
        record.status not in ("complete", "partial")
        or record.error is not None
        or record.arrived_at is None
    ):
        raise ValueError("invalid rank terminal state")
    if not record.requested_at <= record.arrived_at <= record.completed_at:
        raise ValueError("rank arrival out of order")
    expected = normalize_ranks(record.cohort, record.rows, record.arrived_at, policy)
    if canonical_bytes(expected) != canonical_bytes(record.normalized):
        raise ValueError("rank evidence does not reconstruct normalized ranks")
    if expected["complete"] != (record.status == "complete"):
        raise ValueError("rank status disagrees with coverage")


def validate_report(record: BenchmarkRecord) -> None:
    for identifier in (record.report_id, record.parent_id, record.shadow_id):
        validated_id(identifier)
    if (
        type(record.schema_version) is not int
        or record.schema_version != 1
        or type(record.horizon_hours) is not int
        or record.horizon_hours not in (24, 48)
    ):
        raise ValueError("unsupported benchmark schema/horizon")
    RankPolicy(**record.policy)
    aware(record.as_of)
    aware(record.created_at)
    if not all(isinstance(v, dict) for v in (record.manifest, record.payload, record.identity)):
        raise ValueError("invalid benchmark envelope")
    try:
        _validate_report_content(record)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("malformed benchmark content") from exc


def _checksum(value: Any) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ValueError("invalid benchmark checksum")


def _validate_report_content(record: BenchmarkRecord) -> None:
    manifest, payload, identity = record.manifest, record.payload, record.identity
    if identity["report_policy"] != "matched-anchor-benchmark-v1":
        raise ValueError("unsupported report policy")
    _checksum(identity["source"])
    _checksum(identity["measurement_identity"])
    for key, expected in (
        ("parent_id", record.parent_id),
        ("shadow_id", record.shadow_id),
        ("as_of", record.as_of),
        ("horizon_hours", record.horizon_hours),
        ("policy", record.policy),
        ("identity", identity),
    ):
        if canonical_bytes(manifest[key]) != canonical_bytes(expected):
            raise ValueError("benchmark manifest/envelope mismatch")
    _checksum(manifest["parent_checksum"])
    _checksum(manifest["shadow_checksum"])
    if (
        payload["comparison"] != "matched_forward_anchor_staggered_decisions"
        or payload["validation_status"] != "uncalibrated_research"
        or payload["horizon_hours"] != record.horizon_hours
    ):
        raise ValueError("invalid benchmark comparison/horizon")
    for stamp in (payload["parent_completed_at"], payload["child_completed_at"], payload["anchor"]):
        aware(stamp)
    if not payload["parent_completed_at"] <= payload[
        "child_completed_at"
    ] <= record.as_of or payload["anchor"] != outcome_anchor(payload["child_completed_at"]):
        raise ValueError("invalid benchmark chronology/anchor")
    if (
        payload["parent_child_lag_seconds"]
        != (payload["child_completed_at"] - payload["parent_completed_at"]).total_seconds()
    ):
        raise ValueError("invalid benchmark decision lag")
    size, outcomes = payload["original_cohort_size"], payload["outcomes"]
    if (
        type(size) is not int
        or not 1 <= size <= 1000
        or not isinstance(outcomes, dict)
        or len(outcomes) != size
    ):
        raise ValueError("invalid benchmark denominator")
    symbols = set(outcomes)
    if any(re.fullmatch(r"[A-Z0-9_]{1,80}", s) is None for s in symbols):
        raise ValueError("invalid benchmark symbols")
    timeline, initial = payload["timeline"], payload["initial"]
    if (
        timeline["anchor"] != payload["anchor"]
        or timeline["as_of"] != record.as_of
        or timeline["end"] != payload["anchor"] + timedelta(hours=record.horizon_hours)
        or timeline["original_cohort_size"] != size
        or set(timeline["members"]) != symbols
        or set(initial["states"]) != symbols
        or initial["child_completed_at"] != payload["child_completed_at"]
    ):
        raise ValueError("benchmark timeline/initial identity mismatch")
    for outcome in outcomes.values():
        if (
            outcome["anchor"] != payload["anchor"]
            or outcome["horizon_hours"] != record.horizon_hours
            or outcome["status"] not in ("complete", "unavailable")
        ):
            raise ValueError("benchmark outcome identity mismatch")
    refs = manifest["rank_snapshots"]
    if not isinstance(refs, tuple):
        raise ValueError("invalid rank input manifest")
    ids = []
    for reference in refs:
        ids.append(validated_id(reference["snapshot_id"]))
        _checksum(reference["checksum"])
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate benchmark snapshot input")
    considered, selected = manifest["considered_outcomes"], manifest["selected_outcomes"]
    if (
        not isinstance(considered, dict)
        or set(considered) != symbols
        or not isinstance(selected, dict)
        or not set(selected) <= symbols
    ):
        raise ValueError("invalid benchmark outcome manifest")
    for symbol, attempts in considered.items():
        if not isinstance(attempts, list):
            raise ValueError("invalid considered attempts")
        attempt_ids = []
        for attempt in attempts:
            attempt_ids.append(validated_id(attempt["attempt_id"]))
            _checksum(attempt["checksum"])
            if type(attempt["compatible"]) is not bool:
                raise ValueError("invalid compatibility flag")
        if len(set(attempt_ids)) != len(attempt_ids):
            raise ValueError("duplicate considered attempt")
        if symbol in selected:
            reference = selected[symbol]
            validated_id(reference["attempt_id"])
            _checksum(reference["checksum"])
            if not any(
                a["attempt_id"] == reference["attempt_id"]
                and a["checksum"] == reference["checksum"]
                and a["compatible"]
                for a in attempts
            ):
                raise ValueError("selected outcome absent from compatible inputs")
        if (symbol in selected) != (outcomes[symbol]["status"] == "complete"):
            raise ValueError("selected outcome disagrees with report payload")
    if set(payload["arms"]) != {"core_longs", "child_longs", "momentum_reference"}:
        raise ValueError("invalid benchmark arms")
    for arm in payload["arms"].values():
        members = arm["symbols"]
        if (
            not isinstance(members, tuple)
            or len(set(members)) != len(members)
            or not set(members) <= symbols
            or arm["size"] != len(members)
            or type(arm["available"]) is not bool
        ):
            raise ValueError("invalid frozen benchmark arm")
        expected_returns = {s: outcomes[s]["raw_return_pct"] for s in members}
        if canonical_bytes(arm["raw_forward_returns"]) != canonical_bytes(expected_returns) or arm[
            "unknown_outcome_count"
        ] != sum(v is None for v in expected_returns.values()):
            raise ValueError("benchmark arm/outcome mismatch")
        if canonical_bytes(arm["membership"]) != canonical_bytes(
            {s: timeline["members"][s] for s in members}
        ):
            raise ValueError("benchmark arm/timeline mismatch")
    for shorts in (payload["core_shorts"], payload["child_shorts"]):
        if not isinstance(shorts, tuple) or any(
            s["symbol"] not in symbols or s["direction"] != "short" for s in shorts
        ):
            raise ValueError("invalid separated short arm")
    for key in ("child_results", "admission", "journal"):
        if not isinstance(payload[key], tuple):
            raise ValueError("invalid benchmark research evidence")
    if not isinstance(payload["overlap"], dict) or not isinstance(payload["forward_cohort"], dict):
        raise ValueError("invalid benchmark summary")
    if payload["forward_cohort"]["total"] != size:
        raise ValueError("forward cohort denominator mismatch")


class LocalRankArchive:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._store = LocalScanArchive(root)

    def write_snapshot(self, record: RankSnapshotRecord) -> ScanReceipt:
        validate_snapshot(record)
        self._store._write(
            self.root / "ranks" / record.parent_id / record.snapshot_id,
            envelope("rank_snapshot", record),
        )
        return ScanReceipt(record.snapshot_id, "saved")

    def read_snapshot(self, parent_id: str, snapshot_id: str) -> RankSnapshotRecord:
        payload = self._store._read(
            self.root / "ranks" / validated_id(parent_id) / validated_id(snapshot_id)
        )
        try:
            if payload["kind"] != "rank_snapshot":
                raise ValueError("invalid rank kind")
            record = RankSnapshotRecord(**payload["record"])
            validate_snapshot(record)
            if record.parent_id != parent_id or record.snapshot_id != snapshot_id:
                raise ValueError("rank identity mismatch")
            return record
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ArchiveIntegrityError("invalid rank snapshot") from exc

    def snapshots(self, parent_id: str) -> tuple[RankSnapshotRecord, ...]:
        directory = self.root / "ranks" / validated_id(parent_id)
        if not directory.exists():
            return ()
        records = [
            self.read_snapshot(parent_id, validated_id(p.name))
            for p in directory.iterdir()
            if not p.name.startswith(".staging-")
        ]
        return tuple(sorted(records, key=lambda r: (r.arrived_at or r.completed_at, r.snapshot_id)))

    def snapshot_ids(self, parent_id: str) -> tuple[str, ...]:
        """Freeze committed identities without retaining decoded snapshot evidence."""
        directory = self.root / "ranks" / validated_id(parent_id)
        if not directory.exists():
            return ()
        return tuple(
            validated_id(p.name) for p in directory.iterdir() if not p.name.startswith(".staging-")
        )

    def write_report(self, record: BenchmarkRecord) -> ScanReceipt:
        validate_report(record)
        self._store._write(
            self.root / "benchmarks" / record.parent_id / record.report_id,
            envelope("rank_benchmark", record),
        )
        return ScanReceipt(record.report_id, "saved")

    def read_report(self, parent_id: str, report_id: str) -> BenchmarkRecord:
        payload = self._store._read(
            self.root / "benchmarks" / validated_id(parent_id) / validated_id(report_id)
        )
        try:
            if payload["kind"] != "rank_benchmark":
                raise ValueError("invalid benchmark kind")
            record = BenchmarkRecord(**payload["record"])
            validate_report(record)
            if record.parent_id != parent_id or record.report_id != report_id:
                raise ValueError("benchmark identity mismatch")
            return record
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ArchiveIntegrityError("invalid rank benchmark") from exc
