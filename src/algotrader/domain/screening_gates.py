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
    taker_buy_ratio_4h: Decimal = Decimal("0.5")
    # High-tight flag continuation inputs (Section 4)
    high_4h: Decimal | None = None
    low_4h: Decimal | None = None
    atr14_1h: Decimal | None = None
    funding_rate_8h: Decimal | None = None
    oi_delta_24h: Decimal | None = None


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
    taker_buy_ratio_24h: Decimal
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


def evaluate_long_gates(
    inputs: LongGateInputs, archetype: SetupLabel | str | None = None
) -> tuple[bool, dict[str, bool]]:
    """Evaluate mandatory domain screening gates L1-L6 for a LONG candidate.

    Returns (all_passed, gate_details).
    """
    atr = max(inputs.atr14_4h, EPSILON)
    natr_4h = inputs.atr14_4h / max(inputs.close, EPSILON)

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
    l1 = is_standard_trend or is_wyckoff_spring

    # Gate L2: Anti-Chasing 2.0 & High-Tight Flag Continuation
    atr_ext_4h = (inputs.close - inputs.ema20_4h) / atr
    standard_l2 = (
        inputs.excursion_72h <= Decimal("0.36")
        and (Decimal("-0.08") <= inputs.ret_24h <= Decimal("0.14"))
        and (inputs.rsi_1h <= Decimal("76.0"))
        and (inputs.rsi_4h <= Decimal("70.0"))
        and (atr_ext_4h <= Decimal("2.25"))
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
            and (inputs.taker_buy_ratio_4h >= Decimal("0.52"))
        )
        flag_l2 = (
            is_high_tight_flag
            and (inputs.excursion_72h <= Decimal("0.60"))
            and (inputs.ret_24h <= Decimal("0.35"))
            and (inputs.rsi_1h <= Decimal("78.0"))
        )
    l2 = standard_l2 or flag_l2

    # Gate L3: Multi-Week Base Duration & Ceiling Proximity
    range_height_30d = (inputs.high_30d - inputs.low_30d) / max(inputs.low_30d, EPSILON)
    dist_to_ceiling = (inputs.high_30d - inputs.close) / max(inputs.close, EPSILON)
    standard_l3 = (range_height_30d <= Decimal("0.65")) and (
        Decimal("-0.03") <= dist_to_ceiling <= Decimal("0.35")
    )
    flag_l3 = flag_l2 and (range_height_30d <= Decimal("1.20")) and (
        Decimal("-0.05") <= dist_to_ceiling <= Decimal("0.35")
    )
    l3 = standard_l3 or flag_l3

    # Gate L4: 30-Day VPVR Value Area Acceptance
    va_span = max(inputs.vah_30d - inputs.val_30d, EPSILON)
    va_position = (inputs.close - inputs.val_30d) / va_span
    l4 = (inputs.close >= inputs.poc_30d * Decimal("0.985")) and (va_position >= Decimal("0.25"))

    # Gate L5: Archetype-Conditional Volatility Compression
    in_current_squeeze = inputs.bb_inside_kc_4h or (inputs.bb_bandwidth_4h <= Decimal("0.24"))
    recent_sqz_lookback = (
        inputs.bb_inside_kc_4h_lookback[1:7]
        if len(inputs.bb_inside_kc_4h_lookback) >= 2
        else ()
    )
    in_recent_squeeze_expansion = any(recent_sqz_lookback) and (inputs.rvol_4h >= Decimal("1.25"))

    arch_val = archetype.value if isinstance(archetype, SetupLabel) else str(archetype or "")
    if arch_val in ("COIL_SPRING_LONG", "OI_ACCUMULATION_LONG"):
        l5 = in_current_squeeze
    else:
        l5 = in_current_squeeze or in_recent_squeeze_expansion

    # Gate L6: S/R Ceiling Absorption & Bull-Trap Filter
    if dist_to_ceiling <= Decimal("0.045"):
        oi_7d = inputs.oi_delta_7d if inputs.oi_delta_7d is not None else Decimal("0")
        oi_4h = inputs.oi_delta_4h if inputs.oi_delta_4h is not None else Decimal("0")
        l6 = (
            oi_7d >= Decimal("0.0")
            and oi_4h >= Decimal("0.015")
            and inputs.rvol_4h >= Decimal("1.20")
            and inputs.taker_buy_ratio_4h >= Decimal("0.52")
        )
    else:
        l6 = True

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
    atr = max(inputs.atr14_4h, EPSILON)

    # 1. Gate S1: Mandatory Macro Run-Up
    ema50_peak_excursion = (inputs.high_7d - inputs.ema50_4h) / max(inputs.ema50_4h, EPSILON)
    runup_30d = (
        inputs.runup_30d
        if inputs.runup_30d is not None
        else (inputs.high_30d - inputs.low_30d) / max(inputs.low_30d, EPSILON)
    )
    s1 = (runup_30d >= Decimal("0.35")) or (
        inputs.ret_14d >= Decimal("0.22") and ema50_peak_excursion >= Decimal("0.15")
    )

    # 2. Gate S3: Anti-Capitulation & Floor Protection (Universal)
    low_72h = inputs.low_72h if inputs.low_72h is not None else inputs.low_30d
    dist_72h_low = (inputs.close - low_72h) / max(low_72h, EPSILON)
    pos_in_range_30d = (inputs.close - inputs.low_30d) / max(
        inputs.high_30d - inputs.low_30d, EPSILON
    )
    downward_atr_ext = (inputs.ema20_4h - inputs.close) / atr

    s3 = (
        (dist_72h_low > Decimal("0.04"))
        and (inputs.rsi_1h >= Decimal("30.0"))
        and (inputs.rsi_4h >= Decimal("38.0"))
        and (pos_in_range_30d >= Decimal("0.45"))
        and (downward_atr_ext <= Decimal("1.75"))
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
        or ((rsi_1d >= Decimal("76.0")) and (upper_wick_ratio_4h >= Decimal("0.45")))
    )

    macd_prev = (
        inputs.macd_hist_4h_prev
        if inputs.macd_hist_4h_prev is not None
        else inputs.macd_hist_4h
    )
    is_utad_divergence = inputs.has_bearish_div and (inputs.macd_hist_4h < macd_prev)

    # Track A: Upper-Range Climax Blowoff / Wyckoff UTAD
    track_a_pass = (
        (pos_in_range_30d >= Decimal("0.65"))
        and rejected_from_high
        and (is_climax_blowoff or is_utad_divergence)
    )

    # Track B: Structural Breakdown / Macro Distribution Rollover
    below_key_structure = (inputs.close < inputs.ema50_4h) and (inputs.close < inputs.poc_30d)
    momentum_and_orderflow_decay = (
        (inputs.macd_hist_4h < Decimal("0.0"))
        and (inputs.macd_hist_4h <= macd_prev)
        and (inputs.taker_buy_ratio_24h <= Decimal("0.47"))
    )
    track_b_pass = below_key_structure and momentum_and_orderflow_decay

    # S2 and S4 pass if either Track A or Track B passes
    s2_s4_passed = track_a_pass or track_b_pass
    s2 = track_a_pass or (pos_in_range_30d >= Decimal("0.45") and below_key_structure)
    s4 = track_a_pass or momentum_and_orderflow_decay

    # 4. Gate S5: Bimodal Derivatives Crowding (Trapped Longs OR Short-Squeeze Flush)
    if inputs.funding_rate_8h is None and inputs.oi_delta_7d is None:
        s5 = True
    else:
        funding_8h = inputs.funding_rate_8h or Decimal("0")
        basis = inputs.basis_bps or Decimal("0")
        oi_7d = inputs.oi_delta_7d or Decimal("0")
        oi_1h = inputs.oi_delta_1h or Decimal("0")
        taker_1h = inputs.taker_buy_ratio_1h or Decimal("0.5")

        trapped_longs = (funding_8h >= Decimal("0.0001") or basis >= Decimal("12.0")) and (
            oi_7d >= Decimal("0.15")
        )
        squeeze_flush = (
            (funding_8h <= Decimal("-0.0003"))
            and (oi_7d >= Decimal("0.25"))
            and (oi_1h <= Decimal("-0.015"))
            and (taker_1h <= Decimal("0.46"))
        )
        s5 = trapped_longs or squeeze_flush

    all_passed = s1 and s3 and s2_s4_passed and s5
    gates = {"S1": s1, "S2": s2, "S3": s3, "S4": s4, "S5": s5}
    return all_passed, gates
