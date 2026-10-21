from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from algotrader.domain.crypto_screener_models import SetupLabel


@dataclass(frozen=True, slots=True)
class LongArchetypeFeatures:
    close: Decimal
    low_24h: Decimal
    ema50_4h: Decimal
    poc_30d: Decimal
    vah_30d: Decimal
    atr14_4h: Decimal
    vcr_4h: Decimal
    oi_delta_7d: Decimal | None
    oi_delta_4h: Decimal | None
    ret_7d: Decimal
    bb_inside_kc_4h: bool
    bb_bandwidth_4h: Decimal
    local_swing_high_4h: Decimal
    rvol_4h: Decimal
    norm_momentum_1h: Decimal
    btc_residual_alpha_24h: Decimal
    macd_hist_4h: Decimal
    macd_hist_4h_prev: Decimal
    macd_hist_1h: Decimal
    taker_buy_ratio_4h: Decimal
    range_height_30d: Decimal = Decimal("0.50")
    pos_in_range_30d: Decimal = Decimal("0.40")
    dist_to_ceiling: Decimal = Decimal("0.25")
    val_30d: Decimal = Decimal("0")
    funding_rate_8h: Decimal | None = None
    cvd_24h: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class ShortArchetypeFeatures:
    close: Decimal
    high_24h: Decimal
    high_30d: Decimal
    ema50_4h: Decimal
    poc_30d: Decimal
    atr14_4h: Decimal
    has_bearish_div: bool
    rsi_4h: Decimal
    rsi_1d: Decimal
    rsi_1w: Decimal
    oi_delta_7d: Decimal | None
    upper_wick_ratio_4h: Decimal
    max_ret_30d: Decimal
    taker_buy_ratio_24h: Decimal
    macd_hist_4h: Decimal
    pos_in_range_30d: Decimal = Decimal("0.70")
    macd_hist_4h_prev: Decimal = Decimal("0")
    runup_30d: Decimal = Decimal("0")
    ret_14d: Decimal = Decimal("0")


