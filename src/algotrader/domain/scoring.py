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
    SetupLabel.SHORT_SQUEEZE_ABSORPTION_LONG: Decimal("0.60"),
    SetupLabel.COIL_SPRING_LONG: Decimal("0.58"),
    SetupLabel.VCP_COMPRESSION_LONG: Decimal("0.57"),
    SetupLabel.OI_ACCUMULATION_LONG: Decimal("0.56"),
    SetupLabel.RANGE_BOTTOM_ACCUMULATION_LONG: Decimal("0.55"),
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


@dataclass(frozen=True, slots=True)
class LongContinuousFactorScores:
    """Continuous 5-Factor Institutional Factor Scores (each 0.0 to 100.0)."""

    volatility_score: Decimal  # Pillar 1: Squeeze intensity & bandwidth contraction
    value_score: Decimal  # Pillar 2: 30D Volume Profile Value Acceptance & Base Support
    absorption_score: Decimal  # Pillar 3: Smart Money Absorption, CVD trend & OI positioning
    trend_momentum_score: Decimal  # Pillar 4: Multi-timeframe trend alignment, MACD velocity & ADX
    asymmetry_score: Decimal  # Pillar 5: Structural Risk-to-Reward & Payoff Asymmetry
    raw_score: Decimal  # Weighted combination
    macro_multiplier: Decimal  # Sentiment & macro market multiplier
    final_composite_score: Decimal  # Final scaled score (0.0 to 100.0)


@dataclass(frozen=True, slots=True)
class ShortContinuousFactorScores:
    """Continuous 5-Factor Institutional Short Factor Scores (each 0.0 to 100.0)."""

    volatility_score: Decimal  # Pillar 1: Breakdown compression & volatility squeeze
    value_rejection_score: Decimal  # Pillar 2: 30D VPVR Value Rejection & POC Breakdown
    distribution_score: Decimal  # Pillar 3: Smart Money Selling, Trapped Longs, Taker Sell Flow
    trend_breakdown_score: Decimal  # Pillar 4: Multi-timeframe trend breakdown & MACD deceleration
    asymmetry_score: Decimal  # Pillar 5: Structural Risk-to-Reward for Short Setup
    raw_score: Decimal  # Weighted combination
    macro_multiplier: Decimal  # Sentiment & macro market multiplier
    final_composite_score: Decimal  # Final scaled score (0.0 to 100.0)


