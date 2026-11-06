"""Sampled rolling membership, never a reconstruction of exact first entry."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from algotrader.domain.forward_outcomes import outcome_anchor
from algotrader.domain.rank_observations import (
    RankPolicy,
    RankSnapshotRecord,
    normalize_ranks,
    validate_cohort,
)
from algotrader.domain.shadow_observations import ShadowRunRecord


def initial_rank_state(child: ShadowRunRecord, policy: RankPolicy) -> dict[str, Any]:
    validate_cohort(child.cohort, policy)
    calls = [call for call in child.inputs if call.method == "decision_tickers"]
    arrived = child.timing.get("snapshot_arrived_at")
    classified = child.timing.get("classified_at")
    result: dict[str, Any] = {
        "states": {i.id.symbol: "unknown" for i in child.cohort},
        "normalized": None,
        "requested_at": child.timing.get("snapshot_requested_at"),
        "arrived_at": arrived,
        "finished_at": child.timing.get("snapshot_finished_at"),
        "provider_requested_at": child.timing.get("snapshot_provider_requested_at"),
        "provider_finished_at": child.timing.get("snapshot_provider_finished_at"),
        "classified_at": classified,
        "child_completed_at": child.completed_at,
        "reason": "unavailable_final_snapshot",
    }
    if not calls or calls[-1].error or arrived is None or classified is None:
        return result
    if not isinstance(arrived, datetime) or not isinstance(classified, datetime):
        raise ValueError("malformed initial timestamps")
    if (
        arrived.utcoffset() is None
        or classified.utcoffset() is None
        or not child.started_at <= arrived <= classified <= child.completed_at
    ):
        raise ValueError("invalid initial snapshot chronology")
    if not isinstance(calls[-1].result, (list, tuple)):
        raise ValueError("malformed initial raw evidence")
    normalized = normalize_ranks(child.cohort, tuple(calls[-1].result), classified, policy)
    result.update(
        normalized=normalized,
        reason=None if normalized["complete"] else "incomplete_initial_cohort",
    )
    for member in normalized["members"]:
        result["states"][member["symbol"]] = (
            "unknown" if member["rank"] is None else "inside" if member["inside"] else "outside"
        )
    return result 


def membership_timeline(
    child: ShadowRunRecord,
    initial: dict[str, Any],
    snapshots: Iterable[RankSnapshotRecord],
    horizon_hours: int,
    as_of: datetime,
    policy: RankPolicy,
    *,
    ordered: bool = False,
) -> dict[str, Any]:
    if type(horizon_hours) is not int or horizon_hours not in (24, 48) or as_of.utcoffset() is None:
        raise ValueError("timeline requires aware as-of and 24/48h horizon")
    anchor = outcome_anchor(child.completed_at)
    end = anchor + timedelta(hours=horizon_hours)
    accepted_ids: list[str] = []
    stamps: list[datetime] = []
    first_membership: dict[str, Any] = {}
    previous_outside: dict[str, Any] = {}
    total_attempts = 0
    previous_order: tuple[datetime, str] | None = None
    excluded: list[dict[str, Any]] = []
    previous_cutoffs = {
        m["symbol"]: m["cutoff"]
        for m in (initial.get("normalized") or {}).get("members", ())
        if m["cutoff"] is not None
    }
    samples = (
        snapshots
        if ordered
        else sorted(snapshots, key=lambda s: (s.arrived_at or s.completed_at, s.snapshot_id))
    )
    for sample in samples:
        total_attempts += 1
        if (
            sample.parent_id != child.parent_id
            or sample.parent_checksum != child.parent_checksum
            or sample.cohort != child.cohort
        ):
            raise ValueError("rank sample belongs to different parent/cohort")
        stamp = sample.arrived_at or sample.completed_at
        order_key = (stamp, sample.snapshot_id)
        if previous_order is not None and order_key < previous_order:
            raise ValueError("rank stream is not ordered")
        previous_order = order_key
        reason = None
        cutoffs: dict[str, Any] = {}
        if not anchor <= stamp <= min(as_of, end):
            reason = "outside_window"
        elif (
            sample.status != "complete"
            or not sample.normalized.get("complete")
            or sample.arrived_at is None
        ):
            reason = "incomplete"
        else:
            checked = normalize_ranks(child.cohort, sample.rows, sample.arrived_at, policy)
            if not checked["complete"]:
                reason = "incomplete_under_report_policy"
            else:
                cutoffs = {m["symbol"]: m["cutoff"] for m in checked["members"]}
                if any(
                    symbol in previous_cutoffs and cutoff <= previous_cutoffs[symbol]
                    for symbol, cutoff in cutoffs.items()
                ):
                    reason = "nonadvancing_cutoffs"
        if reason:
            excluded.append(
                {
                    "snapshot_id": sample.snapshot_id,
                    "reason": reason,
                    "arrived_at": sample.arrived_at,
                }
            )
            continue
        accepted_ids.append(sample.snapshot_id)
        stamps.append(stamp)
        previous_cutoffs = cutoffs
        # Retain only the first membership and its preceding outside observation,
        # not raw snapshots or a normalized copy for every sample/cohort pair.
        for member in checked["members"]:
            symbol = member["symbol"]
            if symbol in first_membership:
                continue
            observation = {
                "snapshot_id": sample.snapshot_id,
                "arrived_at": sample.arrived_at,
                "cutoff": member["cutoff"],
                "rank": member["rank"],
                "return_sign": member["return_sign"],
            }
            if member["inside"]:
                first_membership[symbol] = {
                    **observation,
                    "lag_from_child_seconds": (stamp - child.completed_at).total_seconds(),
                    "previous_outside": previous_outside.get(symbol),
                }
            else:
                previous_outside[symbol] = observation
    boundaries = [anchor, *stamps, end]
    max_gap = max((b - a).total_seconds() for a, b in zip(boundaries, boundaries[1:], strict=False))
    sufficient = bool(stamps) and as_of >= end and max_gap <= policy.sampling_max_gap_seconds
    members = {}
    for instrument in child.cohort:
        symbol = instrument.id.symbol
        state = initial["states"][symbol]
        first = first_membership.get(symbol)
        status = (
            "already_inside"
            if state == "inside"
            else "unavailable_initial"
            if state == "unknown"
            else "observed_membership"
            if first
            else "not_due"
            if as_of < end
            else "not_observed_in_sufficient_samples"
            if sufficient
            else "insufficient_sampling"
        )
        members[symbol] = {
            "initial_state": state,
            "status": status,
            "first_observed_membership": first if state == "outside" else None,
        }
    return {
        "anchor": anchor,
        "end": end,
        "as_of": as_of,
        "original_cohort_size": len(child.cohort),
        "initial_partitions": {
            state: sum(v == state for v in initial["states"].values())
            for state in ("inside", "outside", "unknown")
        },
        "members": members,
        "sampling": {
            "accepted_count": len(accepted_ids),
            "total_attempts": total_attempts,
            "accepted_ids": tuple(accepted_ids),
            "excluded": tuple(excluded),
            "max_gap_seconds": max_gap,
            "sufficient": sufficient,
            "target_seconds": policy.sampling_target_seconds,
            "interpretation": "sampled_membership_not_exact_entry_or_continuous_absence",
        },
    }