def classify_long_archetype(features: LongArchetypeFeatures) -> SetupLabel | None:
    """Classify candidate into highest-priority Long Archetype (0 to 6).

    Returns SetupLabel if matched, else None.
    """
    atr = max(features.atr14_4h, Decimal("1e-8"))

    # 0. SHORT_SQUEEZE_ABSORPTION_LONG (Crowded Short Squeeze & Order Flow Absorption)
    funding_8h = features.funding_rate_8h
    oi_7d = features.oi_delta_7d or Decimal("0")
    oi_4h = features.oi_delta_4h or Decimal("0")
    is_funding_crowded_short = funding_8h is not None and funding_8h <= Decimal("0.00005")
    is_oi_expanding = oi_7d >= Decimal("0.15") or oi_4h >= Decimal("0.035")
    has_buyer_absorption = features.cvd_24h > Decimal("0") or features.taker_buy_ratio_4h >= Decimal("0.50")
    holding_structure = (
        features.close >= features.ema50_4h * Decimal("0.975")
        or features.close >= features.poc_30d * Decimal("0.975")
    )
    if (
        is_funding_crowded_short
        and is_oi_expanding
        and has_buyer_absorption
        and holding_structure
    ):
        return SetupLabel.SHORT_SQUEEZE_ABSORPTION_LONG

    # 1. COIL_SPRING_LONG (Wyckoff Phase C Shakeout)
    spring_threshold = min(features.ema50_4h, features.poc_30d) - Decimal("0.10") * atr
    reclaim_threshold = max(features.ema50_4h, features.poc_30d * Decimal("0.99"))
    if (
        features.low_24h < spring_threshold
        and features.close >= reclaim_threshold
        and features.vcr_4h <= Decimal("0.90")
    ):
        return SetupLabel.COIL_SPRING_LONG

    # Directional momentum helper inside compression
    is_mom_up = (features.macd_hist_4h >= features.macd_hist_4h_prev) or (features.macd_hist_4h > Decimal("0"))

    # 2. VCP_COMPRESSION_LONG (Mark Minervini Volatility Contraction Pattern)
    is_vcp_compressed = features.bb_inside_kc_4h or (features.bb_bandwidth_4h <= Decimal("0.18"))
    has_supply_exhaustion = (features.vcr_4h <= Decimal("1.10")) or (features.rvol_4h <= Decimal("1.15"))
    if (
        features.range_height_30d <= Decimal("0.45")
        and is_vcp_compressed
        and has_supply_exhaustion
        and is_mom_up
        and features.close >= features.ema50_4h * Decimal("0.980")
        and features.dist_to_ceiling <= Decimal("0.12")
        and features.pos_in_range_30d > Decimal("0.60")
    ):
        return SetupLabel.VCP_COMPRESSION_LONG

    # 3. OI_ACCUMULATION_LONG (Smart Money Absorption)
    oi_7d = features.oi_delta_7d or Decimal("0")
    is_compressed = features.bb_inside_kc_4h or (features.bb_bandwidth_4h <= Decimal("0.22"))
    if (
        Decimal("0.08") <= oi_7d <= Decimal("0.60")
        and is_compressed
        and abs(features.ret_7d) <= Decimal("0.12")
        and is_mom_up
    ):
        return SetupLabel.OI_ACCUMULATION_LONG

    # 4. RANGE_BOTTOM_ACCUMULATION_LONG (Wyckoff Value Area Base Accumulation)
    if (
        features.close >= features.val_30d * Decimal("0.98")
        and features.range_height_30d <= Decimal("0.55")
        and (is_compressed or features.bb_bandwidth_4h <= Decimal("0.24"))
        and is_mom_up
        and features.close >= features.ema50_4h * Decimal("0.975")
        and features.pos_in_range_30d <= Decimal("0.60")
        and features.vcr_4h <= Decimal("1.20")
        and features.taker_buy_ratio_4h >= Decimal("0.48")
    ):
        return SetupLabel.RANGE_BOTTOM_ACCUMULATION_LONG

    # 5. BREAKOUT_LONG (Volume & OI Expansion)
    oi_4h = features.oi_delta_4h or Decimal("0")
    at_or_above_resistance = (features.close >= features.local_swing_high_4h) or (
        features.close >= features.vah_30d
    )
    has_breakout_volume = (
        (features.rvol_4h >= Decimal("1.25") and oi_4h >= Decimal("0.0"))
        or (features.rvol_4h >= Decimal("2.0"))
    )
    if (
        at_or_above_resistance
        and has_breakout_volume
        and features.norm_momentum_1h > Decimal("0")
    ):
        return SetupLabel.BREAKOUT_LONG

    # 6. MOMENTUM_EMERGING_LONG (Residual Alpha Regime Shift)
    if (
        features.btc_residual_alpha_24h >= Decimal("0.03")
        and features.macd_hist_4h > features.macd_hist_4h_prev
        and features.macd_hist_1h > Decimal("0")
        and features.taker_buy_ratio_4h >= Decimal("0.535")
    ):
        return SetupLabel.MOMENTUM_EMERGING_LONG

    # 7. Structural Market-Profile Affinity Fallback
    # Classifies valid consolidation into closest structural archetype only if compressed
    if at_or_above_resistance and has_breakout_volume:
        return SetupLabel.BREAKOUT_LONG
    if oi_7d >= Decimal("0.05") and is_compressed and is_mom_up:
        return SetupLabel.OI_ACCUMULATION_LONG
    if is_compressed or features.bb_bandwidth_4h <= Decimal("0.22"):
        if features.pos_in_range_30d > Decimal("0.60") and has_supply_exhaustion and is_mom_up:
            return SetupLabel.VCP_COMPRESSION_LONG
        is_val_support_holding = (
            features.close >= features.val_30d * Decimal("0.98")
            and features.close >= features.ema50_4h * Decimal("0.975")
        )
        if (
            features.pos_in_range_30d <= Decimal("0.60")
            and is_val_support_holding
            and is_mom_up
            and features.taker_buy_ratio_4h >= Decimal("0.48")
        ):
            return SetupLabel.RANGE_BOTTOM_ACCUMULATION_LONG
    return None