def clip(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return max(lower, min(upper, value))


def calculate_continuous_long_factor_scores(
    *,
    bandwidth_4h: Decimal,
    bb_inside_kc: bool,
    vol_contraction_ratio: Decimal,
    close: Decimal,
    poc_30d: Decimal,
    val_30d: Decimal,
    vah_30d: Decimal,
    pos_in_range_30d: Decimal,
    ema50_4h: Decimal,
    ema200_4h: Decimal,
    resistance_absorption_score: Decimal,
    cvd_zscore: Decimal,
    oi_change_7d: Decimal | None,
    oi_change_4h: Decimal | None,
    taker_buy_ratio_4h: Decimal,
    funding_rate: Decimal | None,
    ema20_4h: Decimal,
    macd_hist_4h: Decimal,
    macd_hist_4h_prev: Decimal,
    adx_subscore: Decimal,
    btc_residual_alpha: Decimal,
    rvol_4h: Decimal,
    asymmetric_rr: Decimal,
    dist_to_trigger: Decimal,
    macro_multiplier: Decimal,
    is_btc_cascade: bool = False,
    dist_to_entry: Decimal = Decimal("0.0"),
) -> LongContinuousFactorScores:
    """Continuous Multi-Factor Quant Scoring Engine across the 5 Institutional Pillars.

    Eliminates brittle cliff-edge cutoffs by scoring each pillar smoothly from 0.0 to 100.0.
    """
    # 1. Volatility Regime & Compression (0 to 100)
    if bandwidth_4h <= Decimal("0.10"):
        base_vol = Decimal("100.0")
    elif bandwidth_4h <= Decimal("0.22"):
        ratio = (bandwidth_4h - Decimal("0.10")) / Decimal("0.12")
        base_vol = Decimal("100.0") - ratio * Decimal("45.0")
    elif bandwidth_4h <= Decimal("0.35"):
        ratio = (bandwidth_4h - Decimal("0.22")) / Decimal("0.13")
        base_vol = Decimal("55.0") - ratio * Decimal("40.0")
    else:
        base_vol = Decimal("15.0")

    kc_bonus = Decimal("15.0") if bb_inside_kc else Decimal("0.0")
    vcr_bonus = (
        Decimal("10.0")
        if vol_contraction_ratio <= Decimal("0.90")
        else (Decimal("5.0") if vol_contraction_ratio <= Decimal("1.10") else Decimal("0.0"))
    )
    volatility_score = clip(base_vol + kc_bonus + vcr_bonus, Decimal("0.0"), Decimal("100.0"))

    # 2. Wyckoff Value Acceptance & Base Support (0 to 100)
    poc_denom = max(poc_30d, Decimal("1e-8"))
    val_denom = max(val_30d, Decimal("1e-8"))
    if close >= poc_30d * Decimal("0.99"):
        dist_poc = (close - poc_30d) / poc_denom
        base_val = Decimal("75.0") + min(Decimal("25.0"), dist_poc * Decimal("100.0"))
    elif close >= val_30d * Decimal("0.98"):
        dist_val = (close - val_30d) / val_denom
        base_val = Decimal("65.0") + min(Decimal("20.0"), dist_val * Decimal("100.0"))
    else:
        base_val = Decimal("45.0")

    stage2_bonus = (
        Decimal("15.0") if (close >= ema50_4h and ema50_4h >= ema200_4h * Decimal("0.98")) else Decimal("0.0")
    )
    value_score = clip(base_val + stage2_bonus, Decimal("0.0"), Decimal("100.0"))

    # 3. Smart Money & Orderflow Absorption (0 to 100)
    base_abs = resistance_absorption_score
    oi_7d_pts = Decimal("8.0") if (oi_change_7d is not None and oi_change_7d >= Decimal("0.05")) else Decimal("0.0")
    taker_pts = Decimal("8.0") if taker_buy_ratio_4h >= Decimal("0.52") else Decimal("0.0")
    funding_pts = Decimal("5.0") if (funding_rate is not None and funding_rate <= Decimal("0.00005")) else Decimal("0.0")
    absorption_score = clip(base_abs + oi_7d_pts + taker_pts + funding_pts, Decimal("0.0"), Decimal("100.0"))

    # 4. Trend Alignment & Momentum Expansion (0 to 100)
    if close >= ema20_4h >= ema50_4h:
        ema_score = Decimal("85.0")
    elif close >= ema50_4h:
        ema_score = Decimal("70.0")
    else:
        ema_score = Decimal("50.0")

    macd_bonus = Decimal("15.0") if macd_hist_4h > macd_hist_4h_prev else Decimal("0.0")
    alpha_bonus = min(Decimal("15.0"), max(Decimal("0.0"), btc_residual_alpha * Decimal("300.0")))
    rvol_bonus = Decimal("10.0") if rvol_4h >= Decimal("1.25") else Decimal("0.0")
    trend_raw = ema_score + macd_bonus + alpha_bonus + rvol_bonus
    trend_momentum_score = clip(
        Decimal("0.30") * adx_subscore + Decimal("0.70") * trend_raw,
        Decimal("0.0"),
        Decimal("100.0"),
    )

    # 5. Payoff Asymmetry & Structural Risk-Reward (0 to 100)
    if asymmetric_rr >= Decimal("3.5"):
        rr_score = Decimal("100.0")
    elif asymmetric_rr >= Decimal("2.5"):
        rr_score = Decimal("75.0") + (asymmetric_rr - Decimal("2.5")) * Decimal("25.0")
    elif asymmetric_rr >= Decimal("1.5"):
        rr_score = Decimal("50.0") + (asymmetric_rr - Decimal("1.5")) * Decimal("25.0")
    else:
        rr_score = max(Decimal("10.0"), asymmetric_rr * Decimal("30.0"))

    trigger_pts = (
        Decimal("15.0")
        if (Decimal("0.0") <= dist_to_trigger <= Decimal("0.05"))
        else (Decimal("8.0") if dist_to_trigger <= Decimal("0.10") else Decimal("0.0"))
    )
    raw_asymmetry = clip(rr_score + trigger_pts, Decimal("0.0"), Decimal("100.0"))

    # Execution Proximity Discount:
    # If market price is disconnected from the entry level (> 4.5% away), apply a proportional penalty
    if dist_to_entry > Decimal("0.045"):
        excess_dist = dist_to_entry - Decimal("0.045")
        proximity_penalty = min(Decimal("55.0"), excess_dist * Decimal("350.0"))
        asymmetry_score = max(Decimal("15.0"), raw_asymmetry - proximity_penalty)
    else:
        asymmetry_score = raw_asymmetry

    # Multi-System Confluence: Best candidates require broad alignment across all 5 systems,
    # penalizing tokens that only spike on 1 or 2 isolated dimensions.
    pillars = (
        volatility_score,
        value_score,
        absorption_score,
        trend_momentum_score,
        asymmetry_score,
    )
    qualified_pillars = sum(1 for p in pillars if p >= Decimal("50.0"))
    min_pillar = min(pillars)

    if qualified_pillars == 5:
        breadth_mult = Decimal("1.08")
    elif qualified_pillars == 4:
        breadth_mult = Decimal("1.03")
    elif qualified_pillars == 3:
        breadth_mult = Decimal("0.98")
    elif qualified_pillars == 2:
        breadth_mult = Decimal("0.85")
    else:
        breadth_mult = Decimal("0.70")

    if min_pillar < Decimal("35.0"):
        deficiency_mult = Decimal("0.85") + (min_pillar / Decimal("35.0")) * Decimal("0.15")
    else:
        deficiency_mult = Decimal("1.00")

    confluence_mult = breadth_mult * deficiency_mult

    # Weighted Combination across 5 Pillars
    raw_score = (
        (
            Decimal("0.25") * volatility_score
            + Decimal("0.20") * value_score
            + Decimal("0.20") * absorption_score
            + Decimal("0.20") * trend_momentum_score
            + Decimal("0.15") * asymmetry_score
        )
        * confluence_mult
    ).quantize(Decimal("0.1"))

    mult = macro_multiplier
    if is_btc_cascade:
        mult *= Decimal("0.60")

    final_composite_score = clip(
        (raw_score * mult).quantize(Decimal("0.1")),
        Decimal("0.0"),
        Decimal("100.0"),
    )

    return LongContinuousFactorScores(
        volatility_score=volatility_score.quantize(Decimal("0.1")),
        value_score=value_score.quantize(Decimal("0.1")),
        absorption_score=absorption_score.quantize(Decimal("0.1")),
        trend_momentum_score=trend_momentum_score.quantize(Decimal("0.1")),
        asymmetry_score=asymmetry_score.quantize(Decimal("0.1")),
        raw_score=raw_score,
        macro_multiplier=mult.quantize(Decimal("0.01")),
        final_composite_score=final_composite_score,
    )


def calculate_continuous_short_factor_scores(
    *,
    bandwidth_4h: Decimal,
    bb_inside_kc: bool,
    vol_contraction_ratio: Decimal,
    close: Decimal,
    poc_30d: Decimal,
    val_30d: Decimal,
    vah_30d: Decimal,
    pos_in_range_30d: Decimal,
    ema50_4h: Decimal,
    ema200_4h: Decimal,
    has_bearish_div: bool,
    cvd_zscore: Decimal,
    oi_change_7d: Decimal | None,
    oi_change_4h: Decimal | None,
    taker_buy_ratio_4h: Decimal,
    funding_rate: Decimal | None,
    ema20_4h: Decimal,
    macd_hist_4h: Decimal,
    macd_hist_4h_prev: Decimal,
    adx_subscore: Decimal,
    btc_residual_alpha: Decimal,
    rvol_4h: Decimal,
    asymmetric_rr: Decimal,
    dist_to_trigger: Decimal,
    macro_multiplier: Decimal,
    is_btc_cascade: bool = False,
    upper_wick_ratio_4h: Decimal = Decimal("0.0"),
    dist_to_entry: Decimal = Decimal("0.0"),
) -> ShortContinuousFactorScores:
    """Continuous Multi-Factor Quant Scoring Engine across the 5 Institutional Short Pillars.

    Eliminates brittle cliff-edge cutoffs by scoring each short pillar smoothly from 0.0 to 100.0.
    """
    # 1. Breakdown Compression & Volatility (0 to 100)
    if bandwidth_4h <= Decimal("0.10"):
        base_vol = Decimal("100.0")
    elif bandwidth_4h <= Decimal("0.22"):
        ratio = (bandwidth_4h - Decimal("0.10")) / Decimal("0.12")
        base_vol = Decimal("100.0") - ratio * Decimal("45.0")
    elif bandwidth_4h <= Decimal("0.35"):
        ratio = (bandwidth_4h - Decimal("0.22")) / Decimal("0.13")
        base_vol = Decimal("55.0") - ratio * Decimal("40.0")
    else:
        base_vol = Decimal("25.0")

    kc_bonus = Decimal("15.0") if bb_inside_kc else Decimal("0.0")
    vcr_bonus = (
        Decimal("10.0")
        if (vol_contraction_ratio <= Decimal("0.90") or rvol_4h >= Decimal("1.25"))
        else (Decimal("5.0") if vol_contraction_ratio <= Decimal("1.10") else Decimal("0.0"))
    )
    volatility_score = clip(base_vol + kc_bonus + vcr_bonus, Decimal("0.0"), Decimal("100.0"))

    # 2. Value Rejection & POC/VAL Breakdown (0 to 100)
    poc_denom = max(poc_30d, Decimal("1e-8"))
    val_denom = max(val_30d, Decimal("1e-8"))
    if close <= val_30d * Decimal("1.01"):
        dist_val = (val_30d - close) / val_denom
        base_val = Decimal("80.0") + min(Decimal("20.0"), dist_val * Decimal("100.0"))
    elif close <= poc_30d * Decimal("1.01"):
        dist_poc = (poc_30d - close) / poc_denom
        base_val = Decimal("70.0") + min(Decimal("20.0"), dist_poc * Decimal("100.0"))
    elif close >= vah_30d * Decimal("0.98") and (has_bearish_div or upper_wick_ratio_4h >= Decimal("0.35")):
        # UTAD top distribution rejection from range highs
        base_val = Decimal("85.0")
    else:
        base_val = Decimal("45.0")

    stage4_bonus = (
        Decimal("15.0") if (close <= ema50_4h and ema50_4h <= ema200_4h * Decimal("1.02")) else Decimal("0.0")
    )
    value_rejection_score = clip(base_val + stage4_bonus, Decimal("0.0"), Decimal("100.0"))

    # 3. Smart Money Distribution & Trapped Longs (0 to 100)
    base_dist = Decimal("50.0")
    # Taker sell flow (taker buy ratio < 0.50 means sellers dominate)
    if taker_buy_ratio_4h <= Decimal("0.47"):
        base_dist += Decimal("20.0")
    elif taker_buy_ratio_4h <= Decimal("0.50"):
        base_dist += Decimal("10.0")

    # CVD Selling Pressure
    if cvd_zscore <= Decimal("-0.50"):
        base_dist += Decimal("15.0")
    elif cvd_zscore <= Decimal("0.0"):
        base_dist += Decimal("8.0")

    # Trapped Longs / Funding Squeeze Fuel (Positive funding during dump = trapped longs paying shorts)
    if funding_rate is not None and funding_rate >= Decimal("0.00008"):
        base_dist += Decimal("15.0")
    elif funding_rate is not None and funding_rate >= Decimal("0.0"):
        base_dist += Decimal("8.0")

    wick_pts = min(Decimal("15.0"), upper_wick_ratio_4h * Decimal("30.0"))
    div_pts = Decimal("15.0") if has_bearish_div else Decimal("0.0")
    distribution_score = clip(base_dist + wick_pts + div_pts, Decimal("0.0"), Decimal("100.0"))

    # 4. Trend Breakdown & Momentum Deceleration (0 to 100)
    if close <= ema20_4h <= ema50_4h:
        ema_score = Decimal("85.0")
    elif close <= ema50_4h:
        ema_score = Decimal("70.0")
    else:
        ema_score = Decimal("50.0")

    macd_bonus = (
        Decimal("15.0")
        if (macd_hist_4h < macd_hist_4h_prev or macd_hist_4h < Decimal("0.0"))
        else Decimal("0.0")
    )
    # Relative weakness vs BTC (negative residual alpha means underperforming market)
    alpha_bonus = min(
        Decimal("15.0"),
        max(Decimal("0.0"), -btc_residual_alpha * Decimal("300.0")),
    )
    rvol_bonus = Decimal("10.0") if rvol_4h >= Decimal("1.25") else Decimal("0.0")
    trend_raw = ema_score + macd_bonus + alpha_bonus + rvol_bonus
    trend_breakdown_score = clip(
        Decimal("0.30") * adx_subscore + Decimal("0.70") * trend_raw,
        Decimal("0.0"),
        Decimal("100.0"),
    )

    # 5. Payoff Asymmetry & Structural Risk-Reward for Short (0 to 100)
    if asymmetric_rr >= Decimal("3.5"):
        rr_score = Decimal("100.0")
    elif asymmetric_rr >= Decimal("2.5"):
        rr_score = Decimal("75.0") + (asymmetric_rr - Decimal("2.5")) * Decimal("25.0")
    elif asymmetric_rr >= Decimal("1.5"):
        rr_score = Decimal("50.0") + (asymmetric_rr - Decimal("1.5")) * Decimal("25.0")
    else:
        rr_score = max(Decimal("10.0"), asymmetric_rr * Decimal("30.0"))

    trigger_pts = (
        Decimal("15.0")
        if (Decimal("0.0") <= dist_to_trigger <= Decimal("0.05"))
        else (Decimal("8.0") if dist_to_trigger <= Decimal("0.10") else Decimal("0.0"))
    )
    raw_asymmetry = clip(rr_score + trigger_pts, Decimal("0.0"), Decimal("100.0"))

    # Execution Proximity Discount:
    # If market price is disconnected from the entry level (> 4.5% away), apply a proportional penalty
    if dist_to_entry > Decimal("0.045"):
        excess_dist = dist_to_entry - Decimal("0.045")
        proximity_penalty = min(Decimal("55.0"), excess_dist * Decimal("350.0"))
        asymmetry_score = max(Decimal("15.0"), raw_asymmetry - proximity_penalty)
    else:
        asymmetry_score = raw_asymmetry

    # Multi-System Confluence: Best candidates require broad alignment across all 5 systems,
    # penalizing tokens that only spike on 1 or 2 isolated dimensions.
    pillars = (
        volatility_score,
        value_rejection_score,
        distribution_score,
        trend_breakdown_score,
        asymmetry_score,
    )
    qualified_pillars = sum(1 for p in pillars if p >= Decimal("50.0"))
    min_pillar = min(pillars)

    if qualified_pillars == 5:
        breadth_mult = Decimal("1.08")
    elif qualified_pillars == 4:
        breadth_mult = Decimal("1.03")
    elif qualified_pillars == 3:
        breadth_mult = Decimal("0.98")
    elif qualified_pillars == 2:
        breadth_mult = Decimal("0.85")
    else:
        breadth_mult = Decimal("0.70")

    if min_pillar < Decimal("35.0"):
        deficiency_mult = Decimal("0.85") + (min_pillar / Decimal("35.0")) * Decimal("0.15")
    else:
        deficiency_mult = Decimal("1.00")

    confluence_mult = breadth_mult * deficiency_mult

    # Weighted Combination across 5 Pillars
    raw_score = (
        (
            Decimal("0.25") * volatility_score
            + Decimal("0.20") * value_rejection_score
            + Decimal("0.20") * distribution_score
            + Decimal("0.20") * trend_breakdown_score
            + Decimal("0.15") * asymmetry_score
        )
        * confluence_mult
    ).quantize(Decimal("0.1"))

    mult = macro_multiplier
    if is_btc_cascade:
        # BTC cascade accelerates short breakdown momentum
        mult = min(Decimal("1.35"), mult * Decimal("1.25"))

    final_composite_score = clip(
        (raw_score * mult).quantize(Decimal("0.1")),
        Decimal("0.0"),
        Decimal("100.0"),
    )

    return ShortContinuousFactorScores(
        volatility_score=volatility_score.quantize(Decimal("0.1")),
        value_rejection_score=value_rejection_score.quantize(Decimal("0.1")),
        distribution_score=distribution_score.quantize(Decimal("0.1")),
        trend_breakdown_score=trend_breakdown_score.quantize(Decimal("0.1")),
        asymmetry_score=asymmetry_score.quantize(Decimal("0.1")),
        raw_score=raw_score,
        macro_multiplier=mult.quantize(Decimal("0.01")),
        final_composite_score=final_composite_score,
    )


def calculate_adx_subscore(
    archetype: SetupLabel | str,
    adx_4h: Decimal,
    adx_4h_prev: Decimal,
    dmi_plus: Decimal,
    dmi_minus: Decimal,
) -> Decimal:
    """Regime-adaptive ADX subscore (0 to 100).

    For pre-breakout accumulation (COIL_SPRING_LONG, OI_ACCUMULATION_LONG, VCP_COMPRESSION_LONG, RANGE_BOTTOM_ACCUMULATION_LONG):
      adx_subscore = clip(100.0 - 4.0 * max(0.0, adx_4h - 14.0), 0.0, 100.0) if dmi_plus >= dmi_minus * 0.90 else 25.0
    For trend expansion:
      adx_subscore = clip(4.0 * (adx_4h - 15.0) + (20.0 if adx_4h > adx_4h_prev else 0.0), 0.0, 100.0)
    """
    is_accumulation = archetype in (
        SetupLabel.SHORT_SQUEEZE_ABSORPTION_LONG,
        SetupLabel.COIL_SPRING_LONG,
        SetupLabel.OI_ACCUMULATION_LONG,
        SetupLabel.VCP_COMPRESSION_LONG,
        SetupLabel.RANGE_BOTTOM_ACCUMULATION_LONG,
        "SHORT_SQUEEZE_ABSORPTION_LONG",
        "COIL_SPRING_LONG",
        "OI_ACCUMULATION_LONG",
        "VCP_COMPRESSION_LONG",
        "RANGE_BOTTOM_ACCUMULATION_LONG",
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
    checklist_passed: int | None = None,
) -> tuple[Decimal, Decimal]:
    """Compute empirical win rate p_win (0.30 to 0.78) and orthogonal composite conviction.

    gamma = -0.12 if direction == "LONG" else +0.14
    p_win = clip(W_base[archetype] + gamma * ((coi - 50.0) / 50.0) + 0.08 * ((score - 70.0) / 30.0), 0.30, 0.78)
    conviction = 0.40 * (p_win * 100.0) + 0.60 * score + checklist_adjustment
    """
    w_base = W_BASE.get(archetype, Decimal("0.54"))
    gamma = Decimal("-0.12") if direction is Direction.LONG else Decimal("0.14")

    coi_delta = (coi - Decimal("50.0")) / Decimal("50.0")
    score_delta = (quantitative_score - Decimal("70.0")) / Decimal("30.0")

    p_win_raw = w_base + gamma * coi_delta + Decimal("0.08") * score_delta
    p_win = clip(p_win_raw, Decimal("0.30"), Decimal("0.78")).quantize(Decimal("0.001"))

    base_conviction = (
        Decimal("0.40") * (p_win * Decimal("100.0")) + Decimal("0.60") * quantitative_score
    )

    # Multi-system institutional confluence adjustment:
    if checklist_passed is not None:
        confluence_adj = (Decimal(str(checklist_passed)) - Decimal("3.0")) * Decimal("2.0")
        conviction = clip(base_conviction + confluence_adj, Decimal("0.0"), Decimal("100.0")).quantize(Decimal("0.1"))
    else:
        conviction = base_conviction.quantize(Decimal("0.1"))

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
    checklist_passed: int | None = None,
) -> OpportunityGrade:
    """Grade A: ev_r >= 1.50 and conviction >= 75.0 and asymmetric_rr >= 2.50 (and checklist_passed >= 3).

    Grade B: ev_r >= 0.25 and conviction >= 62.0.
    """
    if (
        ev_r >= Decimal("1.50")
        and conviction >= Decimal("75.0")
        and asymmetric_rr >= Decimal("2.50")
        and (checklist_passed is None or checklist_passed >= 3)
    ):
        return OpportunityGrade.GRADE_A
    if ev_r >= Decimal("0.25") and conviction >= Decimal("62.0"):
        return OpportunityGrade.GRADE_B
    return OpportunityGrade.SUB_GRADE
