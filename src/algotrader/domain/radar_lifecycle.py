"""Research-only radar lifecycle policy (v2).

Keep immutable discovery references separate from fresh verification inputs. Unknown
telemetry never certifies a gate. Trigger observations do not assert executed fills.
Stops take conservative priority when a candle touches both stop and target. Untriggered
setups expire at 72 hours, including entry shelves; observed triggers persist until
invalidation/completion. Historical OHLC cannot order events within a discovery bar.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from algotrader.domain.gate_contract import (
    BELOW_EMA50_BASE_LONG_ARCHETYPES,
    BELOW_POC_LONG_ARCHETYPES,
    CLIMAX_SHORT_ARCHETYPES,
    EMA50_ANCHORED_SHORT_ARCHETYPES,
)


class RadarLifecycleState(StrEnum):
    FRESH_COIL = "FRESH_COIL"
    AT_ENTRY_ZONE = "AT_ENTRY_ZONE"
    IN_ACTIVE_TRADE = "IN_ACTIVE_TRADE"
    ACTIVE_COIL = "ACTIVE_COIL"
    COMPLETED_TARGET = "COMPLETED_TARGET"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class RadarLifecycleDecision:
    retained: bool
    state: RadarLifecycleState
    label: str
    reason: str
    current_price: Decimal
    pnl_from_entry_pct: Decimal | None = None
    gates_passed: int = 5
    gates_total: int = 5
    failed_gates: tuple[str, ...] = ()
    updated_checklist: tuple[dict[str, Any], ...] = ()
    triggered_at: str | None = None
    trigger_basis: str | None = None
    trigger_entry_price: Decimal | None = None
    trigger_stop_price: Decimal | None = None


def _to_dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        cleaned = (
            str(value).replace("%", "").replace("bps", "").replace("$", "").replace("+", "").strip()
        )
        d = Decimal(cleaned)
        return d if d.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _parse_iso(ts: Any) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo is not None else None
    except ValueError:
        return None


def _extract_decimals_from_candles(candles: Sequence[Mapping[str, Any]], key: str) -> list[Decimal]:
    out: list[Decimal] = []
    for c in candles:
        if isinstance(c, Mapping):
            val = _to_dec(c.get(key))
            if val is not None and val > Decimal("0"):
                out.append(val)
    return out


def _fmt_price(val: Decimal) -> str:
    f = float(val)
    if f < 0.01:
        return f"{f:.6f}"
    if f < 0.1:
        return f"{f:.5f}"
    if f < 1.0:
        return f"{f:.4f}"
    if f < 10.0:
        return f"{f:.3f}"
    return f"{f:.2f}"


def reverify_candidate_gates(
    candidate: Mapping[str, Any],
    *,
    current_price: Decimal,
    recent_candles_1h: Sequence[Mapping[str, Any]] = (),
    ema50_4h: Decimal | None = None,
    continuation_gates: tuple[bool | None, ...] | None = None,
    recovery_gates: tuple[bool | None, ...] | None = None,
    recovery_details: tuple[str, ...] | None = None,
    is_triggered: bool = False,
    trigger_entry_price: Decimal | None = None,
    age_hours: float | None = None,
) -> tuple[tuple[dict[str, Any], ...], int, int, tuple[str, ...]]:
    """Re-evaluates the 5 institutional verification gates (L1–L6) on live market structure.

    Returns (updated_checklist, passed_count, total_count, failed_gate_titles).
    Profit-expansion tolerances apply ONLY after an observed setup trigger (never on pending
    unfilled retest entries).
    """
    if candidate.get("setup_label") == "BREAKDOWN_RETEST_SHORT":
        from algotrader.domain.bearish_continuation import TITLES

        gates = continuation_gates if continuation_gates is not None and len(continuation_gates) == 5 else (None,) * 5
        items = tuple({"title": title, "passed": passed is True,
                       "status": "passed" if passed is True else "unknown" if passed is None else "failed",
                       "detail": "Current continuation gate" if passed is not None else "Fresh continuation inputs required"}
                      for title, passed in zip(TITLES, gates, strict=True))
        return items, sum(g is True for g in gates), 5, tuple(
            title for title, passed in zip(TITLES, gates, strict=True) if passed is not True)

    if candidate.get("setup_label") == "RECOVERY_RECLAIM_LONG":
        from algotrader.domain.early_recovery import TITLES as RECOVERY_TITLES

        gates = (
            recovery_gates
            if recovery_gates is not None and len(recovery_gates) == 5
            else (None,) * 5
        )
        details = (
            recovery_details
            if recovery_details is not None and len(recovery_details) == 5
            else tuple(
                "Current recovery gate verified" if g is not None else "Fresh recovery inputs required"
                for g in gates
            )
        )
        items = tuple(
            {
                "title": title,
                "passed": passed is True,
                "status": "passed" if passed is True else "unknown" if passed is None else "failed",
                "detail": detail,
            }
            for title, passed, detail in zip(RECOVERY_TITLES, gates, details, strict=True)
        )
        return (
            items,
            sum(g is True for g in gates),
            5,
            tuple(
                title
                for title, passed in zip(RECOVERY_TITLES, gates, strict=True)
                if passed is not True
            ),
        )

    direction = str(candidate.get("direction", "long")).lower()
    is_short = direction == "short"

    candle_source = recent_candles_1h
    closes_1h = _extract_decimals_from_candles(candle_source, "close")

    # Only explicit, freshly computed telemetry is eligible for re-verification.
    # Frozen value-area levels remain the original setup's structural references.
    eff_ema50 = ema50_4h
    poc = _to_dec(candidate.get("poc_price")) or _to_dec(candidate.get("poc_30d"))
    vah = _to_dec(candidate.get("vah_price")) or _to_dec(candidate.get("vah_30d"))
    val = _to_dec(candidate.get("val_price")) or _to_dec(candidate.get("val_30d"))
    entry = _to_dec(candidate.get("suggested_entry")) or _to_dec(candidate.get("pullback_entry"))

    # The lifecycle caller validates chronology and locks the observed entry reference.
    # Raw candidate metadata must not revive a rejected trigger or invent an entry.
    effective_is_triggered = is_triggered
    profit_entry = trigger_entry_price
    in_profit_expansion = bool(
        effective_is_triggered
        and profit_entry is not None
        and profit_entry.is_finite()
        and profit_entry > Decimal("0")
        and (
            (not is_short and current_price > profit_entry * Decimal("1.005"))
            or (is_short and current_price < profit_entry * Decimal("0.995"))
        )
    )

    # 1. Gate L1: Trend Baseline (4H EMA50, Sub-EMA50 Emerging Base Shelf, or Climax Ceiling for Shorts)
    setup_label = str(candidate.get("setup_label", "")).upper()
    discovery_price = _to_dec(candidate.get("discovery_price")) or _to_dec(
        candidate.get("current_price")
    )
    stop_ref = _to_dec(candidate.get("suggested_stop"))
    is_climax_or_utad_short = is_short and setup_label in CLIMAX_SHORT_ARCHETYPES
    is_ema50_anchored_short = is_short and setup_label in EMA50_ANCHORED_SHORT_ARCHETYPES
    is_sub_ema50_base_long = (
        not is_short
        and setup_label in BELOW_EMA50_BASE_LONG_ARCHETYPES
        and eff_ema50 is not None
        and eff_ema50 > Decimal("0")
        and discovery_price is not None
        and discovery_price > Decimal("0")
        and discovery_price < eff_ema50 * Decimal("0.992")
    )
    trend_passed = True
    trend_detail = (
        f"Holding above 4H EMA50 (${_fmt_price(eff_ema50)})"
        if eff_ema50
        else "Holding above 4H EMA50"
    )
    if eff_ema50 is not None and eff_ema50 > Decimal("0"):
        diff_ema_pct = (current_price - eff_ema50) / eff_ema50 * Decimal("100")
        if not is_short:
            if is_sub_ema50_base_long and discovery_price is not None:
                base_floor = discovery_price * Decimal("0.975")
                if stop_ref is not None and stop_ref > Decimal("0"):
                    base_floor = max(base_floor, stop_ref * Decimal("1.005"))
                trend_passed = current_price >= eff_ema50 * Decimal("0.992") or current_price >= base_floor
                if current_price >= eff_ema50:
                    trend_detail = f"Reclaimed 4H EMA50 (${_fmt_price(eff_ema50)}, {float(diff_ema_pct):+.1f}%)"
                elif trend_passed:
                    trend_detail = (
                        f"Holding emerging base shelf (${_fmt_price(base_floor)} | 4H EMA50 ${_fmt_price(eff_ema50)})"
                    )
                else:
                    trend_detail = f"Lost emerging base shelf (${_fmt_price(base_floor)})"
            else:
                # Allow 0.8% wick tolerance on EMA50 shelf
                trend_passed = current_price >= eff_ema50 * Decimal("0.992")
                if current_price >= eff_ema50:
                    trend_detail = f"Holding above 4H EMA50 (${_fmt_price(eff_ema50)}, {float(diff_ema_pct):+.1f}%)"
                elif trend_passed:
                    trend_detail = f"Testing 4H EMA50 support (${_fmt_price(eff_ema50)}, {float(diff_ema_pct):+.1f}%)"
                else:
                    trend_detail = (
                        f"Slipped {float(diff_ema_pct):.1f}% below 4H EMA50 (${_fmt_price(eff_ema50)})"
                    )
        elif is_climax_or_utad_short:
            climax_ref = max(
                d
                for d in (discovery_price, entry, current_price)
                if d is not None and d > Decimal("0")
            )
            climax_cap = (
                (discovery_price or climax_ref) * Decimal("1.030")
                if (discovery_price is not None and discovery_price > Decimal("0"))
                else climax_ref * Decimal("1.030")
            )
            trend_passed = (
                current_price <= eff_ema50 * Decimal("1.008")
                or current_price <= climax_cap
            )
            if current_price <= eff_ema50:
                trend_detail = (
                    f"Rolled below 4H EMA50 (${_fmt_price(eff_ema50)}, {float(diff_ema_pct):+.1f}%)"
                )
            elif trend_passed:
                trend_detail = (
                    f"Capped below climax ceiling (${_fmt_price(climax_cap)} | 4H EMA50 ${_fmt_price(eff_ema50)})"
                )
            else:
                trend_detail = (
                    f"Squeezed above climax ceiling (${_fmt_price(climax_cap)})"
                )
        else:
            trend_passed = current_price <= eff_ema50 * Decimal("1.008")
            if current_price <= eff_ema50:
                trend_detail = (
                    f"Capped below 4H EMA50 (${_fmt_price(eff_ema50)}, {float(diff_ema_pct):+.1f}%)"
                )
            elif trend_passed:
                trend_detail = f"Testing 4H EMA50 resistance (${_fmt_price(eff_ema50)}, {float(diff_ema_pct):+.1f}%)"
            else:
                trend_detail = f"Reclaimed +{float(diff_ema_pct):.1f}% above 4H EMA50 (${_fmt_price(eff_ema50)})"

    # 2. Gate L2: Anti-Chasing / Excursion Validation (preserve exact 1D/1W RSI telemetry)
    highs_72h = _extract_decimals_from_candles(candle_source[-72:], "high")
    lows_72h = _extract_decimals_from_candles(candle_source[-72:], "low")
    rsi_suffix = ""
    for item in candidate.get("checklist") or ():
        if isinstance(item, Mapping) and "RSI" in str(item.get("detail", "")):
            parts = [p.strip() for p in str(item.get("detail", "")).split("|") if "RSI" in p]
            if parts:
                rsi_suffix = " | " + " | ".join(parts)
            break
    if not rsi_suffix:
        r1d = _to_dec(candidate.get("rsi_1d"))
        r1w = _to_dec(candidate.get("rsi_1w"))
        if r1d is not None and r1w is not None:
            rsi_suffix = f" | 1D RSI {float(r1d):.1f} | 1W RSI {float(r1w):.1f}"
        else:
            rsi_suffix = " | discovery RSI unavailable"

    anti_chase_passed = True
    anti_chase_detail = f"72h range controlled{rsi_suffix}"
    if highs_72h and lows_72h:
        h72 = max(highs_72h)
        l72 = min(lows_72h)
        rng_72_pct = (h72 - l72) / max(l72, Decimal("1e-8")) * Decimal("100")
        if is_climax_or_utad_short:
            not_squeezed_above_top = current_price <= h72 * Decimal("1.015")
            not_late_waterfall = (
                effective_is_triggered
                or in_profit_expansion
                or current_price >= h72 * Decimal("0.82")
            )
            anti_chase_passed = bool(not_squeezed_above_top and not_late_waterfall)
            if anti_chase_passed:
                anti_chase_detail = f"Climax exhaustion intact (72h range {float(rng_72_pct):.1f}%){rsi_suffix}"
            else:
                anti_chase_detail = f"Climax short displaced from top (72h range {float(rng_72_pct):.1f}%){rsi_suffix}"
        else:
            anti_chase_passed = rng_72_pct <= Decimal("28.0") or in_profit_expansion
            if anti_chase_passed:
                state_word = (
                    "expanding in profit" if (in_profit_expansion and rng_72_pct > Decimal("20.0")) else "tight"
                )
                anti_chase_detail = f"72h range {state_word} ({float(rng_72_pct):.1f}%){rsi_suffix}"
            else:
                anti_chase_detail = f"72h excursion wide ({float(rng_72_pct):.1f}%){rsi_suffix}"

    # 3. Gate L3: Value Acceptance (archetype- and state-aware: preserves below-POC emerging base reclaims & climax shorts)
    value_passed = True
    value_detail = "Accepted above 30d POC"
    is_below_poc_reclaim_long = (not is_short) and (
        setup_label in BELOW_POC_LONG_ARCHETYPES
        or (
            poc is not None
            and poc > Decimal("0")
            and discovery_price is not None
            and discovery_price < poc * Decimal("0.990")
        )
    )
    if poc is not None and poc > Decimal("0"):
        diff_poc = (current_price - poc) / poc * Decimal("100")
        vah_str = f" | VAH: ${_fmt_price(vah)}" if vah is not None else ""
        if not is_short:
            if current_price >= poc:
                value_passed = True
                value_detail = f"Accepted above 30d POC (${_fmt_price(poc)}){vah_str}"
            elif current_price >= poc * Decimal("0.990"):
                value_passed = True
                value_detail = (
                    f"Testing 30d POC shelf (${_fmt_price(poc)}, {float(diff_poc):+.1f}%){vah_str}"
                )
            elif is_below_poc_reclaim_long:
                holds_ema = eff_ema50 is not None and eff_ema50 > 0 and current_price >= eff_ema50 * Decimal("0.992")
                holds_val = val is not None and val > 0 and current_price >= val * Decimal("0.985")
                holds_entry = entry is not None and entry > 0 and current_price >= entry * Decimal("0.985")
                value_passed = bool(holds_ema or holds_val or holds_entry)
                if value_passed:
                    base_ref = (
                        f"4H EMA50 ${_fmt_price(eff_ema50)}"
                        if holds_ema and eff_ema50 is not None
                        else f"VAL ${_fmt_price(val)}"
                        if holds_val and val is not None
                        else f"Entry shelf ${_fmt_price(entry)}"
                        if entry is not None
                        else "base shelf"
                    )
                    value_detail = (
                        f"Holding emerging base reclaim ({base_ref} | POC ${_fmt_price(poc)})"
                    )
                else:
                    val_str = f" | Below VAL (${_fmt_price(val)})" if val is not None else ""
                    value_detail = (
                        f"Lost emerging base below 30d POC (${_fmt_price(poc)}, {float(diff_poc):.1f}%){val_str}"
                    )
            else:
                value_passed = False
                val_str = f" | Testing VAL (${_fmt_price(val)})" if val is not None else ""
                value_detail = (
                    f"Below 30d POC (${_fmt_price(poc)}, {float(diff_poc):.1f}%){val_str}"
                )
        elif is_climax_or_utad_short:
            climax_shelf = discovery_price * Decimal("1.025") if (discovery_price is not None and discovery_price > 0) else None
            value_passed = bool(
                current_price <= poc * Decimal("1.010")
                or (vah is not None and vah > 0 and current_price <= vah * Decimal("1.020"))
                or (climax_shelf is not None and current_price <= climax_shelf)
            )
            if current_price <= poc:
                value_detail = f"Mean-reverted below 30d POC (${_fmt_price(poc)})"
            elif value_passed:
                value_detail = f"Rejecting climax excess toward 30d POC (${_fmt_price(poc)}){vah_str}"
            else:
                value_detail = f"Broke above climax resistance ({float(diff_poc):+.1f}% vs POC ${_fmt_price(poc)})"
        elif (
            is_ema50_anchored_short
            and discovery_price is not None
            and discovery_price > poc * Decimal("1.010")
        ):
            capped_ema = (
                eff_ema50 is not None
                and eff_ema50 > Decimal("0")
                and current_price <= eff_ema50 * Decimal("1.008")
            )
            capped_disc = current_price <= discovery_price * Decimal("1.020")
            value_passed = bool(
                current_price <= poc * Decimal("1.010") or (capped_ema and capped_disc)
            )
            if current_price <= poc:
                value_detail = f"Rejected below 30d POC (${_fmt_price(poc)})"
            elif value_passed and eff_ema50 is not None:
                value_detail = (
                    f"Capped below 4H EMA50 distribution shelf (${_fmt_price(eff_ema50)} | POC ${_fmt_price(poc)})"
                )
            else:
                value_detail = (
                    f"Reclaimed above distribution shelf ({float(diff_poc):+.1f}% vs POC ${_fmt_price(poc)})"
                )
        else:
            value_passed = current_price <= poc * Decimal("1.010")
            if current_price <= poc:
                value_detail = f"Rejected below 30d POC (${_fmt_price(poc)})"
            elif value_passed:
                value_detail = (
                    f"Testing 30d POC resistance (${_fmt_price(poc)}, {float(diff_poc):+.1f}%)"
                )
            else:
                value_detail = (
                    f"Trading above 30d POC (${_fmt_price(poc)}, {float(diff_poc):+.1f}%)"
                )

    # 4. Gate L4: Volatility Compression / Breakout Structure
    vol_passed = True
    vol_detail = "Volatility compression intact"
    metrics = candidate.get("radar_metrics") or {}
    bbw = _to_dec(metrics.get("bb_bandwidth_4h"))
    kc_sq = metrics.get("kc_squeeze_4h")
    if bbw is not None:
        in_favorable_expansion = effective_is_triggered and (
            (not is_short and vah is not None and current_price >= vah) or in_profit_expansion
        )
        constructive_breakout_release = (
            not is_short
            and discovery_price is not None
            and discovery_price > Decimal("0")
            and (
                (bbw <= Decimal("0.32") and current_price >= discovery_price * Decimal("1.005"))
                or (
                    setup_label in ("MOMENTUM_EMERGING_LONG", "BREAKOUT_LONG")
                    and current_price >= discovery_price * Decimal("0.985")
                )
            )
        )
        climax_band_rollover = is_climax_or_utad_short and (
            discovery_price is None or current_price <= discovery_price * Decimal("1.025")
        )
        vol_passed = (
            bbw <= Decimal("0.24")
            or in_favorable_expansion
            or constructive_breakout_release
            or climax_band_rollover
        )
        if bbw <= Decimal("0.13") or kc_sq is True and bbw <= Decimal("0.15"):
            vol_detail = f"Bandwidth {float(bbw):.2f} (BB inside KC Squeeze)"
        elif bbw <= Decimal("0.20"):
            vol_detail = f"Bandwidth {float(bbw):.2f} (Controlled 4H Base Coil)"
        elif bbw <= Decimal("0.24"):
            vol_detail = f"Bandwidth {float(bbw):.2f} (Controlled Base <= 0.24)"
        elif in_favorable_expansion or constructive_breakout_release:
            vol_detail = f"Bandwidth {float(bbw):.2f} (Expanding in Upside Breakout)"
        elif climax_band_rollover:
            vol_detail = f"Bandwidth {float(bbw):.2f} (Climax Exhaustion Rollover)"
        else:
            vol_detail = f"Bandwidth loosened ({float(bbw):.2f}) without upside breakout"

    # 5. Gate L5/L6: Smart Money & Derivatives Absorption (enforces acute 1h OI, taker flow, and funding crowding)
    oi_7d = _to_dec(metrics.get("oi_change_7d_pct"))
    oi_4h = _to_dec(metrics.get("oi_change_4h_pct"))
    oi_1h = _to_dec(metrics.get("oi_change_1h_pct"))
    funding_bps = _to_dec(metrics.get("funding_rate_bps"))
    taker_r = _to_dec(metrics.get("taker_buy_sell_ratio"))
    vcr = _to_dec(metrics.get("volume_contraction_ratio"))
    sm_passed = True
    if not is_short:
        oi_7d_floor = (
            Decimal("-12.0")
            if (
                setup_label == "MOMENTUM_EMERGING_LONG"
                and taker_r is not None
                and taker_r >= Decimal("1.15")
            )
            else Decimal("-8.0")
        )
        # ALFA v3.7 (P0-3): Mirror discovery's fresh contract-quantity OI inflection exemption
        # so active post-washout leaders (e.g. SENT/SKY/RS_IGN) are never killed at T+1h..T+3h
        # by stale 7d OI when fresh 4h OI + buyer flow or active profit expansion confirm demand.
        has_fresh_oi_inflection = bool(
            (oi_4h is not None and oi_4h >= Decimal("1.0") and taker_r is not None and taker_r >= Decimal("1.08"))
            or (oi_4h is not None and oi_4h >= Decimal("0.1") and taker_r is not None and taker_r >= Decimal("1.17"))
            or (
                in_profit_expansion
                and taker_r is not None
                and taker_r >= Decimal("1.00")
                and (oi_1h is None or oi_1h >= Decimal("-3.5"))
            )
        )
        if oi_7d is not None and oi_7d < oi_7d_floor and not has_fresh_oi_inflection:
            sm_passed = False
        if oi_1h is not None and oi_1h <= Decimal("-5.0"):
            sm_passed = False
        stale_untriggered_seller_flow = (
            not effective_is_triggered
            and age_hours is not None
            and age_hours >= 2.5
            and taker_r is not None
            and taker_r < Decimal("1.00")
        )
        triggered_non_negative = bool(
            effective_is_triggered
            and profit_entry is not None
            and profit_entry > Decimal("0")
            and current_price >= profit_entry
        )
        min_active_taker = (
            Decimal("0.85")
            if in_profit_expansion
            else (Decimal("0.88") if triggered_non_negative else Decimal("0.92"))
        )
        if taker_r is not None and (
            taker_r < min_active_taker
            or stale_untriggered_seller_flow
        ):
            sm_passed = False
        if (
            not in_profit_expansion
            and funding_bps is not None
            and funding_bps >= Decimal("15.0")
        ):
            sm_passed = False
    else:
        if (
            funding_bps is not None
            and funding_bps <= Decimal("-5.0")
            and taker_r is not None
            and taker_r >= Decimal("1.15")
        ):
            sm_passed = False
        if not in_profit_expansion and taker_r is not None and taker_r >= Decimal("1.25"):
            sm_passed = False
    constructive_volume_ignition = (
        not is_short
        and taker_r is not None
        and taker_r >= Decimal("1.05")
        and discovery_price is not None
        and discovery_price > Decimal("0")
        and current_price >= discovery_price * Decimal("0.990")
        and vcr is not None
        and vcr <= Decimal("5.00")
    )
    climax_short_volume_ok = (
        is_climax_or_utad_short
        and taker_r is not None
        and taker_r <= Decimal("1.08")
        and vcr is not None
        and vcr <= Decimal("2.50")
    )
    if (
        vcr is not None
        and vcr > Decimal("1.45")
        and not in_profit_expansion
        and not constructive_volume_ignition
        and not climax_short_volume_ok
    ):
        sm_passed = False
    oi_str = f"7d OI {float(oi_7d):+.1f}%" if oi_7d is not None else "7d OI unavailable"
    oi1_str = f", 1h OI {float(oi_1h):+.1f}%" if oi_1h is not None else ""
    fund_str = f", funding {float(funding_bps):+.2f} bps" if funding_bps is not None else ""
    taker_str = f", taker {float(taker_r):.2f}" if taker_r is not None else ""
    sm_detail = (
        f"{oi_str}{oi1_str}{fund_str}{taker_str}"
        if sm_passed
        else f"Orderflow weakened ({oi_str}{oi1_str}{fund_str}{taker_str})"
    )

    availability = (
        eff_ema50 is not None and eff_ema50 > 0,
        len(closes_1h) >= 72,
        poc is not None and poc > 0,
        bbw is not None,
        oi_7d is not None
        and oi_1h is not None
        and taker_r is not None
        and funding_bps is not None
        and vcr is not None,
    )
    checks = (
        ("Trend Baseline", trend_passed, trend_detail),
        ("Anti-Chasing Validated", anti_chase_passed, anti_chase_detail),
        ("Value Acceptance", value_passed, value_detail + " (discovery reference)"),
        ("Volatility Compression", vol_passed, vol_detail),
        ("Smart Money Absorption", sm_passed, sm_detail),
    )
    updated_checklist: tuple[dict[str, Any], ...] = tuple(
        {
            "title": title,
            "passed": bool(known and passed),
            "status": ("passed" if passed else "failed") if known else "unknown",
            "detail": detail if known else "Unavailable: fresh telemetry required",
        }
        for (title, passed, detail), known in zip(checks, availability, strict=True)
    )
    failed_titles: tuple[str, ...] = tuple(
        str(item["title"]) for item in updated_checklist if item["status"] == "failed"
    )
    passed_count = sum(item["passed"] for item in updated_checklist)
    return updated_checklist, passed_count, len(updated_checklist), failed_titles


def evaluate_radar_candidate_lifecycle(
    candidate: Mapping[str, Any],
    *,
    current_price: Decimal,
    recent_candles_1h: Sequence[Mapping[str, Any]] = (),
    ema50_4h: Decimal | None = None,
    now: datetime | None = None,
    max_age_hours: float = 72.0,
    continuation_gates: tuple[bool | None, ...] | None = None,
    recovery_gates: tuple[bool | None, ...] | None = None,
    recovery_details: tuple[str, ...] | None = None,
) -> RadarLifecycleDecision:
    """Evaluate whether a previously discovered radar candidate remains an active trade setup
    and re-verify its 5/5 (L1–L6) institutional screening gates against live price action.
    """
    ref_now = now or datetime.now(UTC)
    direction = str(candidate.get("direction", "long")).lower()
    is_short = direction == "short"

    stop = _to_dec(candidate.get("suggested_stop"))
    entry = _to_dec(candidate.get("suggested_entry")) or _to_dec(candidate.get("pullback_entry"))
    trigger = _to_dec(candidate.get("breakout_trigger"))
    poc = _to_dec(candidate.get("poc_price")) or _to_dec(candidate.get("poc_30d"))
    val = _to_dec(candidate.get("val_price")) or _to_dec(candidate.get("val_30d"))
    vah = _to_dec(candidate.get("vah_price")) or _to_dec(candidate.get("vah_30d"))

    evaluated_at = _parse_iso(candidate.get("first_seen_at")) or _parse_iso(
        candidate.get("evaluated_at")
    )
    prior_trigger = _parse_iso(candidate.get("radar_triggered_at"))
    triggered_at = (
        prior_trigger if prior_trigger and evaluated_at is not None
        and evaluated_at <= prior_trigger <= ref_now else None
    )
    raw_prior_basis = str(candidate.get("radar_trigger_basis", "")).lower()
    locked_entry = _to_dec(candidate.get("radar_trigger_entry"))
    locked_stop = _to_dec(candidate.get("radar_trigger_stop"))
    has_locked_reference = (
        triggered_at is not None and raw_prior_basis in ("retest", "breakout")
        and locked_entry is not None and locked_stop is not None
        and locked_entry > 0 and locked_stop > 0
        and (locked_stop > locked_entry if is_short else locked_stop < locked_entry)
    )
    if has_locked_reference:
        stop = locked_stop

    raw_targets = candidate.get("suggested_targets") or ()
    targets: list[Decimal] = []
    if isinstance(raw_targets, (list, tuple)):
        for t in raw_targets:
            td = _to_dec(t)
            if td is not None and td > Decimal("0"):
                targets.append(td)

    if (
        not current_price.is_finite()
        or current_price <= Decimal("0")
        or stop is None
        or stop <= Decimal("0")
    ):
        return RadarLifecycleDecision(
            retained=False,
            state=RadarLifecycleState.INVALIDATED,
            label="INVALIDATED",
            reason="missing_price_or_stop",
            current_price=current_price,
        )

    eval_epoch = int(evaluated_at.timestamp()) if evaluated_at else 0

    invalid_discovery = evaluated_at is None or evaluated_at > ref_now
    # A candle overlapping discovery cannot establish post-discovery event ordering.
    post_scan_candles = [
        c
        for c in recent_candles_1h
        if not invalid_discovery
        and int(c.get("opened_time", int(c.get("time", 0)) - 3600)) >= eval_epoch
        and int(c.get("time", 0)) <= int(ref_now.timestamp())
        and (_to_dec(c.get("low")) or Decimal("0")) > 0
    ]

    # Locked stops apply after the observed trigger. An overlapping candle cannot
    # establish whether its wick preceded that observation.
    stop_candles = post_scan_candles
    if has_locked_reference and triggered_at is not None:
        stop_candles = [c for c in post_scan_candles
                        if int(c.get("opened_time", int(c["time"]) - 3600)) >= triggered_at.timestamp()]

    # 1. Hard Stop-Loss Invalidation Check (Spot Price + Post-Scan Candle Extremes)
    if not is_short:
        post_lows = _extract_decimals_from_candles(stop_candles, "low")
        min_low_since_scan = min(post_lows) if post_lows else current_price
        if current_price <= stop or min_low_since_scan <= stop:
            return RadarLifecycleDecision(
                retained=False,
                state=RadarLifecycleState.INVALIDATED,
                label="STOP HIT",
                reason="long_stop_loss_breached",
                current_price=current_price,
            )
        # Also invalidate if a core Long setup has neither triggered nor held structure and broke >3.5% below VAL & EMA50
        # (RECOVERY_RECLAIM_LONG originates below 4H EMA50 / 30d POC and enforces its own frozen stop & R1 pivot/EMA20 gate)
        if (
            candidate.get("setup_label") != "RECOVERY_RECLAIM_LONG"
            and val is not None
            and val > Decimal("0")
            and current_price < val * Decimal("0.965")
            and (ema50_4h is not None and current_price < ema50_4h * Decimal("0.965"))
        ):
            return RadarLifecycleDecision(
                retained=False,
                state=RadarLifecycleState.INVALIDATED,
                label="VALUE AREA LOST",
                reason="long_value_area_breakdown",
                current_price=current_price,
            )
    else:
        post_highs = _extract_decimals_from_candles(stop_candles, "high")
        max_high_since_scan = max(post_highs) if post_highs else current_price
        # For shorts, also enforce a tight 6% squeeze cap above entry if stop is wide
        stop_entry = locked_entry if has_locked_reference else entry
        effective_short_stop = min(stop, stop_entry * Decimal("1.06")) if stop_entry else stop
        if current_price >= effective_short_stop or max_high_since_scan >= effective_short_stop:
            return RadarLifecycleDecision(
                retained=False,
                state=RadarLifecycleState.INVALIDATED,
                label="SHORT SQUEEZED",
                reason="short_stop_loss_breached",
                current_price=current_price,
            )
        is_climax_short = str(candidate.get("setup_label", "")).upper() in CLIMAX_SHORT_ARCHETYPES
        if (
            not is_climax_short
            and vah is not None
            and vah > Decimal("0")
            and current_price > vah * Decimal("1.035")
            and (ema50_4h is not None and current_price > ema50_4h * Decimal("1.035"))
        ):
            return RadarLifecycleDecision(
                retained=False,
                state=RadarLifecycleState.INVALIDATED,
                label="SHORT INVALIDATED",
                reason="short_reclaimed_above_value",
                current_price=current_price,
            )

    if evaluated_at is None or evaluated_at > ref_now:
        return RadarLifecycleDecision(
            retained=True,
            state=RadarLifecycleState.ACTIVE_COIL,
            label="DATA UNAVAILABLE",
            reason="invalid_discovery_timestamp",
            current_price=current_price,
            gates_passed=0,
        )

    # 3. Classify Active Trigger State FIRST so profit-expansion tolerances only apply
    # after an observed trigger (never on pending unfilled retest setups).
    # Trigger is an observed setup event, never an assertion of an executed fill.
    execution_mode = str(candidate.get("execution_mode", "")).upper()
    allow_breakout_trigger = execution_mode != "WAIT_FOR_RETEST_ONLY"
    allow_retest_trigger = execution_mode not in ("CEILING_BREAKOUT", "BREAKDOWN_TRIGGER")

    first_trigger_basis = raw_prior_basis if has_locked_reference else None
    deadline = evaluated_at + timedelta(hours=max_age_hours)
    # Previously triggered legacy episodes with no saved basis stay unknown.
    # New episodes select their basis once and persist it through the service.
    if triggered_at is None:
        for c in sorted(post_scan_candles, key=lambda c: int(c["time"])):
            if int(c["time"]) > int(deadline.timestamp()):
                continue
            high, low, close = (_to_dec(c.get(k)) for k in ("high", "low", "close"))
            breakout = (
                allow_breakout_trigger
                and trigger is not None
                and (
                    (not is_short and high is not None and high >= trigger)
                    or (is_short and low is not None and low <= trigger)
                )
            )
            # A retest requires the entire candle to open after discovery and close
            # favorably after touching entry; this is an observation, not a fill model.
            retest = (
                allow_retest_trigger
                and entry is not None
                and high is not None
                and low is not None
                and close is not None
                and (
                    (not is_short and low <= entry and close >= entry * Decimal("1.018"))
                    or (is_short and high >= entry and close <= entry * Decimal("0.982"))
                )
            )
            if breakout or retest:
                triggered_at = datetime.fromtimestamp(int(c["time"]), UTC)
                first_trigger_basis = "retest" if retest else "breakout"
                break
    if (
        triggered_at is None
        and ref_now < deadline
        and allow_breakout_trigger
        and trigger is not None
        and ((not is_short and current_price >= trigger) or (is_short and current_price <= trigger))
    ):
        triggered_at = ref_now
        first_trigger_basis = "breakout"

    trigger_entry_basis = locked_entry if has_locked_reference else None
    trigger_stop_basis = locked_stop if has_locked_reference else None
    if first_trigger_basis is not None and not has_locked_reference:
        trigger_entry_basis = trigger if first_trigger_basis == "breakout" else entry
        trigger_stop_basis = _to_dec(candidate.get(f"{first_trigger_basis}_stop")) or stop
        if (trigger_entry_basis is None or trigger_stop_basis is None
                or trigger_entry_basis <= 0 or trigger_stop_basis <= 0
                or (trigger_stop_basis <= trigger_entry_basis if is_short
                    else trigger_stop_basis >= trigger_entry_basis)):
            first_trigger_basis = None
            trigger_entry_basis = trigger_stop_basis = None

    # A newly observed trigger must enforce its own stop in this evaluation too.
    if not has_locked_reference and triggered_at is not None and trigger_stop_basis is not None:
        after_trigger = [c for c in post_scan_candles
                         if int(c.get("opened_time", int(c["time"]) - 3600)) >= triggered_at.timestamp()]
        effective_stop = trigger_stop_basis
        if is_short and trigger_entry_basis is not None:
            effective_stop = min(effective_stop, trigger_entry_basis * Decimal("1.06"))
        extremes = _extract_decimals_from_candles(after_trigger, "high" if is_short else "low")
        stopped = (max([current_price, *extremes]) >= effective_stop if is_short
                   else min([current_price, *extremes]) <= effective_stop)
        if stopped:
            return RadarLifecycleDecision(
                retained=False,
                state=RadarLifecycleState.INVALIDATED,
                label="SHORT SQUEEZED" if is_short else "STOP HIT",
                reason="short_stop_loss_breached" if is_short else "long_stop_loss_breached",
                current_price=current_price,
                triggered_at=triggered_at.isoformat(),
                trigger_basis=first_trigger_basis,
                trigger_entry_price=trigger_entry_basis,
                trigger_stop_price=trigger_stop_basis,
            )

    # 4. Re-Verify Live System Gates (5/5 Checklist) using observed trigger state & locked entry basis
    candidate_age_hours = (
        (ref_now - evaluated_at).total_seconds() / 3600.0
        if evaluated_at is not None and evaluated_at <= ref_now
        else None
    )
    updated_checklist, gates_passed, gates_total, failed_gates = reverify_candidate_gates(
        candidate,
        current_price=current_price,
        recent_candles_1h=recent_candles_1h,
        ema50_4h=ema50_4h,
        continuation_gates=continuation_gates,
        recovery_gates=recovery_gates,
        recovery_details=recovery_details,
        is_triggered=triggered_at is not None,
        trigger_entry_price=trigger_entry_basis,
        age_hours=candidate_age_hours,
    )

    pnl_pct: Decimal | None = None
    if trigger_entry_basis is not None and trigger_entry_basis > Decimal("0"):
        raw_pnl = (
            ((current_price - trigger_entry_basis) / trigger_entry_basis * Decimal("100"))
            if not is_short
            else ((trigger_entry_basis - current_price) / trigger_entry_basis * Decimal("100"))
        )
        pnl_pct = raw_pnl.quantize(Decimal("0.1"))
    # Expired, never-triggered setups cannot become retrospective target winners.
    if triggered_at is not None or ref_now < deadline:
        # 2. Target Completion Check (Graduated after reaching Target 2 / Target 3)
        if targets:
            completion_target = targets[-1]
            if (
                not is_short
                and max([current_price] + _extract_decimals_from_candles(post_scan_candles, "high"))
                >= completion_target
            ):
                return RadarLifecycleDecision(
                    retained=False,
                    state=RadarLifecycleState.COMPLETED_TARGET,
                    label="TARGET HIT",
                    reason="long_final_target_reached",
                    current_price=current_price,
                )
            if (
                is_short
                and min([current_price] + _extract_decimals_from_candles(post_scan_candles, "low"))
                <= completion_target
            ):
                return RadarLifecycleDecision(
                    retained=False,
                    state=RadarLifecycleState.COMPLETED_TARGET,
                    label="TARGET HIT",
                    reason="short_final_target_reached",
                    current_price=current_price,
                )

    if triggered_at is not None:
        return RadarLifecycleDecision(
            retained=True,
            state=RadarLifecycleState.IN_ACTIVE_TRADE,
            label="TRIGGER OBSERVED",
            reason="setup_trigger_observed",
            current_price=current_price,
            pnl_from_entry_pct=pnl_pct,
            gates_passed=gates_passed,
            gates_total=gates_total,
            failed_gates=failed_gates,
            updated_checklist=updated_checklist,
            triggered_at=triggered_at.isoformat(),
            trigger_basis=first_trigger_basis or "unknown",
            trigger_entry_price=trigger_entry_basis,
            trigger_stop_price=trigger_stop_basis,
        )
    at_entry_shelf = any(
        level is not None and level > 0 and abs(current_price - level) / level <= tolerance
        for level, tolerance in ((entry, Decimal("0.018")), (poc, Decimal("0.012")))
    )
    # 5. Expiration Check (Only expire if not in an active triggered trade and older than max_age_hours)
    if evaluated_at is not None:
        age_hours = (ref_now - evaluated_at).total_seconds() / 3600.0
        if age_hours >= max_age_hours:
            return RadarLifecycleDecision(
                retained=False,
                state=RadarLifecycleState.EXPIRED,
                label="EXPIRED",
                reason="max_retention_age_exceeded",
                current_price=current_price,
                gates_passed=gates_passed,
                gates_total=gates_total,
                failed_gates=failed_gates,
                updated_checklist=updated_checklist,
            )

    if at_entry_shelf:
        return RadarLifecycleDecision(
            retained=True,
            state=RadarLifecycleState.AT_ENTRY_ZONE,
            label="AT ENTRY ZONE",
            reason="testing_limit_entry_shelf",
            current_price=current_price,
            pnl_from_entry_pct=pnl_pct,
            gates_passed=gates_passed,
            gates_total=gates_total,
            failed_gates=failed_gates,
            updated_checklist=updated_checklist,
        )

    coil_label = (
        "ACTIVE COIL"
        if gates_passed == gates_total
        else f"COIL ({gates_passed}/{gates_total} GATES)"
    )
    return RadarLifecycleDecision(
        retained=True,
        state=RadarLifecycleState.ACTIVE_COIL,
        label=coil_label,
        reason="holding_structure_above_stop",
        current_price=current_price,
        pnl_from_entry_pct=pnl_pct,
        gates_passed=gates_passed,
        gates_total=gates_total,
        failed_gates=failed_gates,
        updated_checklist=updated_checklist,
    )


def same_radar_episode(previous: Mapping[str, Any], fresh: Mapping[str, Any]) -> bool:
    """A continuing thesis retains its original bracket, discovery time and events.

    A changed direction or archetype is a new thesis. An active original thesis must
    be reconciled before it can be replaced. Score/price drift alone is not renewal.
    """
    if previous.get("setup_label") == "BREAKDOWN_RETEST_SHORT":
        left = previous.get("continuation_evidence") or {}
        right = fresh.get("continuation_evidence") or {}
        if not isinstance(left, dict) or not isinstance(right, dict):
            return False
        lp, rp = left.get("plan"), right.get("plan")
        if not isinstance(lp, dict) or not isinstance(rp, dict):
            return False
        anchors = ("support", "support_known_at", "break_at", "rejection_at")
        if (left.get("policy_version") != right.get("policy_version")
                or any(lp.get(k) != rp.get(k) for k in anchors)):
            return False
    if previous.get("setup_label") == "RECOVERY_RECLAIM_LONG":
        left = previous.get("recovery_evidence") or {}
        right = fresh.get("recovery_evidence") or {}
        if not isinstance(left, dict) or not isinstance(right, dict):
            return False
        lp, rp = left.get("plan"), right.get("plan")
        if not isinstance(lp, dict) or not isinstance(rp, dict):
            return False
        rec_anchors = ("pivot", "break_bar_at")
        if (
            left.get("policy_version") != right.get("policy_version")
            or any(lp.get(k) != rp.get(k) for k in rec_anchors)
        ):
            return False
    return all(previous.get(k) == fresh.get(k) for k in ("symbol", "direction", "setup_label"))