def classify_short_archetype(features: ShortArchetypeFeatures) -> SetupLabel | None:
    """Classify candidate into highest-priority Short Archetype per Section 3.2.

    Strict Institutional Rules:
      1. Bottom Grinder Exclusion:
         No short setup is valid if the token is sitting in the bottom of its 30-day range
         (pos_in_range_30d < 0.40). Shorting into support floors or ATL is strictly prohibited.
      2. Valid Archetypes:
         - UTAD_DIVERGENCE_SHORT: Wyckoff Phase C Upthrust after Distribution (Sweep of 30d high + Bearish Div)
         - EXHAUSTION_REVERSAL_SHORT: Multi-Timeframe Climax Overbought Blowoff (Violent run-up + Extreme RSI + Wick)
         - DISTRIBUTION_SHORT: Macro Top-Shelf Rollover (Prior markup + breaking below 50 EMA & POC + negative MACD)
         - VCP_DISTRIBUTION_SHORT: Volatility Contraction Distribution Breakdown (Prior markup + breaking 50 EMA)
      3. No Permissive Fallback:
         If no strict archetype matches, returns None. Bottom-scraping dead tokens are never labeled shorts.
    """
    atr = max(features.atr14_4h, Decimal("1e-8"))

    # Hard Exclusion: tokens languishing in the lower range cannot be shorted
    if features.pos_in_range_30d < Decimal("0.40"):
        return None

    # Prior markup verification
    has_prior_markup = (
        features.runup_30d >= Decimal("0.35")
        or features.ret_14d >= Decimal("0.22")
        or features.max_ret_30d >= Decimal("0.35")
    )

    # 1. UTAD_DIVERGENCE_SHORT (Wyckoff Phase C Upthrust After Distribution)
    sweep_test = features.high_24h >= (features.high_30d * Decimal("0.975"))
    rejection = features.close <= (features.high_24h - Decimal("0.45") * atr)
    is_macd_rolling = (
        features.macd_hist_4h <= features.macd_hist_4h_prev
        if features.macd_hist_4h_prev != Decimal("0")
        else True
    )
    if (
        features.pos_in_range_30d >= Decimal("0.60")
        and sweep_test
        and rejection
        and features.has_bearish_div
        and is_macd_rolling
    ):
        return SetupLabel.UTAD_DIVERGENCE_SHORT

    # 2. EXHAUSTION_REVERSAL_SHORT (Multi-Timeframe Overbought Climax)
    oi_7d = features.oi_delta_7d or Decimal("0")
    mtf_rsi_overbought = (
        features.rsi_1d >= Decimal("74.0") or features.rsi_1w >= Decimal("74.0") or features.rsi_4h >= Decimal("74.0")
    )
    has_climax_runup = (
        features.runup_30d >= Decimal("0.40")
        or features.ret_14d >= Decimal("0.25")
        or features.max_ret_30d >= Decimal("0.50")
    )
    if (
        features.pos_in_range_30d >= Decimal("0.60")
        and features.close <= (features.high_24h - Decimal("0.45") * atr)
        and mtf_rsi_overbought
        and features.upper_wick_ratio_4h >= Decimal("0.40")
        and has_climax_runup
        and (oi_7d >= Decimal("0.15") or features.taker_buy_ratio_24h <= Decimal("0.49"))
    ):
        return SetupLabel.EXHAUSTION_REVERSAL_SHORT

    # 3. DISTRIBUTION_SHORT (Macro Rollover Breakdown from Upper Shelf)
    is_macd_decaying = (
        features.macd_hist_4h < Decimal("0.0")
        and (
            features.macd_hist_4h <= features.macd_hist_4h_prev
            if features.macd_hist_4h_prev != Decimal("0")
            else True
        )
    )
    if (
        has_prior_markup
        and features.pos_in_range_30d >= Decimal("0.45")
        and features.close < features.ema50_4h
        and features.close < features.poc_30d
        and is_macd_decaying
        and features.taker_buy_ratio_24h <= Decimal("0.48")
    ):
        return SetupLabel.DISTRIBUTION_SHORT

    # 4. VCP_DISTRIBUTION_SHORT (Volatility Contraction Breakdown below 50 EMA from Upper Shelf)
    if (
        has_prior_markup
        and features.pos_in_range_30d >= Decimal("0.45")
        and features.close <= features.ema50_4h
        and is_macd_rolling
        and features.taker_buy_ratio_24h <= Decimal("0.49")
    ):
        return SetupLabel.VCP_DISTRIBUTION_SHORT

    # Strictly no fallback to COIL_BREAKDOWN_SHORT or VCP_DISTRIBUTION_SHORT for unaligned tokens
    return None


