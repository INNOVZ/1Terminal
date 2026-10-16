from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from algotrader.domain.crypto_screener_models import (
    CryptoScreenerScoreBreakdown,
    SetupLabel,
)
from algotrader.domain.models import Direction

W_BASE: dict[SetupLabel | str, Decimal] = {
    SetupLabel.COIL_SPRING_LONG: Decimal("0.58"),
    SetupLabel.OI_ACCUMULATION_LONG: Decimal("0.56"),
    SetupLabel.BREAKOUT_LONG: Decimal("0.52"),
    SetupLabel.MOMENTUM_EMERGING_LONG: Decimal("0.54"),
    SetupLabel.UTAD_DIVERGENCE_SHORT: Decimal("0.57"),
    SetupLabel.EXHAUSTION_REVERSAL_SHORT: Decimal("0.53"),
    SetupLabel.DISTRIBUTION_SHORT: Decimal("0.55"),
}


class OpportunityGrade(StrEnum):
    GRADE_A = "Grade A"
    GRADE_B = "Grade B"
    BELOW_THRESHOLD = "Below Threshold"
    SUB_GRADE = "Below Threshold"


@dataclass(frozen=True, slots=True)
class OpportunityValuation:
    p_win: Decimal
    conviction: Decimal
    ev_r: Decimal
    grade: OpportunityGrade
    asymmetric_rr: Decimal
    score_breakdown: CryptoScreenerScoreBreakdown


def clip(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return max(lower, min(upper, value))


def calculate_adx_subscore(
    archetype: SetupLabel | str,
    adx_4h: Decimal,
    adx_4h_prev: Decimal,
    dmi_plus: Decimal,
    dmi_minus: Decimal,
) -> Decimal:
    """Regime-adaptive ADX subscore (0 to 100).

    For pre-breakout accumulation (COIL_SPRING_LONG, OI_ACCUMULATION_LONG):
      adx_subscore = clip(100.0 - 4.0 * max(0.0, adx_4h - 14.0), 0.0, 100.0) if dmi_plus >= dmi_minus * 0.90 else 25.0
    For trend expansion:
      adx_subscore = clip(4.0 * (adx_4h - 15.0) + (20.0 if adx_4h > adx_4h_prev else 0.0), 0.0, 100.0)
    """
    is_accumulation = archetype in (
        SetupLabel.COIL_SPRING_LONG,
        SetupLabel.OI_ACCUMULATION_LONG,
        "COIL_SPRING_LONG",
        "OI_ACCUMULATION_LONG",
    )
    if is_accumulation:
        if dmi_plus >= dmi_minus * Decimal("0.90"):
            raw = Decimal("100.0") - Decimal("4.0") * max(Decimal("0.0"), adx_4h - Decimal("14.0"))
            return clip(raw, Decimal("0.0"), Decimal("100.0"))
        return Decimal("25.0")

    bonus = Decimal("20.0") if adx_4h > adx_4h_prev else Decimal("0.0")
    raw = Decimal("4.0") * (adx_4h - Decimal("15.0")) + bonus
    return clip(raw, Decimal("0.0"), Decimal("100.0"))


def compute_p_win_and_conviction(
    *,
    direction: Direction,
    archetype: SetupLabel | str,
    quantitative_score: Decimal,
    coi: Decimal = Decimal("50.0"),
) -> tuple[Decimal, Decimal]:
    """Compute empirical win rate p_win (0.30 to 0.78) and orthogonal composite conviction.

    gamma = -0.12 if direction == "LONG" else +0.14
    p_win = clip(W_base[archetype] + gamma * ((coi - 50.0) / 50.0) + 0.08 * ((score - 70.0) / 30.0), 0.30, 0.78)
    conviction = 0.40 * (p_win * 100.0) + 0.60 * score
    """
    w_base = W_BASE.get(archetype, Decimal("0.54"))
    gamma = Decimal("-0.12") if direction is Direction.LONG else Decimal("0.14")

    coi_delta = (coi - Decimal("50.0")) / Decimal("50.0")
    score_delta = (quantitative_score - Decimal("70.0")) / Decimal("30.0")

    p_win_raw = w_base + gamma * coi_delta + Decimal("0.08") * score_delta
    p_win = clip(p_win_raw, Decimal("0.30"), Decimal("0.78")).quantize(Decimal("0.001"))

    conviction = (
        Decimal("0.40") * (p_win * Decimal("100.0")) + Decimal("0.60") * quantitative_score
    ).quantize(Decimal("0.1"))

    return p_win, conviction


def compute_3tier_ev_r(
    *,
    p_win: Decimal,
    r_t1: Decimal,
    r_t2: Decimal,
    r_t3: Decimal,
) -> Decimal:
    """3-Tier Conditional Mathematical Expectancy (EV_R).

    Position weights: w1 = 0.50 (T1), w2 = 0.30 (T2), w3 = 0.20 (T3).
    Conditional transitions: p_t1 = p_win, p_t2_given_t1 = 0.58, p_t3_given_t2 = 0.42.
    ev_r = (p_t1 * w1 * r_t1) + (p_t1 * p_t2_given_t1 * w2 * r_t2) + (p_t1 * p_t2_given_t1 * p_t3_given_t2 * w3 * r_t3) - ((1.0 - p_t1) * 1.0)
    """
    w1 = Decimal("0.50")
    w2 = Decimal("0.30")
    w3 = Decimal("0.20")

    p_t1 = p_win
    p_t2_given_t1 = Decimal("0.58")
    p_t3_given_t2 = Decimal("0.42")

    term1 = p_t1 * w1 * r_t1
    term2 = p_t1 * p_t2_given_t1 * w2 * r_t2
    term3 = p_t1 * p_t2_given_t1 * p_t3_given_t2 * w3 * r_t3
    loss_term = (Decimal("1.0") - p_t1) * Decimal("1.0")

    ev_r = (term1 + term2 + term3 - loss_term).quantize(Decimal("0.01"))
    return ev_r


def classify_opportunity_grade(
    *,
    ev_r: Decimal,
    conviction: Decimal,
    asymmetric_rr: Decimal,
) -> OpportunityGrade:
    """Grade A: ev_r >= 1.50 and conviction >= 75.0 and asymmetric_rr >= 2.50.

    Grade B: 1.10 <= ev_r < 1.50 and conviction >= 70.0 (or ev_r >= 1.50 with conviction >= 70.0).
    """
    if ev_r >= Decimal("1.50") and conviction >= Decimal("75.0") and asymmetric_rr >= Decimal("2.50"):
        return OpportunityGrade.GRADE_A
    if ev_r >= Decimal("1.10") and conviction >= Decimal("70.0"):
        return OpportunityGrade.GRADE_B
    return OpportunityGrade.SUB_GRADE
