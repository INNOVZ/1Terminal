from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from algotrader.domain.crypto_screener_models import (
    CryptoScreenerScoreBreakdown,
    SetupLabel,
)
from algotrader.domain.models import Direction

EPSILON = Decimal("1e-8")

W_BASE: dict[SetupLabel | str, Decimal] = {
    SetupLabel.FUNDING_SQUEEZE_SPRING_LONG: Decimal("0.60"),
    SetupLabel.WYCKOFF_SQUEEZE_SPRING: Decimal("0.59"),
    SetupLabel.STEALTH_OI_ACCUMULATION: Decimal("0.57"),
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
    "FUNDING_SQUEEZE_SPRING_LONG": Decimal("0.60"),
    "WYCKOFF_SQUEEZE_SPRING": Decimal("0.59"),
    "STEALTH_OI_ACCUMULATION": Decimal("0.57"),
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
    volume_score: Decimal = Decimal("50.0")  # Dedicated Volume confirmation score
    momentum_score: Decimal = Decimal("50.0")  # Dedicated Momentum confirmation score
    derivatives_score: Decimal = Decimal("50.0")  # Dedicated Derivatives & OI confirmation score
    alpha_score: Decimal = Decimal("50.0")  # Dedicated Alpha confirmation score


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
    volume_score: Decimal = Decimal("50.0")
    momentum_score: Decimal = Decimal("50.0")
    derivatives_score: Decimal = Decimal("50.0")
    alpha_score: Decimal = Decimal("50.0")


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
    oi_change_1h: Decimal | None = None,
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

    # Dedicated Volume Confirmation Score (0 to 100)
    # Rewards Wyckoff base supply dry-up + breakout relative volume surge
    base_volume = Decimal("50.0")
    if vol_contraction_ratio <= Decimal("0.80"):
        dryup_pts = Decimal("25.0")
    elif vol_contraction_ratio <= Decimal("0.90"):
        dryup_pts = Decimal("15.0")
    elif vol_contraction_ratio <= Decimal("1.05"):
        dryup_pts = Decimal("5.0")
    elif vol_contraction_ratio >= Decimal("1.30"):
        dryup_pts = Decimal("-15.0")
    else:
        dryup_pts = Decimal("0.0")

    if bandwidth_4h <= Decimal("0.15") and vol_contraction_ratio <= Decimal("0.85") and rvol_4h <= Decimal("0.65"):
        # Wyckoff Volume Vacuum: extreme dry-up in tight coil indicates seller exhaustion
        rvol_pts = Decimal("15.0")
    elif rvol_4h >= Decimal("1.50"):
        rvol_pts = Decimal("25.0")
    elif rvol_4h >= Decimal("1.25"):
        rvol_pts = Decimal("15.0")
    elif rvol_4h >= Decimal("1.00"):
        rvol_pts = Decimal("5.0")
    elif rvol_4h <= Decimal("0.65"):
        rvol_pts = Decimal("-15.0")
    else:
        rvol_pts = Decimal("0.0")

    synergy_bonus = (
        Decimal("5.0")
        if (vol_contraction_ratio <= Decimal("0.90") and rvol_4h >= Decimal("1.20"))
        else Decimal("0.0")
    )
    volume_score = clip(base_volume + dryup_pts + rvol_pts + synergy_bonus, Decimal("0.0"), Decimal("100.0")).quantize(Decimal("0.1"))

    # 3. Smart Money & Orderflow Absorption (0 to 100)
    base_abs = resistance_absorption_score
    # OI Macro confirmation
    if oi_change_7d is not None:
        if oi_change_7d >= Decimal("0.05"):
            oi_7d_pts = Decimal("10.0")
        elif oi_change_7d >= Decimal("0.0"):
            oi_7d_pts = Decimal("5.0")
        elif oi_change_7d <= Decimal("-0.05"):
            oi_7d_pts = Decimal("-10.0")
        else:
            oi_7d_pts = Decimal("-3.0")
    else:
        oi_7d_pts = Decimal("0.0")

    # OI Intraday confirmation
    if oi_change_4h is not None:
        if oi_change_4h >= Decimal("0.01"):
            oi_4h_pts = Decimal("5.0")
        elif oi_change_4h <= Decimal("-0.03"):
            oi_4h_pts = Decimal("-5.0")
        else:
            oi_4h_pts = Decimal("0.0")
    else:
        oi_4h_pts = Decimal("0.0")

    # Taker buy flow confirmation (1.00 scale: buy_vol / sell_vol)
    if taker_buy_ratio_4h >= Decimal("1.18"):
        taker_pts = Decimal("10.0")
    elif taker_buy_ratio_4h >= Decimal("1.05"):
        taker_pts = Decimal("5.0")
    elif taker_buy_ratio_4h <= Decimal("0.85"):
        taker_pts = Decimal("-8.0")
    else:
        taker_pts = Decimal("0.0")

    # Funding rate crowding guard
    if funding_rate is not None:
        if funding_rate <= Decimal("0.00005"):
            funding_pts = Decimal("5.0")
        elif funding_rate >= Decimal("0.00020"):
            funding_pts = Decimal("-8.0")
        else:
            funding_pts = Decimal("0.0")
    else:
        funding_pts = Decimal("0.0")

    # CVD flow confirmation
    if cvd_zscore >= Decimal("1.0"):
        cvd_pts = Decimal("6.0")
    elif cvd_zscore <= Decimal("-1.0"):
        cvd_pts = Decimal("-6.0")
    else:
        cvd_pts = Decimal("0.0")

    absorption_score = clip(
        base_abs + oi_7d_pts + oi_4h_pts + taker_pts + funding_pts + cvd_pts,
        Decimal("0.0"),
        Decimal("100.0"),
    )

    # Dedicated Derivatives Confirmation Score (0 to 100)
    # Fix 2: If oi_change_7d is None, do not award false high score (sub-par baseline 40.0)
    if oi_change_7d is None:
        derivatives_score = Decimal("40.0")
    else:
        base_deriv = Decimal("50.0")
        oi_4h_comp = min(Decimal("16.0"), max(Decimal("-16.0"), (oi_change_4h or Decimal("0")) * Decimal("320.0"))) if oi_change_4h is not None else Decimal("0.0")
        oi_7d_comp = min(Decimal("16.0"), max(Decimal("-16.0"), (oi_change_7d or Decimal("0")) * Decimal("160.0"))) if oi_change_7d is not None else Decimal("0.0")
        oi_1h_comp = min(Decimal("8.0"), max(Decimal("-8.0"), (oi_change_1h or Decimal("0")) * Decimal("400.0"))) if oi_change_1h is not None else Decimal("0.0")
        oi_blended_delta = oi_4h_comp + oi_7d_comp + oi_1h_comp
        taker_delta = min(Decimal("20.0"), max(Decimal("-20.0"), (taker_buy_ratio_4h - Decimal("1.00")) * Decimal("50.0")))
        derivatives_score = clip(
            base_deriv + oi_blended_delta + taker_delta + funding_pts + cvd_pts,
            Decimal("0.0"),
            Decimal("100.0"),
        ).quantize(Decimal("0.1"))

    # 4. Trend Alignment & Momentum Expansion (0 to 100)
    if close < ema200_4h and ema50_4h < ema200_4h * Decimal("0.98"):
        ema_score = Decimal("25.0")
    elif close >= ema20_4h >= ema50_4h:
        ema_score = Decimal("85.0")
    elif close >= ema50_4h:
        ema_score = Decimal("70.0")
    else:
        ema_score = Decimal("50.0")

    # MACD momentum expansion vs negative deceleration
    if macd_hist_4h > macd_hist_4h_prev:
        macd_bonus = Decimal("15.0")
    elif macd_hist_4h < Decimal("0.0") and macd_hist_4h < macd_hist_4h_prev:
        macd_bonus = Decimal("-8.0")
    else:
        macd_bonus = Decimal("0.0")

    # Alpha vs BTC
    if btc_residual_alpha >= Decimal("0.0"):
        alpha_bonus = min(Decimal("15.0"), btc_residual_alpha * Decimal("300.0"))
    else:
        alpha_bonus = max(Decimal("-10.0"), btc_residual_alpha * Decimal("200.0"))

    rvol_bonus = Decimal("10.0") if rvol_4h >= Decimal("1.25") else Decimal("0.0")
    trend_raw = ema_score + macd_bonus + alpha_bonus + rvol_bonus
    trend_momentum_score = clip(
        Decimal("0.30") * adx_subscore + Decimal("0.70") * trend_raw,
        Decimal("0.0"),
        Decimal("100.0"),
    )

    # Dedicated Momentum Confirmation Score (0 to 100)
    base_mom = Decimal("50.0")
    mom_macd = Decimal("20.0") if macd_hist_4h > macd_hist_4h_prev else (Decimal("-15.0") if macd_hist_4h < Decimal("0.0") else Decimal("0.0"))
    mom_alpha = min(Decimal("20.0"), max(Decimal("-20.0"), btc_residual_alpha * Decimal("400.0")))
    mom_adx = (adx_subscore - Decimal("50.0")) * Decimal("0.30")
    momentum_score = clip(base_mom + mom_macd + mom_alpha + mom_adx, Decimal("0.0"), Decimal("100.0")).quantize(Decimal("0.1"))

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

    # Dedicated Alpha Score (BTC-Residual Alpha Normalized: 0.00% -> 50.0)
    alpha_score = clip(
        Decimal("50.0") + btc_residual_alpha * Decimal("500.0"),
        Decimal("0.0"),
        Decimal("100.0"),
    ).quantize(Decimal("0.1"))

    return LongContinuousFactorScores(
        volatility_score=volatility_score.quantize(Decimal("0.1")),
        value_score=value_score.quantize(Decimal("0.1")),
        absorption_score=absorption_score.quantize(Decimal("0.1")),
        trend_momentum_score=trend_momentum_score.quantize(Decimal("0.1")),
        asymmetry_score=asymmetry_score.quantize(Decimal("0.1")),
        raw_score=raw_score,
        macro_multiplier=mult.quantize(Decimal("0.01")),
        final_composite_score=final_composite_score,
        volume_score=volume_score,
        momentum_score=momentum_score,
        derivatives_score=derivatives_score,
        alpha_score=alpha_score,
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
    oi_change_1h: Decimal | None = None,
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

    # 2. Value Rejection & Resistance Geometry (0 to 100)
    # Institutional shorts require price to be rejecting resistance (VAH, POC, or 4H EMA50),
    # strictly penalizing selling into value area exhaustion/floor below VAL.
    poc_denom = max(poc_30d, Decimal("1e-8"))
    val_denom = max(val_30d, Decimal("1e-8"))
    if close >= vah_30d * Decimal("0.96") and (has_bearish_div or upper_wick_ratio_4h >= Decimal("0.30")):
        # Premium: Wyckoff UTAD or range high rejection with supply confirmation
        base_val = Decimal("82.0") + min(Decimal("18.0"), upper_wick_ratio_4h * Decimal("30.0"))
    elif abs(close - poc_30d) / poc_denom <= Decimal("0.040") and close <= poc_30d * Decimal("1.015"):
        # Retest of POC resistance ceiling from below
        dist_poc = abs(poc_30d - close) / poc_denom
        base_val = Decimal("72.0") + (Decimal("0.04") - dist_poc) * Decimal("200.0")
    elif abs(close - ema50_4h) / max(ema50_4h, EPSILON) <= Decimal("0.035") and close <= ema50_4h * Decimal("1.015"):
        # Retest of 4H EMA50 resistance ceiling
        base_val = Decimal("65.0")
    elif close < val_30d:
        # Severe penalty for bottom-shorting: price is already below VAL
        dist_below_val = (val_30d - close) / val_denom
        base_val = max(Decimal("15.0"), Decimal("40.0") - dist_below_val * Decimal("150.0"))
    else:
        # Inside value area without clear resistance test
        base_val = Decimal("45.0")

    stage4_bonus = (
        Decimal("10.0") if (close <= ema50_4h and ema50_4h <= ema200_4h * Decimal("1.02")) else Decimal("0.0")
    )
    value_rejection_score = clip(base_val + stage4_bonus, Decimal("0.0"), Decimal("100.0"))

    # 3. Smart Money Distribution & Trapped Longs (0 to 100)
    base_dist = Decimal("40.0")
    # Taker sell flow (taker buy ratio < 1.00 means sellers dominate)
    if taker_buy_ratio_4h <= Decimal("0.85"):
        base_dist += Decimal("18.0")
    elif taker_buy_ratio_4h <= Decimal("0.95"):
        base_dist += Decimal("10.0")
    elif taker_buy_ratio_4h >= Decimal("1.15"):
        base_dist -= Decimal("15.0")

    # CVD Selling Pressure
    if cvd_zscore <= Decimal("-0.75"):
        base_dist += Decimal("15.0")
    elif cvd_zscore <= Decimal("-0.15"):
        base_dist += Decimal("8.0")
    elif cvd_zscore >= Decimal("0.40"):
        base_dist -= Decimal("12.0")

    # Trapped Longs / Funding: positive funding during dump = trapped longs paying shorts
    # Negative funding = crowded short squeeze risk (heavy penalty)
    if funding_rate is not None and funding_rate <= Decimal("-0.0001"):
        base_dist -= Decimal("25.0")
    elif funding_rate is not None and funding_rate >= Decimal("0.00008"):
        base_dist += Decimal("12.0")
    elif funding_rate is not None and funding_rate >= Decimal("0.0"):
        base_dist += Decimal("6.0")

    wick_pts = min(Decimal("15.0"), upper_wick_ratio_4h * Decimal("30.0"))
    div_pts = Decimal("15.0") if has_bearish_div else Decimal("0.0")
    distribution_score = clip(base_dist + wick_pts + div_pts, Decimal("0.0"), Decimal("100.0"))

    # 4. Trend Breakdown & Momentum Deceleration (0 to 100)
    if close <= ema20_4h <= ema50_4h:
        # Check for oversold extension below EMA20
        dist_below_ema20 = (ema20_4h - close) / max(ema20_4h, EPSILON)
        if dist_below_ema20 > Decimal("0.07"):
            ema_score = Decimal("45.0")  # Overextended dump, high snapback risk
        else:
            ema_score = Decimal("68.0")
    elif close <= ema50_4h:
        ema_score = Decimal("55.0")
    else:
        ema_score = Decimal("35.0")

    macd_bonus = (
        Decimal("12.0")
        if (macd_hist_4h < macd_hist_4h_prev or macd_hist_4h < Decimal("0.0"))
        else Decimal("0.0")
    )
    # Relative weakness vs BTC (negative residual alpha means underperforming market)
    alpha_bonus = min(
        Decimal("12.0"),
        max(Decimal("0.0"), -btc_residual_alpha * Decimal("250.0")),
    )
    rvol_bonus = Decimal("8.0") if rvol_4h >= Decimal("1.25") else Decimal("0.0")
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

    # Dedicated Short Volume Score (Distribution Volume Confirmation)
    base_short_vol = Decimal("50.0")
    if rvol_4h >= Decimal("1.50"):
        short_rvol_pts = Decimal("25.0")
    elif rvol_4h >= Decimal("1.25"):
        short_rvol_pts = Decimal("15.0")
    elif rvol_4h <= Decimal("0.65"):
        short_rvol_pts = Decimal("-10.0")
    else:
        short_rvol_pts = Decimal("0.0")
    short_volume_score = clip(base_short_vol + short_rvol_pts, Decimal("0.0"), Decimal("100.0")).quantize(Decimal("0.1"))

    # Dedicated Short Momentum Score
    base_short_mom = Decimal("50.0")
    mom_macd_short = Decimal("20.0") if (macd_hist_4h < macd_hist_4h_prev or macd_hist_4h < Decimal("0.0")) else Decimal("-10.0")
    mom_alpha_short = min(Decimal("20.0"), max(Decimal("-20.0"), -btc_residual_alpha * Decimal("350.0")))
    short_momentum_score = clip(base_short_mom + mom_macd_short + mom_alpha_short, Decimal("0.0"), Decimal("100.0")).quantize(Decimal("0.1"))

    # Dedicated Short Derivatives Score
    # Fix 2: If oi_change_7d is None, do not award false high score (sub-par baseline 40.0)
    if oi_change_7d is None:
        short_derivatives_score = Decimal("40.0")
    else:
        base_short_deriv = Decimal("50.0")
        short_deriv_pts = (distribution_score - Decimal("50.0"))
        short_derivatives_score = clip(base_short_deriv + short_deriv_pts, Decimal("0.0"), Decimal("100.0")).quantize(Decimal("0.1"))

    # Dedicated Short Alpha Score (Residual Alpha underperformance vs BTC Normalized: 0.00% -> 50.0)
    alpha_score = clip(
        Decimal("50.0") - btc_residual_alpha * Decimal("500.0"),
        Decimal("0.0"),
        Decimal("100.0"),
    ).quantize(Decimal("0.1"))

    return ShortContinuousFactorScores(
        volatility_score=volatility_score.quantize(Decimal("0.1")),
        value_rejection_score=value_rejection_score.quantize(Decimal("0.1")),
        distribution_score=distribution_score.quantize(Decimal("0.1")),
        trend_breakdown_score=trend_breakdown_score.quantize(Decimal("0.1")),
        asymmetry_score=asymmetry_score.quantize(Decimal("0.1")),
        raw_score=raw_score,
        macro_multiplier=mult.quantize(Decimal("0.01")),
        final_composite_score=final_composite_score,
        volume_score=short_volume_score,
        momentum_score=short_momentum_score,
        derivatives_score=short_derivatives_score,
        alpha_score=alpha_score,
    )


def calculate_adx_subscore(
    archetype: SetupLabel | str,
    adx_4h: Decimal,
    adx_4h_prev: Decimal,
    dmi_plus: Decimal,
    dmi_minus: Decimal,
    *,
    bandwidth_4h: Decimal | None = None,
    bb_inside_kc: bool = False,
    delta_adx_1h: Decimal | None = None,
    delta_dmi_diff_1h: Decimal | None = None,
    dmi_plus_4h: Decimal | None = None,
    dmi_minus_4h: Decimal | None = None,
) -> Decimal:
    """Regime-adaptive ADX subscore (0 to 100).

    Task 3: Resolve the ADX vs. Squeeze Scoring Paradox
    - Separate Established Trend Strength (ADX_4h >= 25) from Coiled Ignition Inflection.
    - When a token is in verified compression (BB_Bandwidth_4h <= 16% or BB_Inside_KC == True),
      award full ADX sub-score points (100.0) if +DI_4h > -DI_4h AND either Delta ADX_1h(3) >= +1.5 or Delta(+DI_1h - -DI_1h) >= +3.0.
    """
    # 1. Coiled Ignition Inflection (Task 3 Institutional Rule)
    is_compressed = (bandwidth_4h is not None and bandwidth_4h <= Decimal("0.16")) or bb_inside_kc
    dp_4h = dmi_plus_4h if dmi_plus_4h is not None else dmi_plus
    dm_4h = dmi_minus_4h if dmi_minus_4h is not None else dmi_minus
    has_inflection = (
        (delta_adx_1h is not None and delta_adx_1h >= Decimal("1.5"))
        or (delta_dmi_diff_1h is not None and delta_dmi_diff_1h >= Decimal("3.0"))
    )
    if is_compressed and dp_4h > dm_4h and has_inflection:
        return Decimal("100.0")

    # 2. Established Trend Strength (ADX_4h >= 25)
    if adx_4h >= Decimal("25.0") and dmi_plus > dmi_minus:
        bonus = Decimal("20.0") if adx_4h > adx_4h_prev else Decimal("0.0")
        raw = Decimal("4.0") * (adx_4h - Decimal("15.0")) + bonus
        return clip(raw, Decimal("50.0"), Decimal("100.0"))

    # 3. Pre-breakout accumulation baseline (when not yet ignited)
    is_accumulation = archetype in (
        SetupLabel.SHORT_SQUEEZE_ABSORPTION_LONG,
        SetupLabel.COIL_SPRING_LONG,
        SetupLabel.OI_ACCUMULATION_LONG,
        SetupLabel.VCP_COMPRESSION_LONG,
        SetupLabel.RANGE_BOTTOM_ACCUMULATION_LONG,
        SetupLabel.FUNDING_SQUEEZE_SPRING_LONG,
        SetupLabel.WYCKOFF_SQUEEZE_SPRING,
        SetupLabel.STEALTH_OI_ACCUMULATION,
        "SHORT_SQUEEZE_ABSORPTION_LONG",
        "COIL_SPRING_LONG",
        "OI_ACCUMULATION_LONG",
        "VCP_COMPRESSION_LONG",
        "RANGE_BOTTOM_ACCUMULATION_LONG",
        "FUNDING_SQUEEZE_SPRING_LONG",
        "WYCKOFF_SQUEEZE_SPRING",
        "STEALTH_OI_ACCUMULATION",
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
