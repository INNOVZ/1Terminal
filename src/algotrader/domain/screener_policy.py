"""Shared publication rules for the public API, CLI and scheduled screener."""
from __future__ import annotations

from decimal import Decimal

from algotrader.domain.crypto_screener_models import CryptoOpportunity
from algotrader.domain.models import Direction
from algotrader.domain.scoring import OpportunityGrade

SCREENING_POLICY_VERSION = "atlas-v2.7-data-contract-1"
COI_OVEREXTENDED_THRESHOLD = 75.0
COI_OVERSOLD_THRESHOLD = 25.0
COI_TIGHTENED_CONVICTION_FLOOR = Decimal("70.0")
COI_STANDARD_CONVICTION_FLOOR = Decimal("65.0")


def publication_rejection_reasons(
    opportunity: CryptoOpportunity, *, coi_score: float | None = None
) -> tuple[str, ...]:
    """Evaluate the existing tiered quality policy without changing its thresholds."""
    reasons: list[str] = []
    score_floor = Decimal("71.0") if opportunity.is_discovery_tier else Decimal("68.0")
    conviction_floor = Decimal("69.0") if opportunity.is_discovery_tier else COI_STANDARD_CONVICTION_FLOOR
    effective_score = opportunity.conviction if opportunity.conviction is not None else opportunity.score
    grade = getattr(opportunity.grade, "value", opportunity.grade)
    if grade == OpportunityGrade.BELOW_THRESHOLD.value:
        reasons.append("grade_below_threshold")
    if opportunity.score < score_floor or effective_score < score_floor:
        reasons.append("score_floor")
    if opportunity.conviction is not None and opportunity.conviction < conviction_floor:
        reasons.append("conviction_floor")
    if opportunity.checklist_verification is not None and opportunity.checklist_verification.passed_count < 3:
        reasons.append("checklist_consensus")
    minimum_rr = Decimal("1.50") if opportunity.direction is Direction.SHORT else Decimal("2.00")
    if opportunity.risk_reward_ratio < minimum_rr:
        reasons.append("reward_risk_floor")
    if opportunity.expected_value_r < Decimal("0.20"):
        reasons.append("model_ev_floor")

    tightened = coi_score is not None and (
        (coi_score >= COI_OVEREXTENDED_THRESHOLD and opportunity.direction is Direction.LONG)
        or (coi_score <= COI_OVERSOLD_THRESHOLD and opportunity.direction is Direction.SHORT)
    )
    regime_floor = COI_TIGHTENED_CONVICTION_FLOOR if tightened else conviction_floor
    if tightened and opportunity.is_discovery_tier:
        regime_floor += Decimal("4.0")
    if effective_score < regime_floor:
        reasons.append("regime_conviction_floor")
    return tuple(reasons)
