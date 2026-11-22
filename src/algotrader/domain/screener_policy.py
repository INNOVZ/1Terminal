"""Shared publication rules for the public API, CLI and scheduled screener."""
from __future__ import annotations

from decimal import Decimal

from algotrader.domain.bearish_continuation import POLICY_VERSION as CONTINUATION_POLICY_VERSION
from algotrader.domain.crypto_screener_models import CryptoOpportunity, SetupLabel
from algotrader.domain.execution_brackets import valid_execution_prices
from algotrader.domain.models import Direction
from algotrader.domain.scoring import OpportunityGrade

SCREENING_POLICY_VERSION = "atlas-v3.1-discovery-guards-1"
COI_OVEREXTENDED_THRESHOLD = 75.0
COI_OVERSOLD_THRESHOLD = 25.0
COI_TIGHTENED_CONVICTION_FLOOR = Decimal("70.0")
COI_STANDARD_CONVICTION_FLOOR = Decimal("65.0")


def publication_rejection_reasons(
    opportunity: CryptoOpportunity,
    *,
    coi_score: float | None = None,
    top_gainer_symbols: set[str] | frozenset[str] | None = None,
) -> tuple[str, ...]:
    """Evaluate the existing tiered quality policy without changing its thresholds."""
    reasons: list[str] = []
    if not valid_execution_prices(
        opportunity.direction, opportunity.suggested_entry,
        opportunity.suggested_stop, opportunity.suggested_targets,
    ):
        return ("invalid_execution_prices",)
    if opportunity.setup_label is SetupLabel.BREAKDOWN_RETEST_SHORT:
        evidence = opportunity.continuation_evidence or {}
        if (evidence.get("policy_version") != CONTINUATION_POLICY_VERSION
                or evidence.get("derivatives_confirmed") is not True
                or len(opportunity.checklist) != 5
                or not all(item[1] is True for item in opportunity.checklist)):
            reasons.append("continuation_requires_five_current_gates")
    if opportunity.setup_label is SetupLabel.RECOVERY_RECLAIM_LONG and (
        len(opportunity.checklist) != 5 or not all(item[1] is True for item in opportunity.checklist)
    ):
        return ("recovery_confirmation_unavailable",)
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
    if opportunity.direction is Direction.LONG:
        if top_gainer_symbols and opportunity.instrument.id.symbol in top_gainer_symbols:
            reasons.append("already_in_top10_gainers_exhaustion_guard")
        if getattr(opportunity, "execution_mode", None) == "WAIT_FOR_RETEST_ONLY":
            reasons.append("wait_for_retest_only_non_actionable")
        oi_7d = getattr(opportunity, "oi_change_7d", None)
        oi_1h = getattr(opportunity, "oi_change_1h", None)
        funding = getattr(opportunity, "funding_rate", None)
        if oi_7d is not None and Decimal(str(oi_7d)) < Decimal("-0.010"):
            if (oi_1h is None or Decimal(str(oi_1h)) <= Decimal("0")) or (
                funding is not None and Decimal(str(funding)) >= Decimal("0.00015")
            ):
                reasons.append("bleeding_7d_oi_without_fresh_inflow")
        taker_4h = getattr(opportunity, "taker_ratio_4h", None)
        if taker_4h is None:
            taker_4h = getattr(opportunity, "taker_buy_ratio_4h", None)
        if taker_4h is not None:
            min_taker = (
                Decimal("1.02")
                if opportunity.setup_label is SetupLabel.MOMENTUM_EMERGING_LONG
                else Decimal("1.05")
            )
            if Decimal(str(taker_4h)) < min_taker:
                reasons.append("insufficient_taker_buyer_absorption")
        if (
            opportunity.is_discovery_tier
            and opportunity.checklist_verification is not None
            and opportunity.checklist_verification.passed_count < 5
        ):
            high_oi_override = (
                oi_7d is not None
                and Decimal(str(oi_7d)) >= Decimal("0.20")
                and (oi_1h is None or Decimal(str(oi_1h)) >= Decimal("-0.005"))
            )
            emerging_override = (
                opportunity.setup_label is SetupLabel.MOMENTUM_EMERGING_LONG
                and opportunity.checklist_verification.passed_count >= 4
                and opportunity.checklist_verification.derivatives_positioning is True
            )
            if not high_oi_override and not emerging_override:
                reasons.append("checklist_consensus_below_5_of_5")
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
