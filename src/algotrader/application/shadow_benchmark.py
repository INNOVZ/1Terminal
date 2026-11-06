"""Filesystem-only matched-forward-anchor reports with exact frozen inputs."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from algotrader.application.shadow_observer import _measurement_identity
from algotrader.domain.forward_outcomes import ForwardObservation, outcome_anchor, rank_cohort
from algotrader.domain.rank_observations import (
    BenchmarkRecord,
    RankPolicy,
    RankSnapshotRecord,
    finite_number,
    normalize_ranks,
    validate_cohort,
)
from algotrader.domain.rank_timeline import initial_rank_state, membership_timeline
from algotrader.domain.shadow_benchmark import freeze_benchmark_arms, summarize_benchmark
from algotrader.domain.shadow_discovery import classify_shadow
from algotrader.infrastructure.rank_archive import LocalRankArchive, aware, digest, envelope
from algotrader.infrastructure.scan_archive import (
    ArchiveIntegrityError,
    LocalScanArchive,
    canonical_bytes,
    validated_id,
)
from algotrader.infrastructure.shadow_archive import LocalShadowArchive


class ShadowBenchmark:
    def __init__(self, root: Path, policy: RankPolicy) -> None:
        self.root, self.policy = root, policy

    def freeze_inputs(
        self, shadow_id: str, horizon_hours: int, *, as_of: datetime
    ) -> dict[str, Any]:
        """Validate and freeze exact archived evidence without publishing a report."""
        return self._assemble(shadow_id, horizon_hours, as_of=as_of).manifest

    def build(
        self,
        shadow_id: str,
        horizon_hours: int,
        *,
        as_of: datetime,
        report_id: str | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> BenchmarkRecord:
        identifier = validated_id(report_id) if report_id is not None else str(uuid4())
        existing = None
        if report_id is not None:
            child = LocalShadowArchive(self.root).read_run(shadow_id)
            if (self.root / "benchmarks" / child.parent_id / identifier).exists():
                existing = LocalRankArchive(self.root).read_report(child.parent_id, identifier)
                if (
                    existing.shadow_id != shadow_id
                    or existing.as_of != as_of
                    or existing.horizon_hours != horizon_hours
                ):
                    raise ArchiveIntegrityError("reserved report identity mismatch")
                inputs = existing.manifest if inputs is None else inputs
        record = self._assemble(
            shadow_id, horizon_hours, as_of=as_of, report_id=identifier, inputs=inputs
        )
        if existing is not None:
            if canonical_bytes(existing.manifest) != canonical_bytes(
                record.manifest
            ) or canonical_bytes(existing.payload) != canonical_bytes(record.payload):
                raise ArchiveIntegrityError("reserved report differs from frozen inputs")
            return existing
        LocalRankArchive(self.root).write_report(record)
        return record

    def _assemble(
        self,
        shadow_id: str,
        horizon_hours: int,
        *,
        as_of: datetime,
        report_id: str | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> BenchmarkRecord:
        aware(as_of)
        if type(horizon_hours) is not int or horizon_hours not in (24, 48):
            raise ValueError("benchmark horizon must be 24/48h")
        archive = LocalShadowArchive(self.root)
        ranks = LocalRankArchive(self.root)
        child = archive.read_run(shadow_id)
        if as_of < child.completed_at:
            raise ValueError("benchmark as-of precedes child decision completion")
        parent = LocalScanArchive(self.root).read_scan(child.parent_id)
        validate_cohort(child.cohort, self.policy)
        if parent.status != "complete" or child.status != "complete":
            raise ValueError("benchmark requires complete child/parent")
        if child.parent_checksum != digest(parent) or child.cohort != parent.cohort:
            raise ArchiveIntegrityError("benchmark parent/cohort identity mismatch")
        if child.started_at < parent.completed_at or child.completed_at < child.started_at:
            raise ArchiveIntegrityError("invalid child/parent chronology")
        initial = initial_rank_state(child, self.policy)
        snapshot_manifest = []

        def evidence() -> Iterator[RankSnapshotRecord]:
            for sample in self._snapshots(ranks, parent.scan_id, as_of, inputs):
                snapshot_manifest.append(
                    {
                        "snapshot_id": sample.snapshot_id,
                        "checksum": digest(envelope("rank_snapshot", sample)),
                    }
                )
                yield sample

        timeline = membership_timeline(
            child, initial, evidence(), horizon_hours, as_of, self.policy, ordered=True
        )
        anchor = outcome_anchor(child.completed_at)
        measurement = _measurement_identity(
            child.parent_checksum, child.shadow_id, child.completed_at
        )
        selected: dict[str, Any] = {}
        considered: dict[str, Any] = {}
        observations: dict[str, ForwardObservation] = {}
        for inst in child.cohort:
            symbol = inst.id.symbol
            attempts = self._attempts(archive, shadow_id, horizon_hours, symbol, inputs)
            compatible = []
            considered[symbol] = []
            for attempt in attempts:
                try:
                    aware(attempt["observed_at"])
                    if attempt["observed_at"] > as_of:
                        continue
                    considered[symbol].append(
                        {
                            "attempt_id": attempt["attempt_id"],
                            "checksum": digest(attempt),
                            "compatible": attempt.get("measurement_identity") == measurement,
                        }
                    )
                    if attempt.get("measurement_identity") != measurement:
                        continue
                    validated_id(attempt["attempt_id"])
                    if (
                        attempt["shadow_id"] != shadow_id
                        or attempt["symbol"] != symbol
                        or attempt["anchor"] != anchor
                        or attempt["horizon_hours"] != horizon_hours
                    ):
                        raise ValueError("outcome identity mismatch")
                    observation = ForwardObservation(**attempt["observation"])
                    if (
                        observation.anchor != anchor
                        or observation.horizon_hours != horizon_hours
                        or observation.status
                        not in ("complete", "not_due", "missing", "invalid", "unavailable")
                    ):
                        raise ValueError("outcome envelope mismatch")
                    if observation.status == "complete":
                        baseline, end_price, raw = (
                            finite_number(v)
                            for v in (
                                observation.baseline,
                                observation.end_price,
                                observation.raw_return_pct,
                            )
                        )
                        if (
                            baseline is None
                            or end_price is None
                            or raw is None
                            or baseline <= 0
                            or end_price <= 0
                            or raw != (end_price / baseline - 1) * 100
                            or attempt["observed_at"] < anchor + timedelta(hours=horizon_hours)
                        ):
                            raise ValueError("invalid complete price path")
                        compatible.append((attempt, observation))
                except (KeyError, TypeError, ValueError, AttributeError) as exc:
                    raise ArchiveIntegrityError("malformed benchmark outcome") from exc
            if compatible:
                attempt, observation = max(
                    compatible, key=lambda pair: (pair[0]["observed_at"], pair[0]["attempt_id"])
                )
                selected[symbol] = {
                    "attempt_id": attempt["attempt_id"],
                    "checksum": digest(attempt),
                }
                observations[symbol] = observation
            else:
                observations[symbol] = ForwardObservation(
                    "unavailable", anchor, horizon_hours, "no_compatible_complete_child_path"
                )
        arms = freeze_benchmark_arms(parent, child, initial, self.policy)
        payload = summarize_benchmark(
            arms, timeline, {s: obs.as_dict() for s, obs in observations.items()}
        )
        cohort_summary = asdict(rank_cohort(observations))
        if not cohort_summary["full_coverage"]:
            cohort_summary["ranks"] = {}
        payload.update(
            comparison="matched_forward_anchor_staggered_decisions",
            validation_status="uncalibrated_research",
            anchor=anchor,
            horizon_hours=horizon_hours,
            original_cohort_size=len(child.cohort),
            parent_completed_at=parent.completed_at,
            child_completed_at=child.completed_at,
            parent_child_lag_seconds=(child.completed_at - parent.completed_at).total_seconds(),
            information_warning="core_decisions_used_older_information_not_a_causal_comparison",
            initial=initial,
            timeline=timeline,
            outcomes={s: o.as_dict() for s, o in observations.items()},
            forward_cohort=cohort_summary,
            metric_warning="fixed_forward_cohort_ranks_are_not_rolling_24h_membership",
        )
        identity = {
            "source": digest(
                tuple(
                    (str(p.relative_to(Path(__file__).parents[1])), p.read_text())
                    for p in (
                        Path(__file__),
                        Path(freeze_benchmark_arms.__code__.co_filename),
                        Path(membership_timeline.__code__.co_filename),
                        Path(normalize_ranks.__code__.co_filename),
                        Path(classify_shadow.__code__.co_filename),
                    )
                )
            ),
            "measurement_identity": measurement,
            "report_policy": "matched-anchor-benchmark-v1",
        }
        manifest = {
            "parent_id": parent.scan_id,
            "parent_checksum": digest(parent),
            "shadow_id": shadow_id,
            "shadow_checksum": digest(
                {
                    "kind": "shadow_run",
                    "record": {k: getattr(child, k) for k in child.__dataclass_fields__},
                }
            ),
            "rank_snapshots": tuple(snapshot_manifest),
            "selected_outcomes": selected,
            "considered_outcomes": considered,
            "as_of": as_of,
            "horizon_hours": horizon_hours,
            "identity": identity,
            "policy": asdict(self.policy),
        }
        if inputs is not None and canonical_bytes(inputs) != canonical_bytes(manifest):
            raise ArchiveIntegrityError("frozen benchmark manifest does not reconstruct")
        record = BenchmarkRecord(
            report_id or str(uuid4()),
            parent.scan_id,
            shadow_id,
            as_of,
            datetime.now(UTC),
            horizon_hours,
            asdict(self.policy),
            identity,
            manifest,
            payload,
        )
        return record

    @staticmethod
    def _snapshots(
        archive: LocalRankArchive,
        parent_id: str,
        as_of: datetime,
        inputs: dict[str, Any] | None,
    ) -> Iterator[RankSnapshotRecord]:
        try:
            refs = (
                inputs["rank_snapshots"]
                if inputs is not None
                else tuple(
                    {"snapshot_id": identifier} for identifier in archive.snapshot_ids(parent_id)
                )
            )
            if not isinstance(refs, tuple):
                raise ValueError("invalid frozen snapshots")
            index = []
            seen = set()
            for ref in refs:
                identifier = validated_id(ref["snapshot_id"])
                if identifier in seen:
                    raise ValueError("duplicate frozen snapshot")
                seen.add(identifier)
                record = archive.read_snapshot(parent_id, identifier)
                checksum = digest(envelope("rank_snapshot", record))
                stamp = record.arrived_at or record.completed_at
                if inputs is not None and (checksum != ref["checksum"] or stamp > as_of):
                    raise ValueError("frozen snapshot checksum/cutoff mismatch")
                if stamp <= as_of:
                    index.append((stamp, identifier, checksum))
                del record
            # Only timestamps/identities/checksums are sorted. Evidence is decoded,
            # validated and released one item at a time on both manifest paths.
            for _stamp, identifier, checksum in sorted(index):
                record = archive.read_snapshot(parent_id, identifier)
                if digest(envelope("rank_snapshot", record)) != checksum:
                    raise ValueError("snapshot changed during report assembly")
                yield record
                del record
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ArchiveIntegrityError("invalid frozen rank references") from exc

    @staticmethod
    def _attempts(
        archive: LocalShadowArchive,
        shadow_id: str,
        horizon: int,
        symbol: str,
        inputs: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], ...]:
        if inputs is None:
            return archive.outcome_attempts(shadow_id, horizon, symbol)
        try:
            refs = inputs["considered_outcomes"][symbol]
            if not isinstance(refs, list):
                raise ValueError("invalid frozen outcome references")
            attempts = []
            for ref in refs:
                identifier = validated_id(ref["attempt_id"])
                record = archive._store._read(
                    archive._outcome_directory(shadow_id, horizon, symbol) / identifier
                )
                if (
                    not isinstance(record, dict)
                    or record.get("attempt_id") != identifier
                    or digest(record) != ref["checksum"]
                ):
                    raise ValueError("frozen outcome checksum/identity mismatch")
                attempts.append(record)
            if len({record["attempt_id"] for record in attempts}) != len(attempts):
                raise ValueError("duplicate frozen outcome")
            return tuple(
                sorted(attempts, key=lambda record: (record["observed_at"], record["attempt_id"]))
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ArchiveIntegrityError("invalid frozen outcome references") from exc
