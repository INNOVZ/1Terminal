"""Frozen descriptive controls; no live allocation or promotion decisions."""

from __future__ import annotations

from typing import Any

from algotrader.domain.rank_observations import RankPolicy
from algotrader.domain.scan_observations import ScanRecord
from algotrader.domain.shadow_observations import ShadowRunRecord


def freeze_benchmark_arms(
    parent: ScanRecord,
    child: ShadowRunRecord,
    initial: dict[str, Any],
    policy: RankPolicy,
) -> dict[str, Any]:
    symbols = {i.id.symbol for i in child.cohort}
    for item in (*parent.opportunities, *child.results):
        if item.get("symbol") not in symbols or item.get("direction") not in ("long", "short"):
            raise ValueError("malformed candidate identity/direction")
    core = tuple(i["symbol"] for i in parent.opportunities if i["direction"] == "long")
    qualified = tuple(
        i["symbol"]
        for i in child.results
        if i.get("qualified") is True and i["direction"] == "long"
    )
    if len(set(core)) != len(core) or len(set(qualified)) != len(qualified):
        raise ValueError("duplicate frozen candidates")
    normalized = initial.get("normalized")
    available = bool(normalized and normalized["complete"])
    rows = normalized["members"] if normalized else ()
    available = available and all(m["quote_volume"] is not None for m in rows)
    eligible = sorted(
        (
            m
            for m in rows
            if available
            and not m["inside"]
            and m["return_24h"] > 0
            and m["quote_volume"] >= 1500000
        ),
        key=lambda m: (-m["return_24h"], -m["quote_volume"], m["symbol"]),
    )
    return {
        "core_longs": {"symbols": core, "available": True},
        "child_longs": {"symbols": qualified, "available": True},
        "momentum_reference": {
            "symbols": tuple(m["symbol"] for m in eligible[:5]),
            "available": available,
            "reason": None if available else "incomplete_initial_ranks_or_volume",
            "definition": "volume_filtered_positive_rolling_momentum_outside_top10",
        },
        "core_shorts": tuple(i for i in parent.opportunities if i["direction"] == "short"),
        "child_shorts": tuple(
            i for i in child.results if i.get("qualified") is True and i["direction"] == "short"
        ),
        "child_results": child.results,
        "admission": child.admission,
        "journal": child.journal,
    }


def summarize_benchmark(
    arms: dict[str, Any],
    timeline: dict[str, Any],
    outcomes: dict[str, Any],
) -> dict[str, Any]:
    output = {}
    names = ("core_longs", "child_longs", "momentum_reference")
    for name in names:
        arm = arms[name]
        symbols = arm["symbols"]
        returns = {symbol: outcomes[symbol].get("raw_return_pct") for symbol in symbols}
        statuses = {symbol: timeline["members"][symbol] for symbol in symbols}
        counts = {
            status: sum(m["status"] == status for m in statuses.values())
            for status in (
                "observed_membership",
                "already_inside",
                "unavailable_initial",
                "not_due",
                "not_observed_in_sufficient_samples",
                "insufficient_sampling",
            )
        }
        top5 = (
            {
                "status": "available",
                "symbols": symbols[:5],
                "raw_forward_returns": {s: returns[s] for s in symbols[:5]},
            }
            if len(symbols) >= 5
            else {"status": "insufficient_candidates"}
        )
        output[name] = {
            **arm,
            "size": len(symbols),
            "raw_forward_returns": returns,
            "unknown_outcome_count": sum(v is None for v in returns.values()),
            "membership": statuses,
            "membership_counts": counts,
            "top_five": top5,
            "initial_partitions": {
                state: sum(v["initial_state"] == state for v in statuses.values())
                for state in ("inside", "outside", "unknown")
            },
        }
    return {
        "arms": output,
        "overlap": {
            f"{a}__{b}": tuple(s for s in arms[a]["symbols"] if s in arms[b]["symbols"])
            for index, a in enumerate(names)
            for b in names[index + 1 :]
        },
        "core_shorts": arms["core_shorts"],
        "child_shorts": arms["child_shorts"],
        "child_results": arms["child_results"],
        "admission": arms["admission"],
        "journal": arms["journal"],
    }
