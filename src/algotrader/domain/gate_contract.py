"""Canonical cross-layer archetype & checklist gate contract.

Single source of truth shared across:
- `radar_lifecycle.py` (multi-hour 5/5 retention re-verification)
- `auto_watchlist.py` (soft order-flow Watchlist demotion vs. structural hard invalidation)
- `tests/test_cross_layer_lifecycle_parity.py` (CI parity guard preventing split-brain drift)
"""
from __future__ import annotations

from algotrader.domain.bearish_continuation import (
    TITLES as CONTINUATION_GATE_TITLES,
)
from algotrader.domain.early_recovery import (
    FLOW_TITLE as RECOVERY_FLOW_TITLE,
)
from algotrader.domain.early_recovery import (
    STRUCTURAL_TITLES as RECOVERY_STRUCTURAL_TITLES,
)

# Short archetypes that intentionally originate above 30d VAH / POC and 4H EMA50
# during parabolic overbought blow-offs or Wyckoff Phase C Upthrusts:
CLIMAX_SHORT_ARCHETYPES: frozenset[str] = frozenset(
    {
        "EXHAUSTION_REVERSAL_SHORT",
        "UTAD_DIVERGENCE_SHORT",
    }
)

# Short archetypes anchored to 4H EMA50 resistance in `archetypes.py:414-423`
# even when 30d POC sits lower (`poc_30d < close <= ema50_4h`):
EMA50_ANCHORED_SHORT_ARCHETYPES: frozenset[str] = frozenset(
    {
        "VCP_DISTRIBUTION_SHORT",
    }
)

# Long archetypes permitted to base below 30d POC while holding 4H EMA50 / VAL / entry shelf:
BELOW_POC_LONG_ARCHETYPES: frozenset[str] = frozenset(
    {
        "MOMENTUM_EMERGING_LONG",
        "RECOVERY_RECLAIM_LONG",
        "RANGE_BOTTOM_ACCUMULATION_LONG",
        "SHORT_SQUEEZE_ABSORPTION_LONG",
    }
)

# Long archetypes permitted at T=0 (`screening_gates.py:224` & `kinetic_readiness.py`) to
# originate below 4H EMA50 when reclaiming local base / 4H EMA20 above invalidation stop:
EMERGING_SUB_EMA50_LONG_ARCHETYPES: frozenset[str] = frozenset(
    {
        "MOMENTUM_EMERGING_LONG",
        "SHORT_SQUEEZE_ABSORPTION_LONG",
    }
)

# Long archetypes permitted at T=0 (`screening_gates.py` / `kinetic_readiness.py`) to originate
# from a compressed base below 4H EMA50 while holding 4H/1H EMA20 & base stop shelf:
BELOW_EMA50_BASE_LONG_ARCHETYPES: frozenset[str] = frozenset(
    {
        "MOMENTUM_EMERGING_LONG",
        "SHORT_SQUEEZE_ABSORPTION_LONG",
        "FUNDING_SQUEEZE_SPRING_LONG",
    }
)

# Canonical soft derivatives / order-flow gate titles across T=0 Screener & T+1h Lifecycle:
# - Long Accumulation & T+1h Retention: "Smart Money Absorption"
# - T=0 Distribution / Climax Shorts (`crypto_screener.py:3024`): "Trapped Longs / Funding"
# - T=0 Breakout Screener (`crypto_screener.py:3675`): "Derivatives Flow"
# - Early Recovery Family (`early_recovery.py`): "Positioning Confirmation"
# - Bearish Continuation Short (`bearish_continuation.py`): CONTINUATION_GATE_TITLES[4]
CONTINUATION_FLOW_TITLE: str = CONTINUATION_GATE_TITLES[4]
CONTINUATION_STRUCTURAL_TITLES: frozenset[str] = frozenset(CONTINUATION_GATE_TITLES[:4])

CANONICAL_SOFT_FLOW_TITLES: frozenset[str] = frozenset(
    {
        "Smart Money Absorption",
        "Trapped Longs / Funding",
        "Derivatives Flow",
    }
)


def is_soft_only_checklist_failure(
    failed_titles: set[str] | frozenset[str],
    *,
    setup_label: str = "",
) -> bool:
    """Return True iff failed_titles consists solely of the family's soft order-flow/derivatives gate."""
    if not failed_titles or len(failed_titles) != 1:
        return False
    lbl = setup_label.upper().strip()
    if lbl == "RECOVERY_RECLAIM_LONG":
        return failed_titles == {RECOVERY_FLOW_TITLE} and not (
            failed_titles & RECOVERY_STRUCTURAL_TITLES
        )
    if lbl == "BREAKDOWN_RETEST_SHORT":
        return failed_titles == {CONTINUATION_FLOW_TITLE} and not (
            failed_titles & CONTINUATION_STRUCTURAL_TITLES
        )
    only_title = next(iter(failed_titles))
    return only_title in CANONICAL_SOFT_FLOW_TITLES

