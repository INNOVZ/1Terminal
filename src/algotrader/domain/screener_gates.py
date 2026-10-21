"""Reference Specification Implementation for Screening Gates (Float Precision).

Matches Section 11 of AGENT.md. Used as the formal mathematical reference
and tested for parity against the Decimal production implementation
in `algotrader.domain.screening_gates`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GateEvaluationInput:
    close: float
    open_4h: float
    high_4h: float
    low_4h: float
    high_24h: float
    low_24h: float
    low_72h: float
    high_7d: float
    high_30d: float
    low_30d: float
    ema20_1h: float
    ema20_4h: float
    ema50_4h: float
    ema200_4h: float
    atr14_1h: float
    atr14_4h: float
    rsi_1h: float
    rsi_4h: float
    rsi_1d: float
    macd_hist_1h: float
    macd_hist_4h: float
    macd_hist_4h_prev: float
    bb_inside_kc_4h: bool
    bb_inside_kc_4h_lookback: tuple[bool, ...]
    bb_bandwidth_4h: float
    poc_30d: float
    vah_30d: float
    val_30d: float
    rvol_4h: float
    vcr_4h: float
    ret_24h: float
    ret_7d: float
    ret_14d: float
    runup_30d: float
    excursion_72h: float
    oi_delta_1h: float
    oi_delta_4h: float
    oi_delta_24h: float
    oi_delta_7d: float
    funding_rate_8h: float
    basis_bps: float
    taker_buy_ratio_1h: float
    taker_buy_ratio_4h: float
    taker_buy_ratio_24h: float
    has_4h_bearish_rsi_div: bool
    rsi_1w: float = 50.0


def evaluate_long_gates(s: GateEvaluationInput, archetype: str) -> tuple[bool, str | None]:
    natr_4h = s.atr14_4h / max(s.close, 1e-8)

    # Gate L1: Minervini Trend & Wyckoff Spring Baseline
    is_standard_trend = (
        (s.close >= s.ema50_4h * 0.985)
        and (s.ema20_1h >= s.ema50_4h * 0.975)
        and (s.ema50_4h >= s.ema200_4h * 0.96)
    )
    is_wyckoff_spring = (
        (s.low_24h < s.ema50_4h - 0.15 * s.atr14_4h)
        and (s.close >= s.ema50_4h)
        and (s.vcr_4h <= 0.90)
    )
    # Macro Death Cross Veto: Price and EMA50 must not be submerged in a severe macro downtrend under EMA200
    has_macro_death_cross = (
        (s.ema50_4h < s.ema200_4h * 0.95)
        and (s.close < s.ema200_4h * 0.96)
    )

    # Post-Crash Markdown Filter: Exclude tokens that dumped severely over 7 days while bleeding OI or deeply below EMA200
    is_post_crash_markdown = (
        (s.ret_7d <= -0.20)
        and (
            (s.oi_delta_7d <= -0.08)
            or (s.close < s.ema200_4h * 0.95)
        )
    )

    if not (is_standard_trend or is_wyckoff_spring) or has_macro_death_cross or is_post_crash_markdown:
        return False, "REJECT_L1_TREND_OR_SPRING_BASELINE"

    # Gate L2: Anti-Chasing 2.0 & High-Tight Flag Continuation
    atr_ext_4h = (s.close - s.ema20_4h) / max(s.atr14_4h, 1e-8)
    standard_l2 = (
        (s.excursion_72h <= 0.36)
        and (-0.08 <= s.ret_24h <= 0.14)
        and (s.rsi_1h <= 76.0)
        and (s.rsi_4h <= 70.0)
        and (atr_ext_4h <= 2.25)
    )
    is_high_tight_flag = (
        (((s.high_4h - s.low_4h) / max(s.close, 1e-8)) <= 1.5 * natr_4h)
        and (s.close >= s.ema20_1h * 0.99)
        and (((s.close - s.ema20_1h) / max(s.atr14_1h, 1e-8)) <= 1.10)
        and (s.funding_rate_8h <= 0.0001)
        and (s.oi_delta_24h >= 0.10)
        and (s.taker_buy_ratio_4h >= 0.52)
    )
    flag_l2 = (
        is_high_tight_flag
        and (s.excursion_72h <= 0.60)
        and (s.ret_24h <= 0.35)
        and (s.rsi_1h <= 78.0)
    )
    is_htf_overbought = (s.rsi_1d >= 70.0) or (getattr(s, "rsi_1w", 50.0) >= 70.0)
    if not (standard_l2 or flag_l2) or is_htf_overbought:
        return False, "REJECT_L2_ANTI_CHASING"

    # Gate L3: Base Duration & Ceiling Proximity
    range_height_30d = (s.high_30d - s.low_30d) / max(s.low_30d, 1e-8)
    dist_to_ceiling = (s.high_30d - s.close) / max(s.close, 1e-8)
    standard_l3 = (range_height_30d <= 0.65) and (-0.03 <= dist_to_ceiling <= 0.35)
    flag_l3 = is_high_tight_flag and (range_height_30d <= 1.20) and (-0.05 <= dist_to_ceiling <= 0.35)
    if not (standard_l3 or flag_l3):
        return False, "REJECT_L3_BASE_DURATION"

    # Gate L4: 30-Day VPVR Value Area Acceptance
    va_pos = (s.close - s.val_30d) / max(s.vah_30d - s.val_30d, 1e-8)
    if not ((s.close >= s.poc_30d * 0.985) and (va_pos >= 0.25)):
        return False, "REJECT_L4_VPVR_VALUE_ACCEPTANCE"

    # Gate L5: Archetype-Conditional Volatility Compression
    in_current_squeeze = s.bb_inside_kc_4h or (s.bb_bandwidth_4h <= 0.24)
    in_recent_squeeze_exp = any(s.bb_inside_kc_4h_lookback[:6]) and (s.rvol_4h >= 1.25)
    if archetype in ("COIL_SPRING_LONG", "OI_ACCUMULATION_LONG"):
        l5_pass = in_current_squeeze
    else:
        l5_pass = in_current_squeeze or in_recent_squeeze_exp
    if not l5_pass:
        return False, "REJECT_L5_VOLATILITY_COMPRESSION"

    # Gate L6: S/R Ceiling Absorption & Bull-Trap Filter
    if dist_to_ceiling <= 0.045:
        l6_pass = (
            (s.oi_delta_7d >= 0.0)
            and (s.oi_delta_4h >= 0.015)
            and (s.rvol_4h >= 1.20)
            and (s.taker_buy_ratio_4h >= 0.52)
        )
        if not l6_pass:
            return False, "REJECT_L6_BULL_TRAP_RESISTANCE"

    return True, None


def evaluate_short_gates(s: GateEvaluationInput) -> tuple[bool, str | None]:
    # Anti-Squeeze & Trend Circuit Breaker
    funding_8h = s.funding_rate_8h or 0.0
    oi_7d = s.oi_delta_7d or 0.0
    oi_1h = s.oi_delta_1h or 0.0
    taker_24h = s.taker_buy_ratio_24h or 0.5
    if (
        s.funding_rate_8h is not None
        and funding_8h < 0.0
        and (oi_7d >= 0.10)
        and (s.oi_delta_1h is None or oi_1h >= 0.0 or taker_24h >= 0.50)
        and s.close >= s.ema50_4h * 0.975
    ):
        return False, "REJECT_ANTI_SQUEEZE_NEGATIVE_FUNDING"
    if taker_24h >= 0.51 and not s.has_4h_bearish_rsi_div and s.close >= s.ema50_4h:
        return False, "REJECT_ANTI_SQUEEZE_BUYER_DOMINANCE"

    # Gate S1: Mandatory Macro Run-Up
    ema50_peak_excursion = (s.high_7d - s.ema50_4h) / max(s.ema50_4h, 1e-8)
    s1_pass = (s.runup_30d >= 0.35) or (s.ret_14d >= 0.22 and ema50_peak_excursion >= 0.15)
    if not s1_pass:
        return False, "REJECT_S1_NO_MACRO_RUNUP"

    # Gate S3: Anti-Capitulation & Floor Protection
    dist_72h_low = (s.close - s.low_72h) / max(s.low_72h, 1e-8)
    dist_30d_low = (s.close - s.low_30d) / max(s.low_30d, 1e-8)
    pos_in_range_30d = (s.close - s.low_30d) / max(s.high_30d - s.low_30d, 1e-8)
    downward_atr_ext = (s.ema20_4h - s.close) / max(s.atr14_4h, 1e-8)
    s3_pass = (
        (dist_72h_low > 0.04)
        and (dist_30d_low > 0.08)
        and (s.rsi_1h >= 30.0)
        and (s.rsi_4h >= 38.0)
        and (pos_in_range_30d >= 0.45)
        and (downward_atr_ext <= 2.00)
    )
    if not s3_pass:
        return False, "REJECT_S3_CAPITULATION_FLOOR"

    # Gate S2 + S4 Paired Track A: Upper-Range Climax Blowoff / Wyckoff UTAD
    upper_wick_ratio_4h = (s.high_4h - max(s.open_4h, s.close)) / max(s.high_4h - s.low_4h, 1e-8)
    rejected_from_high = s.close <= (s.high_24h - 0.50 * s.atr14_4h)
    is_climax_blowoff = (s.rsi_1d >= 76.0) and (upper_wick_ratio_4h >= 0.45)
    is_utad_divergence = s.has_4h_bearish_rsi_div and (s.macd_hist_4h < s.macd_hist_4h_prev)
    track_a_climax_utad = (
        (pos_in_range_30d >= 0.65)
        and rejected_from_high
        and (is_climax_blowoff or is_utad_divergence)
    )

    # Gate S2 + S4 Paired Track B: Structural Breakdown / Macro Distribution Rollover
    below_key_structure = (s.close < s.ema50_4h) and (s.close < s.poc_30d)
    momentum_and_orderflow_decay = (
        (s.macd_hist_4h < 0.0)
        and (s.macd_hist_4h < s.macd_hist_4h_prev)
        and (s.taker_buy_ratio_24h <= 0.47)
    )
    track_b_distribution = below_key_structure and momentum_and_orderflow_decay

    if not (track_a_climax_utad or track_b_distribution):
        return False, "REJECT_S2_S4_STRUCTURE_EXHAUSTION_MISMATCH"

    # Gate S5: Bimodal Derivatives Crowding (Trapped Longs OR Short-Squeeze Flush)
    trapped_longs = (s.funding_rate_8h >= 0.0001 or s.basis_bps >= 12.0) and (s.oi_delta_7d >= 0.15)
    squeeze_flush = (
        (s.funding_rate_8h <= -0.0003)
        and (s.oi_delta_7d >= 0.25)
        and (s.oi_delta_1h <= -0.015)
        and (s.taker_buy_ratio_1h <= 0.46)
    )
    if not (trapped_longs or squeeze_flush):
        return False, "REJECT_S5_NO_DERIVATIVES_CROWDING"

    return True, None
