"""Production Institutional Screening Gates (High-Precision Decimal).

Implements mandatory Long Gates (L1–L6) and Short Gates (S1–S5 Two-Track Precision Spec)
using exact Decimal arithmetic and zero-division guards for live production execution.
Tested for parity against the reference specification in `algotrader.domain.screener_gates`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from algotrader.domain.crypto_screener_models import SetupLabel

EPSILON = Decimal("1e-8")


@dataclass(frozen=True, slots=True)
class LongGateInputs:
    close: Decimal
    ema50_4h: Decimal
    ema20_1h: Decimal
    ema200_4h: Decimal
    low_24h: Decimal
    atr14_4h: Decimal
    vcr_4h: Decimal
    ema20_4h: Decimal
    excursion_72h: Decimal
    ret_24h: Decimal
    rsi_1h: Decimal
    rsi_4h: Decimal
    high_30d: Decimal
    low_30d: Decimal
    poc_30d: Decimal
    val_30d: Decimal
    vah_30d: Decimal
    bb_inside_kc_4h: bool
    bb_bandwidth_4h: Decimal
    bb_inside_kc_4h_lookback: Sequence[bool] = field(default_factory=tuple)
    rvol_4h: Decimal = Decimal("1.0")
    oi_delta_7d: Decimal | None = None
    oi_delta_4h: Decimal | None = None
    oi_delta_1h: Decimal | None = None
    taker_buy_ratio_4h: Decimal = Decimal("1.0000")
    # High-tight flag continuation inputs (Section 4)
    high_4h: Decimal | None = None
    low_4h: Decimal | None = None
    atr14_1h: Decimal | None = None
    funding_rate_8h: Decimal | None = None
    oi_delta_24h: Decimal | None = None
    rsi_1d: Decimal | None = None
    rsi_1w: Decimal | None = None
    ret_7d: Decimal = Decimal("0")
    # V2.7 Kinetic Ignition & Anti-Chasing 2.1 inputs
    prior_bb_bandwidth_4h: Decimal | None = None
    candle_4h_close_pos: Decimal | None = None
    pivot_resistance: Decimal | None = None
    has_kinetic_ignition: bool = False
    pocket_pivot_1h: bool = False
    micro_expansion_1h: bool = False
    of_absorption_1h: bool = False
    taker_buy_ratio_1h: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ShortGateInputs:
    close: Decimal
    ema50_4h: Decimal
    high_7d: Decimal
    range_height_30d: Decimal
    ret_7d: Decimal
    ret_14d: Decimal
    high_30d: Decimal
    low_30d: Decimal
    days_since_30d_high: Decimal
    ema20_4h: Decimal
    atr14_4h: Decimal
    ret_24h: Decimal
    rsi_1h: Decimal
    rsi_4h: Decimal
    has_bearish_div: bool
    has_climax_blowoff: bool
    poc_30d: Decimal
    macd_hist_4h: Decimal
    taker_buy_ratio_24h: Decimal = Decimal("1.0000")
    funding_rate_8h: Decimal | None = None
    oi_delta_7d: Decimal | None = None
    oi_delta_1h: Decimal | None = None
    taker_buy_ratio_1h: Decimal | None = None
    # Two-track precision inputs (Section 5)
    low_72h: Decimal | None = None
    high_24h: Decimal | None = None
    open_4h: Decimal | None = None
    high_4h: Decimal | None = None
    low_4h: Decimal | None = None
    rsi_1d: Decimal | None = None
    macd_hist_4h_prev: Decimal | None = None
    basis_bps: Decimal | None = None
    runup_30d: Decimal | None = None


def compute_coil_maturity_score(inputs: LongGateInputs) -> int:
    """Compute Coil Maturity Score (0-100) for volatility compression setups.

    Evaluates the quality, depth, duration, and structure of volatility compression.
    Candidates with mature coils (score >= 65) represent pre-loaded springs ready
    to expand, allowing bypass of active volume ignition requirements in Gate L6.
    """
    coil_maturity = 0

    # 1. Compression Depth (max 30 pts)
    if inputs.bb_bandwidth_4h <= Decimal("0.08"):
        coil_maturity += 30
    elif inputs.bb_bandwidth_4h <= Decimal("0.12"):
        coil_maturity += 25
    elif inputs.bb_bandwidth_4h <= Decimal("0.18"):
        coil_maturity += 15

    # 2. Volume & Range Contraction (max 25 pts)
    if inputs.vcr_4h <= Decimal("0.65"):
        coil_maturity += 25
    elif inputs.vcr_4h <= Decimal("0.85"):
        coil_maturity += 15

    # 3. Duration: Count consecutive low-volatility / squeeze bars (max 20 pts)
    squeeze_bars = sum(1 for b in inputs.bb_inside_kc_4h_lookback if b) + (1 if inputs.bb_inside_kc_4h else 0)
    if squeeze_bars >= 8:
        coil_maturity += 20
    elif squeeze_bars >= 4:
        coil_maturity += 12

    # 4. Position Quality & Structural Holding (max 15 pts)
    if inputs.close >= inputs.ema20_4h * Decimal("0.995") and inputs.close >= inputs.poc_30d * Decimal("0.975"):
        coil_maturity += 15

    # 5. RSI Neutral Coil: Spring wound tightly without directional exhaustion (max 10 pts)
    if Decimal("40.0") <= inputs.rsi_4h <= Decimal("60.0"):
        coil_maturity += 10

    return coil_maturity


def is_volume_vacuum_setup(inputs: LongGateInputs) -> bool:
    """Detect Volume Vacuum (Supply Exhaustion) Pre-Ignition Signal.

    When trading volume drops to <= 55% of average, price volatility contracts (vcr <= 0.85),
    24h price action is flat (-4% to +4%), and price holds structural EMA20,
    the absence of volume reflects extreme seller exhaustion rather than lack of interest.
    """
    return bool(
        inputs.rvol_4h <= Decimal("0.55")
        and inputs.vcr_4h <= Decimal("0.85")
        and abs(inputs.ret_24h) <= Decimal("0.04")
        and inputs.bb_bandwidth_4h <= Decimal("0.15")
        and inputs.close >= inputs.ema20_4h * Decimal("0.99")
    )


def is_mature_squeeze_setup(inputs: LongGateInputs) -> bool:
    """Detect Mature Squeeze Duration (32+ hours in squeeze holding EMA20)."""
    squeeze_bars = sum(1 for b in inputs.bb_inside_kc_4h_lookback if b) + (1 if inputs.bb_inside_kc_4h else 0)
    return bool(
        squeeze_bars >= 8
        and inputs.bb_bandwidth_4h <= Decimal("0.15")
        and inputs.close >= inputs.ema20_4h * Decimal("0.985")
    )


def evaluate_long_gates(
    inputs: LongGateInputs, archetype: SetupLabel | str | None = None
) -> tuple[bool, dict[str, bool]]:
    """Evaluate mandatory domain screening gates L1-L6 for a LONG candidate.

    Returns (all_passed, gate_details).
    """
    atr = max(inputs.atr14_4h, EPSILON)
    natr_4h = inputs.atr14_4h / max(inputs.close, EPSILON)

    arch_val = archetype.value if isinstance(archetype, SetupLabel) else str(archetype or "")

    # Gate L1: Minervini Trend & Wyckoff Spring Baseline
    is_standard_trend = (
        (inputs.close >= inputs.ema50_4h * Decimal("0.985"))
        and (inputs.ema20_1h >= inputs.ema50_4h * Decimal("0.975"))
        and (inputs.ema50_4h >= inputs.ema200_4h * Decimal("0.96"))
    )
    is_wyckoff_spring = (
        (inputs.low_24h < inputs.ema50_4h - Decimal("0.15") * atr)
        and (inputs.close >= inputs.ema50_4h)
        and (inputs.vcr_4h <= Decimal("0.90"))
    )
    is_base_consolidation = (
        arch_val in ("VCP_COMPRESSION_LONG", "RANGE_BOTTOM_ACCUMULATION_LONG", "OI_ACCUMULATION_LONG")
        and (inputs.close >= inputs.ema50_4h * Decimal("0.985"))
        and (inputs.bb_inside_kc_4h or inputs.bb_bandwidth_4h <= Decimal("0.22"))
    )
    is_squeeze_absorption = (
        arch_val == "SHORT_SQUEEZE_ABSORPTION_LONG"
        and (inputs.funding_rate_8h is None or inputs.funding_rate_8h <= Decimal("-0.00005"))
        and (
            inputs.close >= inputs.ema50_4h * Decimal("0.975")
            or inputs.close >= inputs.poc_30d * Decimal("0.975")
        )
    )

    # Macro Death Cross Veto: Price and EMA50 must not be submerged in a severe macro downtrend under EMA200
    has_macro_death_cross = (
        (inputs.ema50_4h < inputs.ema200_4h * Decimal("0.95"))
        and (inputs.close < inputs.ema200_4h * Decimal("0.96"))
    )

    # Post-Crash Markdown Filter: Exclude tokens that dumped severely over 7 days while bleeding OI or deeply below EMA200
    is_post_crash_markdown = (
        (inputs.ret_7d <= Decimal("-0.20"))
        and (
            (inputs.oi_delta_7d is not None and inputs.oi_delta_7d <= Decimal("-0.08"))
            or (inputs.close < inputs.ema200_4h * Decimal("0.95"))
        )
    )

    l1 = (
        (is_standard_trend or is_wyckoff_spring or is_base_consolidation or is_squeeze_absorption)
        and not has_macro_death_cross
        and not is_post_crash_markdown
    )

    # Gate L2: Anti-Chasing 2.0 & High-Tight Flag Continuation
    atr_ext_4h = (inputs.close - inputs.ema20_4h) / atr
    atr_ext_ok = (
        (atr_ext_4h <= Decimal("2.50"))
        or (
            atr_ext_4h <= Decimal("3.20")
            and inputs.rsi_4h <= Decimal("65.0")
            and inputs.rsi_1h <= Decimal("74.0")
        )
        or (
            # Allow high-momentum breakout coil ignition up to 4.0 ATRs if HTF daily and 4H RSI remain non-overbought (<= 65.0)
            atr_ext_4h <= Decimal("4.00")
            and inputs.rsi_4h <= Decimal("65.0")
            and (inputs.rsi_1d is None or inputs.rsi_1d <= Decimal("65.0"))
            and inputs.rsi_1h <= Decimal("75.0")
        )
    )
    # Anti-Chasing 2.1: Standard range [-8.0%, +14.0%] with controlled first-bar ignition override for (+14.0%, +17.5%]
    ret_in_standard = (Decimal("-0.08") <= inputs.ret_24h <= Decimal("0.14"))
    ignition_override_ok = False
    if Decimal("0.14") < inputs.ret_24h <= Decimal("0.175"):
        prior_coiled = (
            inputs.prior_bb_bandwidth_4h is not None
            and inputs.prior_bb_bandwidth_4h <= Decimal("0.160")
        )
        near_pivot = (
            inputs.pivot_resistance is not None
            and (inputs.close - inputs.pivot_resistance) <= Decimal("0.85") * atr
        ) or (inputs.pivot_resistance is None)
        clean_candle = (
            inputs.candle_4h_close_pos is not None
            and inputs.candle_4h_close_pos >= Decimal("0.70")
        )
        momentum_ok = (
            inputs.rsi_4h <= Decimal("68.0")
            and inputs.rsi_1h <= Decimal("73.0")
        )
        ignition_override_ok = prior_coiled and near_pivot and clean_candle and momentum_ok

    ret_24h_pass = ret_in_standard or ignition_override_ok

    standard_l2 = (
        inputs.excursion_72h <= Decimal("0.36")
        and ret_24h_pass
        and (inputs.rsi_1h <= Decimal("76.0"))
        and (inputs.rsi_4h <= Decimal("70.0"))
        and atr_ext_ok
    )

    # Optional high-tight flag evaluation if metrics provided
    flag_l2 = False
    if (
        inputs.high_4h is not None
        and inputs.low_4h is not None
        and inputs.atr14_1h is not None
    ):
        high_4h = inputs.high_4h
        low_4h = inputs.low_4h
        atr14_1h = max(inputs.atr14_1h, EPSILON)
        funding = inputs.funding_rate_8h or Decimal("0")
        oi_24h = inputs.oi_delta_24h or Decimal("0")

        is_high_tight_flag = (
            (((high_4h - low_4h) / max(inputs.close, EPSILON)) <= Decimal("1.5") * natr_4h)
            and (inputs.close >= inputs.ema20_1h * Decimal("0.99"))
            and (((inputs.close - inputs.ema20_1h) / atr14_1h) <= Decimal("1.10"))
            and (funding <= Decimal("0.0001"))
            and (oi_24h >= Decimal("0.10"))
            and (inputs.taker_buy_ratio_4h >= Decimal("1.08"))
        )
        flag_l2 = (
            is_high_tight_flag
            and (inputs.excursion_72h <= Decimal("0.60"))
            and (inputs.ret_24h <= Decimal("0.35"))
            and (inputs.rsi_1h <= Decimal("78.0"))
        )

    # Anti-Chasing HTF Check: daily or weekly RSI >= 70.0 immediately eliminates the candidate
    is_htf_overbought = (
        (inputs.rsi_1d is not None and inputs.rsi_1d >= Decimal("70.0"))
        or (inputs.rsi_1w is not None and inputs.rsi_1w >= Decimal("70.0"))
    )
    l2 = (standard_l2 or flag_l2) and not is_htf_overbought

    # Gate L3: Multi-Week Base Duration & Ceiling Proximity
    range_height_30d = (inputs.high_30d - inputs.low_30d) / max(inputs.low_30d, EPSILON)
    dist_to_ceiling = (inputs.high_30d - inputs.close) / max(inputs.close, EPSILON)
    is_expansion_archetype = arch_val in ("BREAKOUT_LONG", "MOMENTUM_EMERGING_LONG")
    max_range = Decimal("2.50") if is_expansion_archetype else Decimal("1.80")
    max_dist = Decimal("0.45") if arch_val in ("SHORT_SQUEEZE_ABSORPTION_LONG", "RANGE_BOTTOM_ACCUMULATION_LONG") else Decimal("0.38")
    standard_l3 = (range_height_30d <= max_range) and (
        Decimal("-0.05") <= dist_to_ceiling <= max_dist
    )
    flag_l3 = flag_l2 and (range_height_30d <= Decimal("1.50")) and (
        Decimal("-0.05") <= dist_to_ceiling <= Decimal("0.38")
    )
    l3 = standard_l3 or flag_l3

    # Gate L4: 30-Day VPVR Value Area Acceptance (Auction Market Theory)
    # Principle: Candidate must be accepted within or above the institutional Value Profile,
    # rather than breaking down into liquidation discovery below multi-week support.
    va_span = max(inputs.vah_30d - inputs.val_30d, EPSILON)
    va_position = (inputs.close - inputs.val_30d) / va_span
    
    is_va_supported = (
        (inputs.close >= inputs.val_30d * Decimal("0.96"))
        or (inputs.close >= inputs.low_30d * Decimal("0.98"))
        or (inputs.close >= inputs.poc_30d * Decimal("0.975"))
        or (va_position >= Decimal("0.18"))
    )

    if arch_val in ("RANGE_BOTTOM_ACCUMULATION_LONG", "OI_ACCUMULATION_LONG"):
        # Fix 4: Strictly require close >= val_30d (never allow below Value Area Low)
        l4 = is_va_supported and (inputs.val_30d <= Decimal("0") or inputs.close >= inputs.val_30d)
    elif arch_val == "BREAKOUT_LONG":
        # For active ceiling breakout expansion, price must also be challenging/exceeding POC
        l4 = is_va_supported and (
            (inputs.close >= inputs.poc_30d * Decimal("0.97")) or (va_position >= Decimal("0.35"))
        )
    else:
        # All accumulation, coil, squeeze, emerging momentum, and absorption setups
        # build positions throughout the Value Area (from VAL to POC)
        l4 = is_va_supported

    # Gate L5: Archetype-Conditional Volatility Compression
    in_current_squeeze = inputs.bb_inside_kc_4h or (inputs.bb_bandwidth_4h <= Decimal("0.22"))
    in_vcp_tight_squeeze = inputs.bb_inside_kc_4h or (inputs.bb_bandwidth_4h <= Decimal("0.18"))
    recent_sqz_lookback = (
        inputs.bb_inside_kc_4h_lookback[1:7]
        if len(inputs.bb_inside_kc_4h_lookback) >= 2
        else ()
    )
    in_recent_squeeze_expansion = any(recent_sqz_lookback) and (inputs.rvol_4h >= Decimal("1.25"))

    if arch_val == "SHORT_SQUEEZE_ABSORPTION_LONG":
        l5 = True
    elif arch_val == "VCP_COMPRESSION_LONG":
        l5 = in_vcp_tight_squeeze
    elif arch_val in (
        "COIL_SPRING_LONG",
        "OI_ACCUMULATION_LONG",
        "RANGE_BOTTOM_ACCUMULATION_LONG",
    ):
        l5 = in_current_squeeze
    else:
        l5 = in_current_squeeze or in_recent_squeeze_expansion

    # Gate L6: Universal Order-Flow & Mandatory Kinetic Ignition Gate
    coil_maturity = compute_coil_maturity_score(inputs)
    volume_vacuum = is_volume_vacuum_setup(inputs)
    mature_squeeze = is_mature_squeeze_setup(inputs)
    stealth_coil_qualified = (coil_maturity >= 65) or volume_vacuum or mature_squeeze

    # 1. Universal Order-Flow Audit (Bypass eliminated: every candidate audited regardless of dist_to_ceiling)
    has_oi_metrics = (inputs.oi_delta_7d is not None or inputs.oi_delta_4h is not None)
    if has_oi_metrics:
        # Across all distances to ceiling: require non-bleeding OI: OI_7d >= -2.5% OR OI_4h >= +2.5%
        universal_oi_ok = (
            (inputs.oi_delta_7d is not None and inputs.oi_delta_7d >= Decimal("-0.025"))
            or (inputs.oi_delta_4h is not None and inputs.oi_delta_4h >= Decimal("0.025"))
        )
        if dist_to_ceiling <= Decimal("0.045"):
            # Strict resistance ceiling rule: OI_7d >= -1.0% AND RVOL >= 1.15x
            # Stealth coil bypass: mature compression coiling under ceiling doesn't require pre-breakout volume surge
            ceiling_strict_ok = stealth_coil_qualified or (
                inputs.oi_delta_7d is not None
                and inputs.oi_delta_7d >= Decimal("-0.01")
                and inputs.rvol_4h >= Decimal("1.15")
            )
            flow_confirmed = universal_oi_ok and ceiling_strict_ok
        else:
            flow_confirmed = universal_oi_ok
    else:
        # Stage 1 in-memory pre-derivatives: volume dryup, mature coil, or surge required
        flow_confirmed = stealth_coil_qualified or (inputs.rvol_4h >= Decimal("1.15")) or (inputs.vcr_4h <= Decimal("0.90"))

    # 2. Mandatory Kinetic Ignition Trigger & Stealth Coil Qualification
    # Compressed long candidates can qualify via:
    # (a) Active Kinetic Ignition (Trigger A, B, or C, or RVOL surge)
    # (b) Mature Coil Bypass (Coil Maturity Score >= 65)
    # (c) Volume Vacuum Pre-Ignition (Supply exhaustion with structural hold)
    # (d) Extended Squeeze Maturity (8+ squeeze bars holding EMA20)
    has_ignition = (
        inputs.has_kinetic_ignition
        or inputs.pocket_pivot_1h
        or inputs.micro_expansion_1h
        or inputs.of_absorption_1h
        or (inputs.rvol_4h is not None and inputs.rvol_4h >= Decimal("1.20"))
    )
    if not has_ignition and inputs.oi_delta_4h is not None and inputs.taker_buy_ratio_1h is not None:
        if (
            inputs.oi_delta_4h >= Decimal("0.035")
            and inputs.taker_buy_ratio_1h >= Decimal("1.15")
            and inputs.close >= inputs.ema20_1h
        ):
            has_ignition = True

    is_compressed_setup = (
        inputs.bb_bandwidth_4h <= Decimal("0.20")
        or inputs.bb_inside_kc_4h
        or arch_val in (
            "COIL_SPRING_LONG",
            "OI_ACCUMULATION_LONG",
            "VCP_COMPRESSION_LONG",
            "RANGE_BOTTOM_ACCUMULATION_LONG",
            "FUNDING_SQUEEZE_SPRING_LONG",
        )
    )

    if not has_oi_metrics:
        # Pre-derivatives Stage 1 structural pass: allow base supply dry-up, mature coil, or volume surge
        ignition_confirmed = (
            has_ignition
            or stealth_coil_qualified
            or (inputs.vcr_4h <= Decimal("0.90"))
            or (inputs.rvol_4h >= Decimal("1.15"))
        )
    elif is_compressed_setup:
        ignition_confirmed = has_ignition or stealth_coil_qualified
    else:
        ignition_confirmed = has_ignition or (inputs.rvol_4h >= Decimal("1.20"))

    # Fix 4: Acute Long Liquidation Veto (oi_change_1h < -2.5%)
    is_acute_oi_liquidation = (inputs.oi_delta_1h is not None and inputs.oi_delta_1h < Decimal("-0.025"))
    if is_acute_oi_liquidation:
        flow_confirmed = False

    l6 = flow_confirmed and ignition_confirmed and not is_acute_oi_liquidation

    gates = {"L1": l1, "L2": l2, "L3": l3, "L4": l4, "L5": l5, "L6": l6}
    return all(gates.values()), gates


def evaluate_short_gates(
    inputs: ShortGateInputs, archetype: SetupLabel | str | None = None
) -> tuple[bool, dict[str, bool]]:
    """Evaluate mandatory domain screening gates S1-S5 for a SHORT candidate.

    Implements the Master Specification Two-Track Precision Architecture:
      - Gate S1: Mandatory Macro Run-Up (Universal)
      - Gate S3: Anti-Capitulation & Floor Protection (Universal)
      - Gate S2 + S4 Paired Tracks:
          Track A: Upper-Range Climax Blowoff / Wyckoff UTAD
          Track B: Structural Breakdown / Macro Distribution Rollover
      - Gate S5: Bimodal Derivatives Crowding (Trapped Longs OR Short-Squeeze Flush)

    Returns (all_passed, gate_details).
    """
    # 0. Anti-Squeeze & Trend Circuit Breaker (Institutional Hard Veto)
    # Strictly reject shorts if:
    # A) Funding rate is negative (< 0) with high/expanding OI or buyer dominance (active short squeeze)
    # B) Strong taker buyer dominance (taker_buy_ratio >= 0.51) without confirmed bearish divergence
    funding_8h = inputs.funding_rate_8h or Decimal("0")
    oi_7d = inputs.oi_delta_7d or Decimal("0")
    oi_1h = inputs.oi_delta_1h or Decimal("0")
    taker_24h = inputs.taker_buy_ratio_24h or Decimal("1.0000")

    is_short_squeeze_risk = (
        (inputs.funding_rate_8h is not None and funding_8h < Decimal("0.0"))
        and (inputs.close >= inputs.ema50_4h * Decimal("0.975"))
    )
    is_buyer_dominated_trend = (
        (taker_24h >= Decimal("1.20") and inputs.close >= inputs.ema50_4h)
        or (taker_24h >= Decimal("1.05") and not inputs.has_bearish_div and inputs.close >= inputs.ema50_4h)
    )
    if is_short_squeeze_risk or is_buyer_dominated_trend:
        return False, {"S1": False, "S2": False, "S3": False, "S4": False, "S5": False}

    atr = max(inputs.atr14_4h, EPSILON)
    pos_in_range_30d = (inputs.close - inputs.low_30d) / max(
        inputs.high_30d - inputs.low_30d, EPSILON
    )

    # 1. Gate S1: Mandatory Macro Run-Up (Universal)
    # Token must have demonstrated a genuine upward expansion into highs.
    # Excludes tokens whose price has simply plummeted over the last 14 to 30 days.
    ema50_peak_excursion = (inputs.high_7d - inputs.ema50_4h) / max(inputs.ema50_4h, EPSILON)
    has_recent_surge = (inputs.ret_14d >= Decimal("0.18")) or (
        inputs.high_7d >= inputs.close * Decimal("1.08") and inputs.ret_14d >= Decimal("0.08")
    )
    has_macro_high = (
        pos_in_range_30d >= Decimal("0.60")
        and (inputs.high_30d - inputs.low_30d) / max(inputs.low_30d, EPSILON) >= Decimal("0.30")
        and inputs.ret_14d >= Decimal("-0.05")
    )
    s1 = has_recent_surge or has_macro_high

    # 2. Gate S3: Anti-Capitulation & Floor Protection (Universal)
    # Strictly forbids shorting into horizontal support floors or near 30-day lows
    low_72h = inputs.low_72h if inputs.low_72h is not None else inputs.low_30d
    dist_72h_low = (inputs.close - low_72h) / max(low_72h, EPSILON)
    dist_30d_low = (inputs.close - inputs.low_30d) / max(inputs.low_30d, EPSILON)
    downward_atr_ext = (inputs.ema20_4h - inputs.close) / atr

    s3 = (
        (dist_72h_low >= Decimal("0.04"))
        and (dist_30d_low >= Decimal("0.10"))
        and (inputs.rsi_1h >= Decimal("32.0"))
        and (inputs.rsi_4h >= Decimal("38.0"))
        and (pos_in_range_30d >= Decimal("0.45"))
        and (downward_atr_ext <= Decimal("2.00"))
    )

    # 3. Gate S2 + S4 Paired Two-Track Evaluation
    high_24h = (
        inputs.high_24h
        if inputs.high_24h is not None
        else (inputs.close + Decimal("0.80") * atr)
    )
    rejected_from_high = inputs.close <= (high_24h - Decimal("0.50") * atr)

    # Upper wick ratio on 4H candle
    open_4h = inputs.open_4h if inputs.open_4h is not None else inputs.close
    high_4h = inputs.high_4h if inputs.high_4h is not None else high_24h
    low_4h = inputs.low_4h if inputs.low_4h is not None else low_72h
    span_4h = max(high_4h - low_4h, EPSILON)
    upper_wick_ratio_4h = (high_4h - max(open_4h, inputs.close)) / span_4h

    rsi_1d = inputs.rsi_1d if inputs.rsi_1d is not None else inputs.rsi_4h
    is_climax_blowoff = (
        inputs.has_climax_blowoff
        or ((rsi_1d >= Decimal("72.0")) and (upper_wick_ratio_4h >= Decimal("0.35")))
    )

    macd_prev = (
        inputs.macd_hist_4h_prev
        if inputs.macd_hist_4h_prev is not None
        else inputs.macd_hist_4h
    )
    is_utad_divergence = inputs.has_bearish_div and (inputs.macd_hist_4h < macd_prev)

    # Track A: Upper-Range Climax Blowoff / Wyckoff UTAD
    # Strictly require either price breaking below 4H EMA20 OR verified taker seller dominance (<= 0.92)
    track_a_pass = (
        (pos_in_range_30d >= Decimal("0.65"))
        and rejected_from_high
        and (is_climax_blowoff or is_utad_divergence)
        and (inputs.close <= inputs.ema20_4h or inputs.taker_buy_ratio_24h <= Decimal("0.92"))
    )

    # Track B: Structural Breakdown / Macro Distribution Rollover
    # Must be actively retesting an overhead resistance ceiling (EMA20, EMA50, or POC) within 5.0%
    overhead_levels = [
        lvl for lvl in (inputs.ema20_4h, inputs.ema50_4h, inputs.poc_30d)
        if lvl is not None and lvl >= inputs.close * Decimal("0.985")
    ]
    if overhead_levels:
        closest_ceiling = min(overhead_levels)
        dist_to_ceiling = (closest_ceiling - inputs.close) / max(inputs.close, EPSILON)
        is_active_retest = dist_to_ceiling <= Decimal("0.050")
    else:
        dist_to_ceiling = Decimal("1.0")
        is_active_retest = False

    momentum_and_orderflow_decay = (
        (inputs.macd_hist_4h <= Decimal("0.05"))
        and (
            inputs.macd_hist_4h <= macd_prev
            or inputs.taker_buy_ratio_24h <= Decimal("1.00")
            or inputs.close <= inputs.ema20_4h
        )
    )
    track_b_pass = is_active_retest and momentum_and_orderflow_decay and (pos_in_range_30d >= Decimal("0.45"))

    # S2 and S4 pass if either Track A or Track B passes
    s2_s4_passed = track_a_pass or track_b_pass
    s2 = s2_s4_passed
    s4 = s2_s4_passed

    # 4. Gate S5: Bimodal Derivatives Crowding (Trapped Longs OR Short-Squeeze Flush)
    if inputs.funding_rate_8h is None and inputs.oi_delta_7d is None:
        s5 = True
    else:
        funding_8h = inputs.funding_rate_8h or Decimal("0")
        basis = inputs.basis_bps or Decimal("0")
        oi_7d = inputs.oi_delta_7d or Decimal("0")
        oi_1h = inputs.oi_delta_1h or Decimal("0")
        taker_1h = inputs.taker_buy_ratio_1h or Decimal("1.0000")

        trapped_longs = (
            (funding_8h >= Decimal("0.0001") or basis >= Decimal("12.0")) and (oi_7d >= Decimal("0.15"))
        ) or (
            (funding_8h >= Decimal("0.00005") or basis >= Decimal("4.0"))
            and (oi_7d >= Decimal("0.0") or taker_1h <= Decimal("0.92"))
        ) or (
            inputs.taker_buy_ratio_24h <= Decimal("0.92")
        )
        squeeze_flush = (
            (funding_8h <= Decimal("-0.0003"))
            and (oi_7d >= Decimal("0.25"))
            and (oi_1h <= Decimal("-0.015"))
            and (taker_1h <= Decimal("0.85"))
        ) or (
            (funding_8h <= Decimal("-0.00015")) and (taker_1h <= Decimal("0.92"))
        )
        s5 = trapped_longs or squeeze_flush

    all_passed = s1 and s3 and s2_s4_passed and s5
    gates = {"S1": s1, "S2": s2, "S3": s3, "S4": s4, "S5": s5}
    return all_passed, gates
