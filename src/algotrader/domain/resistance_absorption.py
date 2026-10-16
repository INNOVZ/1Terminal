from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

EPSILON = Decimal("1e-8")


@dataclass(frozen=True, slots=True)
class ResistanceAbsorptionSnapshot:
    """Inputs for evaluating the 5 institutional breakout confirmation pillars."""

    cvd_zscore_24h: float | Decimal
    taker_buy_ratio_4h: float | Decimal
    oi_delta_7d: float | Decimal | None
    oi_delta_4h: float | Decimal | None
    funding_bps: float | Decimal
    low_4h: Decimal
    low_4h_prev: Decimal
    low_4h_prev2: Decimal
    close: Decimal
    ema20_1h: Decimal
    atr14_1h: Decimal
    ret_24h: Decimal
    vpvr_bins: Sequence[Any]
    vah_30d: Decimal
    high_30d: Decimal
    poc_bin_volume: Decimal


def compute_resistance_absorption(
    snapshot: ResistanceAbsorptionSnapshot | None = None,
    *,
    cvd_zscore_24h: float | Decimal = 0.0,
    taker_buy_ratio_4h: float | Decimal = 0.50,
    oi_delta_7d: float | Decimal | None = None,
    oi_delta_4h: float | Decimal | None = None,
    funding_bps: float | Decimal = 0.0,
    low_4h: Decimal = Decimal("0"),
    low_4h_prev: Decimal = Decimal("0"),
    low_4h_prev2: Decimal = Decimal("0"),
    close: Decimal = Decimal("0"),
    ema20_1h: Decimal = Decimal("0"),
    atr14_1h: Decimal = Decimal("0"),
    ret_24h: Decimal = Decimal("0"),
    vpvr_bins: Sequence[Any] = (),
    vah_30d: Decimal = Decimal("0"),
    high_30d: Decimal = Decimal("0"),
    poc_bin_volume: Decimal = Decimal("0"),
) -> tuple[float, str, dict[str, bool]]:
    """Evaluates 5 institutional breakout confirmation pillars on a 0.0 to 100.0 scale.

    Returns:
        (resistance_absorption_score, breakout_readiness_status, confirmation_flags)
    """
    if snapshot is not None:
        cvd_zscore_24h = snapshot.cvd_zscore_24h
        taker_buy_ratio_4h = snapshot.taker_buy_ratio_4h
        oi_delta_7d = snapshot.oi_delta_7d
        oi_delta_4h = snapshot.oi_delta_4h
        funding_bps = snapshot.funding_bps
        low_4h = snapshot.low_4h
        low_4h_prev = snapshot.low_4h_prev
        low_4h_prev2 = snapshot.low_4h_prev2
        close = snapshot.close
        ema20_1h = snapshot.ema20_1h
        atr14_1h = snapshot.atr14_1h
        ret_24h = snapshot.ret_24h
        vpvr_bins = snapshot.vpvr_bins
        vah_30d = snapshot.vah_30d
        high_30d = snapshot.high_30d
        poc_bin_volume = snapshot.poc_bin_volume

    # 1. Pillar 1: CVD Order-Flow Absorption (25 Points Max)
    cvd_z = float(cvd_zscore_24h)
    taker_ratio = float(taker_buy_ratio_4h)
    if cvd_z >= 0.50 and taker_ratio >= 0.53:
        pts_cvd = 25.0
    elif cvd_z >= 0.0 and taker_ratio >= 0.51:
        pts_cvd = 18.0
    elif cvd_z >= -0.20:
        pts_cvd = 8.0
    else:
        pts_cvd = 0.0

    # 2. Pillar 2: Open Interest Expansion Fuel (25 Points Max)
    oi_7d = float(oi_delta_7d) if oi_delta_7d is not None else 0.0
    oi_4h = float(oi_delta_4h) if oi_delta_4h is not None else 0.0
    if oi_7d >= 0.05 and oi_4h >= 0.015:
        pts_oi = 25.0
    elif oi_7d >= 0.03 and oi_4h >= 0.0:
        pts_oi = 18.0
    elif oi_7d >= 0.0:
        pts_oi = 10.0
    else:
        pts_oi = 0.0

    # 3. Pillar 3: Trapped Shorts / Funding Squeeze Fuel (20 Points Max)
    f_bps = float(funding_bps)
    # If passed as decimal funding rate (e.g. -0.0007), convert to basis points (-7.0)
    if abs(f_bps) <= 0.005 and f_bps != 0.0:
        f_bps = f_bps * 10000.0

    if f_bps <= -1.0:
        pts_funding = 20.0
    elif -1.0 < f_bps <= 1.01:
        pts_funding = 15.0
    elif 1.01 < f_bps <= 2.50:
        pts_funding = 6.0
    else:
        pts_funding = 0.0

    # 4. Pillar 4: Ceiling Approach Geometry — Ascending Shelf vs. V-Spike (15 Points Max)
    higher_lows_4h = (low_4h >= low_4h_prev) and (low_4h_prev >= low_4h_prev2 * Decimal("0.995"))
    tight_1h_coil = ((close - ema20_1h) / max(atr14_1h, EPSILON)) <= Decimal("1.25")
    not_v_spike = abs(ret_24h) <= Decimal("0.12")

    if higher_lows_4h and tight_1h_coil and not_v_spike:
        pts_geometry = 15.0
    elif tight_1h_coil and not_v_spike:
        pts_geometry = 10.0
    else:
        pts_geometry = 0.0

    # 5. Pillar 5: VPVR Overhead Liquidity Vacuum / LVN Check (15 Points Max)
    v_30d = Decimal(str(vah_30d))
    h_30d = Decimal(str(high_30d))
    poc_vol = Decimal(str(poc_bin_volume))
    overhead_bins = [
        Decimal(str(getattr(b, "volume", b[1] if isinstance(b, tuple | list) else 0)))
        for b in vpvr_bins
        if v_30d < Decimal(str(getattr(b, "price", b[0] if isinstance(b, tuple | list) else 0))) <= h_30d
    ]
    if overhead_bins:
        overhead_avg_bin_vol = sum(overhead_bins) / Decimal(len(overhead_bins))
    else:
        overhead_avg_bin_vol = Decimal("0.0")

    overhead_density_ratio = float(overhead_avg_bin_vol / max(poc_vol, EPSILON))
    if overhead_density_ratio <= 0.35:
        pts_vpvr = 15.0
    elif overhead_density_ratio <= 0.55:
        pts_vpvr = 9.0
    else:
        pts_vpvr = 0.0

    # Composite Score
    score = min(100.0, max(0.0, round(pts_cvd + pts_oi + pts_funding + pts_geometry + pts_vpvr, 1)))

    # Universal Invariant 4 Status Mapping:
    # 1. "BREAKOUT_CONFIRMED" (Green): resistance_absorption_score >= 65.0 and oi_delta_7d >= 0.0 and cvd_zscore_24h > -0.25
    # 2. "HIGH_REJECTION_RISK" (Red): resistance_absorption_score < 45.0 or oi_delta_7d < -0.01 or (cvd_zscore_24h <= -1.0 and dist_to_vah <= 0.03)
    # 3. "RETEST_ONLY_WAIT_FOR_DIP" (Amber): All other valid candidates (e.g. scores 45.0 to 64.9 with non-bleeding 7d OI)
    dist_to_vah = float((Decimal(str(vah_30d)) - Decimal(str(close))) / max(Decimal(str(close)), EPSILON))

    if score < 45.0 or oi_7d < -0.01 or (cvd_z <= -1.0 and dist_to_vah <= 0.03):
        status = "HIGH_REJECTION_RISK"
        score = min(score, 44.0)
    elif score >= 65.0 and oi_7d >= 0.0 and cvd_z > -0.25:
        status = "BREAKOUT_CONFIRMED"
    else:
        status = "RETEST_ONLY_WAIT_FOR_DIP"

    flags = {
        "pillar1_cvd_absorption": pts_cvd >= 18.0,
        "pillar2_oi_expansion": pts_oi >= 10.0,
        "pillar3_funding_squeeze": pts_funding >= 15.0,
        "pillar4_ascending_geometry": pts_geometry >= 10.0,
        "pillar5_lvn_vacuum": pts_vpvr >= 9.0,
    }

    return score, status, flags
