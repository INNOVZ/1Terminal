"""Bounded enrichment allocation, separate from signal qualification."""
from dataclasses import dataclass

SHORTLIST_LIMIT = 45
DISCOVERY_PROBE_LIMIT = 30
LEGACY_PROBE_LIMIT = 15
ENRICHMENT_CONCURRENCY = 8


@dataclass(frozen=True, slots=True)
class Stage2Capacity:
    budget: int
    probe_limit: int
    rollover_slots: int
    policy: str


def stage2_capacity(
    shortlist_count: int, *, discovery: bool, probes_enabled: bool, rollover_enabled: bool,
) -> Stage2Capacity:
    if not 0 <= shortlist_count <= SHORTLIST_LIMIT:
        raise ValueError("invalid Stage-2 shortlist size")
    base_probes = DISCOVERY_PROBE_LIMIT if discovery else LEGACY_PROBE_LIMIT
    rollover = SHORTLIST_LIMIT - shortlist_count if discovery and probes_enabled and rollover_enabled else 0
    return Stage2Capacity(
        budget=SHORTLIST_LIMIT + base_probes,
        probe_limit=base_probes + rollover if probes_enabled else 0,
        rollover_slots=rollover,
        policy="stage2-spare-capacity-v1" if discovery and rollover_enabled else "stage2-fixed-lanes-v1",
    )
