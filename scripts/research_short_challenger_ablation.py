"""Offline ablation & validation for versioned Short Challenger rules on Nov 8-10 and Nov 12-14.

Precomputes 1H/4H indicators (caching 4H VPVR/divergences by 4H bar count) once per
(cohort, symbol, hour), then evaluates baseline and challenger variants H1-H4
with strict causal cutoffs and 1H-bar bracket fill + stop/target simulation.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from algotrader.domain.archetypes import ShortArchetypeFeatures, classify_short_archetype
from algotrader.domain.crypto_screener_models import SetupLabel
from algotrader.domain.execution_brackets import InvalidExecutionBracket, build_short_bracket
from algotrader.domain.indicators import (
    atr,
    confirmed_macd_divergences,
    confirmed_rsi_divergences,
    ema,
    macd,
    resample_candles,
    rsi,
    snapshot as indicator_snapshot,
    taker_buy_ratio_from_candles,
    volume_profile,
)
from algotrader.domain.models import Candle, DerivativesSnapshot
from algotrader.domain.screening_gates import EPSILON, ShortGateInputs, evaluate_short_gates
from scripts.research_top_gainer_short_study import (
    RAW_CACHE_DIR,
    build_derivatives_at,
    infer_tick,
    load_cohorts,
    make_instrument,
    parse_candles,
)

ZERO = Decimal("0")
OUT_PATH = ROOT / "docs" / "security" / "evidence" / "2026-11-16-short-challenger-ablation.json"


def simulate_bracket(
    candles_1h: list[Candle],
    signal_dt: datetime,
    entry: float,
    stop: float,
    t1: float,
    t2: float,
    horizon_hours: int = 48,
) -> dict[str, Any]:
    """Simulate 1H-bar fill and stop-vs-target ordering (conservative: stop wins tie)."""
    fwd = [c for c in candles_1h if c.opened_at >= signal_dt][:horizon_hours]
    if not fwd:
        return {"filled": False, "outcome": "no_bars"}

    eff_stop = min(stop, entry * 1.06)
    fill_idx = None
    for idx, bar in enumerate(fwd):
        if float(bar.high) >= entry >= float(bar.low) or (idx == 0 and float(bar.open) <= entry <= float(bar.high)):
            fill_idx = idx
            break
    if fill_idx is None:
        return {"filled": False, "outcome": "unfilled"}

    risk = eff_stop - entry
    if risk <= 0:
        return {"filled": False, "outcome": "invalid_risk"}
    hit_t1 = False
    for bar in fwd[fill_idx:]:
        hi, lo = float(bar.high), float(bar.low)
        if hi >= eff_stop:
            return {
                "filled": True,
                "outcome": "t1_then_stop" if hit_t1 else "stopped",
                "r_multiple": round((entry - t1) / risk * 0.5 - 0.5, 2) if hit_t1 else -1.0,
                "return_pct": round(((entry - eff_stop) / entry) * 100.0, 2),
            }
        if lo <= t2:
            return {
                "filled": True,
                "outcome": "target_2",
                "r_multiple": round((entry - t2) / risk, 2),
                "return_pct": round(((entry - t2) / entry) * 100.0, 2),
            }
        if lo <= t1:
            hit_t1 = True

    last_c = float(fwd[-1].close)
    return {
        "filled": True,
        "outcome": "t1_partial_open" if hit_t1 else "open_at_horizon",
        "r_multiple": round((entry - last_c) / risk, 2),
        "return_pct": round(((entry - last_c) / entry) * 100.0, 2),
    }


def evaluate_precomputed(
    feat: dict[str, Any],
    *,
    h1_checklist_parity: bool = False,
    h2_bounce_short: bool = False,
    h3_s2_s4_4h_taker: bool = False,
    h4_mild_neg_funding: bool = False,
) -> dict[str, Any] | None:
    close: Decimal = feat["close"]
    ema20_4h: Decimal = feat["ema20_4h"]
    ema50_4h: Decimal = feat["ema50_4h"]
    atr14_4h: Decimal = feat["atr14_4h"]
    poc_price: Decimal = feat["poc_price"]
    vah_price: Decimal = feat["vah_price"]
    val_price: Decimal = feat["val_price"]
    high_30d: Decimal = feat["high_30d"]
    low_30d: Decimal = feat["low_30d"]
    range_height_30d: Decimal = feat["range_height_30d"]
    pos_in_range: Decimal = feat["pos_in_range"]
    high_24h: Decimal = feat["high_24h"]
    high_7d: Decimal = feat["high_7d"]
    low_72h: Decimal = feat["low_72h"]
    ret_24h: Decimal = feat["ret_24h"]
    ret_7d: Decimal = feat["ret_7d"]
    ret_14d: Decimal = feat["ret_14d"]
    rsi_1h: Decimal = feat["rsi_1h"]
    rsi_4h: Decimal = feat["rsi_4h"]
    rsi_1d: Decimal = feat["rsi_1d"]
    rsi_1w: Decimal = feat["rsi_1w"]
    has_bearish_div: bool = feat["has_bearish_div"]
    macd_hist_4h: Decimal = feat["macd_hist_4h"]
    macd_hist_4h_prev: Decimal = feat["macd_hist_4h_prev"]
    macd_hist_1h: Decimal = feat["macd_hist_1h"]
    upper_wick_ratio_4h: Decimal = feat["upper_wick_ratio_4h"]
    taker_24h: Decimal | None = feat["taker_24h"]
    taker_4h: Decimal | None = feat["taker_4h"]
    taker_1h: Decimal | None = feat["taker_1h"]
    oi_7d: Decimal | None = feat["oi_7d"]
    oi_1h: Decimal | None = feat["oi_1h"]
    funding_rate: Decimal | None = feat["funding_rate"]
    has_deriv: bool = feat["has_deriv"]

    if has_deriv and oi_7d is None:
        return None

    short_features = ShortArchetypeFeatures(
        close=close, high_24h=high_24h, high_30d=high_30d, ema50_4h=ema50_4h, poc_30d=poc_price,
        atr14_4h=atr14_4h, has_bearish_div=has_bearish_div, rsi_4h=rsi_4h, rsi_1d=rsi_1d, rsi_1w=rsi_1w,
        oi_delta_7d=oi_7d, upper_wick_ratio_4h=upper_wick_ratio_4h, max_ret_30d=range_height_30d,
        taker_buy_ratio_24h=taker_24h, macd_hist_4h=macd_hist_4h, pos_in_range_30d=pos_in_range,
        macd_hist_4h_prev=macd_hist_4h_prev, runup_30d=range_height_30d, ret_14d=ret_14d,
    )
    archetype = classify_short_archetype(short_features)

    is_h2_bounce = False
    if archetype is None and h2_bounce_short:
        dist_72h_low = (close - low_72h) / max(low_72h, EPSILON)
        surge_72h = (high_24h - low_72h) / max(low_72h, EPSILON)
        rejected = close <= (high_24h - Decimal("0.50") * atr14_4h)
        is_turning_down = (
            (macd_hist_4h <= macd_hist_4h_prev or macd_hist_1h < ZERO)
            and (taker_4h is not None and taker_4h <= Decimal("0.98"))
        )
        if (
            Decimal("0.15") <= pos_in_range < Decimal("0.45")
            and dist_72h_low >= Decimal("0.08")
            and (ret_24h >= Decimal("0.12") or surge_72h >= Decimal("0.22"))
            and rejected
            and (rsi_4h >= Decimal("58.0") or upper_wick_ratio_4h >= Decimal("0.30"))
            and is_turning_down
        ):
            archetype = SetupLabel.EXHAUSTION_REVERSAL_SHORT
            is_h2_bounce = True

    if archetype is None:
        return None

    if not is_h2_bounce and not (h3_s2_s4_4h_taker or h4_mild_neg_funding):
        gate_inputs = ShortGateInputs(
            close=close, ema50_4h=ema50_4h, high_7d=high_7d, range_height_30d=range_height_30d,
            ret_7d=ret_7d, ret_14d=ret_14d, high_30d=high_30d, low_30d=low_30d,
            days_since_30d_high=feat["days_since_high"], ema20_4h=ema20_4h, atr14_4h=atr14_4h,
            ret_24h=ret_24h, rsi_1h=rsi_1h, rsi_4h=rsi_4h, has_bearish_div=has_bearish_div,
            has_climax_blowoff=(rsi_1d >= Decimal("76.0")) and (upper_wick_ratio_4h >= Decimal("0.45")),
            poc_30d=poc_price, macd_hist_4h=macd_hist_4h, taker_buy_ratio_24h=taker_24h,
            funding_rate_8h=funding_rate, oi_delta_7d=oi_7d, oi_delta_1h=oi_1h,
            taker_buy_ratio_1h=taker_1h, low_72h=low_72h, high_24h=high_24h,
            open_4h=feat["open_4h"], high_4h=feat["high_4h"], low_4h=feat["low_4h"],
            rsi_1d=rsi_1d, macd_hist_4h_prev=macd_hist_4h_prev, basis_bps=ZERO, runup_30d=range_height_30d,
        )
        gates_ok, _ = evaluate_short_gates(gate_inputs, archetype)
        if not gates_ok:
            return None
    else:
        if h4_mild_neg_funding:
            is_squeeze = (
                funding_rate is not None
                and funding_rate < ZERO
                and close >= ema50_4h * Decimal("0.975")
                and (
                    funding_rate <= Decimal("-0.00005")
                    or (taker_1h is not None and taker_1h > Decimal("0.98"))
                    or (oi_1h is not None and oi_1h > ZERO)
                )
            )
        else:
            is_squeeze = (
                funding_rate is not None
                and funding_rate < ZERO
                and close >= ema50_4h * Decimal("0.975")
            )
        is_buyer_dom = taker_24h is not None and (
            (taker_24h >= Decimal("1.20") and close >= ema50_4h)
            or (taker_24h >= Decimal("1.05") and not has_bearish_div and close >= ema50_4h)
        )
        if is_squeeze or is_buyer_dom:
            return None

        dist_72h_low = (close - low_72h) / max(low_72h, EPSILON)
        dist_30d_low = (close - low_30d) / max(low_30d, EPSILON)
        downward_ext = (ema20_4h - close) / max(atr14_4h, EPSILON)
        if not (
            dist_72h_low >= Decimal("0.04")
            and dist_30d_low >= Decimal("0.10")
            and rsi_1h >= Decimal("32.0")
            and rsi_4h >= Decimal("38.0")
            and downward_ext <= Decimal("2.00")
            and (is_h2_bounce or pos_in_range >= Decimal("0.45"))
        ):
            return None

        if not is_h2_bounce:
            rejected = close <= (high_24h - Decimal("0.50") * atr14_4h)
            is_climax = (rsi_1d >= Decimal("72.0") and upper_wick_ratio_4h >= Decimal("0.35")) or (
                rsi_4h >= Decimal("72.0") and upper_wick_ratio_4h >= Decimal("0.35")
            )
            is_utad = has_bearish_div and (macd_hist_4h < macd_hist_4h_prev)
            seller_confirm = (
                close <= ema20_4h
                or (taker_24h is not None and taker_24h <= Decimal("0.92"))
                or (h3_s2_s4_4h_taker and taker_4h is not None and taker_4h <= Decimal("0.92"))
            )
            track_a = pos_in_range >= Decimal("0.65") and rejected and (is_climax or is_utad) and seller_confirm
            overhead = [lvl for lvl in (ema20_4h, ema50_4h, poc_price) if lvl >= close * Decimal("0.985")]
            is_retest = bool(overhead) and ((min(overhead) - close) / max(close, EPSILON) <= Decimal("0.050"))
            mom_decay = macd_hist_4h <= Decimal("0.05") and (
                macd_hist_4h <= macd_hist_4h_prev
                or (taker_24h is not None and taker_24h <= Decimal("1.00"))
                or close <= ema20_4h
            )
            track_b = is_retest and mom_decay and pos_in_range >= Decimal("0.45")
            if not (track_a or track_b):
                return None

        if has_deriv:
            f8 = funding_rate or ZERO
            o7 = oi_7d or ZERO
            o1 = oi_1h or ZERO
            trapped = (
                (f8 >= Decimal("0.0001") and o7 >= Decimal("0.15"))
                or (f8 >= Decimal("0.00005") and ((oi_7d is not None and o7 >= ZERO) or (taker_1h is not None and taker_1h <= Decimal("0.92"))))
                or (taker_24h is not None and taker_24h <= Decimal("0.92"))
                or (h3_s2_s4_4h_taker and taker_4h is not None and taker_4h <= Decimal("0.90"))
            )
            sq_flush = (
                (f8 <= Decimal("-0.0003") and o7 >= Decimal("0.25") and o1 <= Decimal("-0.015") and taker_1h is not None and taker_1h <= Decimal("0.85"))
                or (f8 <= Decimal("-0.00015") and taker_1h is not None and taker_1h <= Decimal("0.92"))
            )
            if not (trapped or sq_flush):
                return None

    try:
        bracket = build_short_bracket(
            close=close, ema50_4h=ema50_4h, poc_30d=poc_price, atr14_4h=atr14_4h,
            local_consolidation_low_4h=feat["local_low_4h"],
            swing_high_4h=feat["swing_high_4h"],
            low_30d=low_30d, vah_30d=vah_price, val_30d=val_price, tick_size=feat["tick"],
        )
    except InvalidExecutionBracket:
        return None
    if abs(close - bracket.suggested_entry) / max(close, EPSILON) > Decimal("0.045"):
        return None
    if bracket.asymmetric_rr < Decimal("1.40"):
        return None

    # Checklist item 2 ("Bearish Divergence") + remaining checklist gates check
    if not h1_checklist_parity and not has_bearish_div:
        return None
    if h1_checklist_parity and archetype == SetupLabel.UTAD_DIVERGENCE_SHORT and not has_bearish_div:
        return None

    # Check the other 4 checklist items when testing checklist parity
    primary_ceiling = max(ema50_4h, poc_price)
    dist_to_ceiling = (primary_ceiling - close) / max(close, EPSILON)
    is_active_retest = Decimal("-0.015") <= dist_to_ceiling <= Decimal("0.050")
    is_utad_or_climax = archetype in (SetupLabel.UTAD_DIVERGENCE_SHORT, SetupLabel.EXHAUSTION_REVERSAL_SHORT)
    if is_utad_or_climax:
        item1_ok = bool(rsi_4h >= Decimal("65.0") or rsi_1d >= Decimal("68.0") or (rsi_4h >= Decimal("58.0") and has_bearish_div) or is_h2_bounce)
    else:
        item1_ok = bool(is_active_retest)
    item3_ok = bool(feat["is_4h_macd_bearish_cross"] or macd_hist_4h < ZERO or macd_hist_1h < ZERO)
    item4_ok = bool(feat["vol_drying"] or feat["has_climax_wick"] or (macd_hist_4h < macd_hist_4h_prev))
    f8_val = funding_rate or ZERO
    item5_ok = bool(
        (has_deriv is False)
        or (funding_rate is not None and f8_val >= Decimal("0.00005"))
        or (taker_24h is not None and taker_24h <= Decimal("0.92"))
        or (h3_s2_s4_4h_taker and taker_4h is not None and taker_4h <= Decimal("0.90"))
    )
    if not (item1_ok and item3_ok and item4_ok and item5_ok):
        return None

    return {
        "archetype": ("BOUNCE_EXHAUSTION_SHORT" if is_h2_bounce else archetype.value),
        "entry": float(bracket.suggested_entry),
        "stop": float(bracket.invalidation_stop),
        "t1": float(bracket.targets[0]),
        "t2": float(bracket.targets[1]),
    }


async def main() -> None:
    cohorts = load_cohorts()
    all_syms = sorted(set(cohorts["nov8"]["symbols"]) | set(cohorts["nov12"]["symbols"]))
    parsed: dict[str, dict[str, Any]] = {}
    for sym in all_syms:
        p = RAW_CACHE_DIR / f"{sym}.json"
        if not p.exists():
            continue
        raw = json.loads(p.read_text())
        c1h = parse_candles(raw["klines_1h"])
        if not c1h:
            continue
        parsed[sym] = {
            "inst": make_instrument(sym, infer_tick(c1h)),
            "c1h": c1h,
            "c4h": parse_candles(raw["klines_4h"]),
            "c1d": parse_candles(raw["klines_1d"]),
            "c1w": parse_candles(raw["klines_1w"]),
            "oi_map": {int(r["timestamp"]): Decimal(str(r["sumOpenInterest"])) for r in raw["oi_1h"] if float(r["sumOpenInterest"]) > 0},
            "taker_map": {int(r["timestamp"]): (Decimal(str(r["buyVol"])), Decimal(str(r["sellVol"]))) for r in raw["taker_1h"]},
            "funding": sorted((int(r["fundingTime"]), Decimal(str(r["fundingRate"]))) for r in raw["funding"]),
        }

    print(f"Precomputing hourly features for {len(parsed)} symbols...")
    # Precompute 4H feature bundle keyed by (sym, len(c4h), len(c1d))
    cache_4h: dict[tuple[str, int, int], dict[str, Any]] = {}
    precomputed: dict[str, dict[str, list[tuple[datetime, dict[str, Any]]]]] = {}

    for cid, cohort in cohorts.items():
        start_hour = cohort["anchor"].replace(minute=0, second=0, microsecond=0)
        step_times = [start_hour + timedelta(hours=h) for h in range(49)]
        precomputed[cid] = {}
        for sym in sorted(cohort["symbols"]):
            pdata = parsed.get(sym)
            if not pdata:
                continue
            rows: list[tuple[datetime, dict[str, Any]]] = []
            c1h_all = pdata["c1h"]
            c4h_all = pdata["c4h"]
            c1d_all = pdata["c1d"]
            c1w_all = pdata["c1w"]
            tick = pdata["inst"].price_tick

            for dt in step_times:
                c1h = [c for c in c1h_all if c.closed_at <= dt]
                c4h = [c for c in c4h_all if c.closed_at <= dt]
                if len(c1h) < 210 or len(c4h) < 55:
                    continue
                c1d = [c for c in c1d_all if c.closed_at <= dt]
                c1w = [c for c in c1w_all if c.closed_at <= dt]

                key4 = (sym, len(c4h), len(c1d))
                if key4 not in cache_4h:
                    closes_4h = [c.close for c in c4h]
                    ema20_4h = ema(closes_4h, 20)[-1]
                    ema50_4h = ema(closes_4h, 50)[-1] if len(closes_4h) >= 50 else ema20_4h
                    atr14_4h = atr(c4h, 14)[-1]
                    vpvr_window = c4h[-180:] if len(c4h) >= 180 else c4h
                    vp = volume_profile(vpvr_window, bins=50, tick_size=tick)
                    high_30d = max(c.high for c in vpvr_window)
                    low_30d = min(c.low for c in vpvr_window)
                    if low_30d <= ZERO or high_30d <= low_30d:
                        cache_4h[key4] = {"valid": False}
                    else:
                        d_bars = c1d if len(c1d) >= 15 else resample_candles(c4h, "1d")
                        w_bars = c1w if len(c1w) >= 15 else resample_candles(d_bars, "1w")
                        rsi_4h = (rsi(closes_4h, 14) or [Decimal("50")])[-1]
                        rsi_1d = (rsi([c.close for c in d_bars], 14) or [rsi_4h])[-1] if len(d_bars) >= 15 else rsi_4h
                        rsi_1w = (rsi([c.close for c in w_bars], 14) or [rsi_1d])[-1] if len(w_bars) >= 15 else rsi_1d
                        has_bearish_div = (
                            any(d.kind == "bearish" for d in confirmed_rsi_divergences(c4h, lookback=48, min_rsi_delta=Decimal("2.0")))
                            or any(d.kind == "bearish" for d in confirmed_macd_divergences(c4h, lookback=48))
                            or (len(d_bars) >= 20 and any(d.kind == "bearish" for d in confirmed_rsi_divergences(d_bars, lookback=30, min_rsi_delta=Decimal("2.0"))))
                            or (len(d_bars) >= 30 and any(d.kind == "bearish" for d in confirmed_macd_divergences(d_bars, lookback=30)))
                        )
                        ml, ms, mh = macd(closes_4h)
                        macd_hist_4h = mh[-1] if mh else ZERO
                        macd_hist_4h_prev = mh[-2] if len(mh) >= 2 else macd_hist_4h
                        is_4h_cross = len(ml) >= 2 and ml[-1] <= ms[-1]
                        avg_vol_20 = sum(c.volume for c in c4h[-20:]) / Decimal("20")
                        recent_4h = c4h[-8:]
                        uwick = max(((b.high - max(b.open, b.close)) / max(b.high - b.low, Decimal("1e-8"))) for b in recent_4h)
                        climax_wick = any(b.volume > avg_vol_20 * Decimal("1.3") and (b.high - max(b.open, b.close)) >= (b.high - b.low) * Decimal("0.35") for b in recent_4h)
                        high_idx = max(range(len(vpvr_window)), key=lambda i: vpvr_window[i].high)
                        cache_4h[key4] = {
                            "valid": True,
                            "ema20_4h": ema20_4h, "ema50_4h": ema50_4h, "atr14_4h": atr14_4h,
                            "poc_price": vp.poc_price, "vah_price": vp.vah_price, "val_price": vp.val_price,
                            "high_30d": high_30d, "low_30d": low_30d,
                            "range_height_30d": (high_30d - low_30d) / max(low_30d, Decimal("1e-8")),
                            "rsi_4h": rsi_4h, "rsi_1d": rsi_1d, "rsi_1w": rsi_1w,
                            "has_bearish_div": has_bearish_div,
                            "macd_hist_4h": macd_hist_4h, "macd_hist_4h_prev": macd_hist_4h_prev,
                            "is_4h_macd_bearish_cross": is_4h_cross,
                            "vol_drying": c4h[-1].volume < avg_vol_20 * Decimal("0.95"),
                            "has_climax_wick": climax_wick, "upper_wick_ratio_4h": uwick,
                            "days_since_high": Decimal(str(len(vpvr_window) - 1 - high_idx)) / Decimal("24"),
                            "open_4h": c4h[-1].open, "high_4h": c4h[-1].high, "low_4h": c4h[-1].low,
                            "local_low_4h": min(c.low for c in c4h[-12:]),
                            "swing_high_4h": max(c.high for c in c4h[-42:]),
                        }

                b4 = cache_4h[key4]
                if not b4["valid"]:
                    continue

                snap_1h = indicator_snapshot(c1h[-220:])
                close = snap_1h.close
                high_24h = max(c.high for c in c1h[-24:])
                high_7d = max(c.high for c in c1h[-168:]) if len(c1h) >= 168 else b4["high_30d"]
                low_72h = min(c.low for c in c1h[-72:]) if len(c1h) >= 72 else b4["low_30d"]
                ret_24h = (close - c1h[-25].close) / c1h[-25].close if len(c1h) >= 25 else ZERO
                ret_7d = (close - c1h[-169].close) / c1h[-169].close if len(c1h) >= 169 else ZERO
                ret_14d = (close - c1h[-337].close) / c1h[-337].close if len(c1h) >= 337 else ret_7d
                deriv, f_rate = build_derivatives_at(pdata["oi_map"], pdata["taker_map"], pdata["funding"], int(dt.timestamp() * 1000))
                taker_24h = deriv.taker_buy_sell_ratio_24h if (deriv and deriv.taker_buy_sell_ratio_24h is not None) else taker_buy_ratio_from_candles(c1h, hours=24)
                taker_4h = deriv.taker_buy_sell_ratio_4h if (deriv and deriv.taker_buy_sell_ratio_4h is not None) else taker_buy_ratio_from_candles(c1h, hours=4)
                feat = {
                    **b4,
                    "tick": tick,
                    "close": close,
                    "pos_in_range": (close - b4["low_30d"]) / max(b4["high_30d"] - b4["low_30d"], Decimal("1e-8")),
                    "high_24h": high_24h, "high_7d": high_7d, "low_72h": low_72h,
                    "ret_24h": ret_24h, "ret_7d": ret_7d, "ret_14d": ret_14d,
                    "rsi_1h": snap_1h.rsi14, "macd_hist_1h": snap_1h.macd_hist,
                    "taker_24h": taker_24h, "taker_4h": taker_4h,
                    "taker_1h": deriv.taker_buy_sell_ratio if deriv else None,
                    "oi_7d": deriv.open_interest_change_7d if deriv else None,
                    "oi_1h": deriv.open_interest_change_1h if deriv else None,
                    "funding_rate": f_rate,
                    "has_deriv": deriv is not None,
                }
                rows.append((dt, feat))
            precomputed[cid][sym] = rows

    print("Precompute complete! Evaluating variants...")
    variants = {
        "baseline_5_of_5": {},
        "H1_checklist_parity": {"h1_checklist_parity": True},
        "H1+H3_4h_taker": {"h1_checklist_parity": True, "h3_s2_s4_4h_taker": True},
        "H1+H4_mild_neg_funding": {"h1_checklist_parity": True, "h4_mild_neg_funding": True},
        "H1+H2_bounce_exhaustion": {"h1_checklist_parity": True, "h2_bounce_short": True},
        "H1+H3+H4_upper_only": {"h1_checklist_parity": True, "h3_s2_s4_4h_taker": True, "h4_mild_neg_funding": True},
        "H1+H2+H3+H4_all": {"h1_checklist_parity": True, "h2_bounce_short": True, "h3_s2_s4_4h_taker": True, "h4_mild_neg_funding": True},
    }

    summary: dict[str, Any] = {}
    for vname, vkwargs in variants.items():
        summary[vname] = {}
        for cid in ("nov8", "nov12"):
            signals = []
            for sym, rows in sorted(precomputed[cid].items()):
                c1h_all = parsed[sym]["c1h"]
                for dt, feat in rows:
                    res = evaluate_precomputed(feat, **vkwargs)
                    if res is not None:
                        fwd24 = [c for c in c1h_all if c.opened_at >= dt][:24]
                        fwd48 = [c for c in c1h_all if c.opened_at >= dt][:48]
                        ref = float(fwd24[0].open)
                        r24 = (ref - float(fwd24[-1].close)) / ref * 100.0 if fwd24 else 0.0
                        r48 = (ref - float(fwd48[-1].close)) / ref * 100.0 if fwd48 else 0.0
                        sq24 = (max(float(b.high) for b in fwd24) - ref) / ref * 100.0 if fwd24 else 0.0
                        sim = simulate_bracket(c1h_all, dt, res["entry"], res["stop"], res["t1"], res["t2"], 48)
                        signals.append({
                            "symbol": sym, "at": dt.isoformat(), "archetype": res["archetype"],
                            "r24": round(r24, 2), "r48": round(r48, 2), "sq24": round(sq24, 2), "sim": sim,
                        })
                        break

            filled = [s for s in signals if s["sim"].get("filled")]
            filled_r = [s["sim"]["r_multiple"] for s in filled if "r_multiple" in s["sim"]]
            wins_24 = sum(1 for s in signals if s["r24"] > 0)
            wins_48 = sum(1 for s in signals if s["r48"] > 0)
            sq6 = sum(1 for s in signals if s["sq24"] >= 6.0)
            summary[vname][cid] = {
                "count": len(signals),
                "mean_r24": round(sum(s["r24"] for s in signals) / len(signals), 2) if signals else None,
                "win_rate_24h": f"{wins_24}/{len(signals)}",
                "mean_r48": round(sum(s["r48"] for s in signals) / len(signals), 2) if signals else None,
                "win_rate_48h": f"{wins_48}/{len(signals)}",
                "mean_sq24": round(sum(s["sq24"] for s in signals) / len(signals), 2) if signals else None,
                "breached_6pct": sq6,
                "filled_count": len(filled),
                "mean_filled_r": round(sum(filled_r) / len(filled_r), 2) if filled_r else None,
                "signals": signals,
            }

    OUT_PATH.write_text(json.dumps(summary, indent=2) + "\n")
    for vname, cohorts_res in summary.items():
        print(f"\n=== {vname} ===")
        for cid, m in cohorts_res.items():
            syms = [f"{s['symbol']}({s['r24']:+.1f}%)" for s in m["signals"]]
            print(f"  {cid}: n={m['count']} | 24h={m['mean_r24']}% ({m['win_rate_24h']}) | 48h={m['mean_r48']}% ({m['win_rate_48h']}) | sq24={m['mean_sq24']}% (>6%:{m['breached_6pct']}) | filled={m['filled_count']} meanR={m['mean_filled_r']} | {syms}")


if __name__ == "__main__":
    asyncio.run(main())
