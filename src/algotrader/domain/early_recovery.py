"""Point-in-time, opt-in Early-Recovery candidate family (`early-recovery-radar-v1`).

Pure domain evaluation with zero network or execution capabilities:
- Stage 1 (`derivatives=None`) evaluates R1-R4 purely on closed candles, quote, and BTC
  alignment, keeping R5 explicitly `None` (unknown — never invented or pre-failed).
- Stage 2 (`derivatives!=None`) requires fresh, non-degraded OI/funding/taker evidence
  proving net-new buyer positioning (blocking short-covering-only bounces, acute long
  liquidations, and crowded positive funding).
- Overhead `4H EMA50`, `30d POC`, `30d VAH`, and `30d High` remain explicit structural
  resistance bounds; T1 is never pushed past the nearest overhead structural resistance
  merely to manufacture R:R.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from algotrader.domain.discovery_features import discovery_temporal_features
from algotrader.domain.execution_brackets import valid_execution_prices
from algotrader.domain.indicators import atr, ema, volume_profile
from algotrader.domain.models import Candle, DerivativesSnapshot, Direction, Quote
from algotrader.domain.precision import ceil_to_increment, floor_to_increment

D = Decimal
ZERO = D("0")
POLICY_VERSION = "early-recovery-radar-v1"
EARLY_RECOVERY_SCREENING_POLICY_VERSION = "atlas-v3.1-early-recovery-v1"
RECOVERY_PROBE_CAP = 8

TITLES: tuple[str, str, str, str, str] = (
    "Local Structural Recovery",
    "Controlled Extension",
    "Relative Participation",
    "Analytical Plan Geometry",
    "Positioning Confirmation",
)
STRUCTURAL_TITLES: frozenset[str] = frozenset(TITLES[:4])
FLOW_TITLE: str = TITLES[4]


@dataclass(frozen=True, slots=True)
class EarlyRecoveryPolicy:
    """Predeclared `early-recovery-radar-v1` configuration; any change requires a new version."""

    version: str = POLICY_VERSION
    family_policy_mode: str = "family_v1"  # "family_v1" or "inherited_long" for policy ablation
    min_closed_1h_bars: int = 48
    min_closed_4h_bars: int = 50
    pivot_lookback_bars: int = 3
    recent_low_bars: int = 3
    prior_low_bars: int = 6
    max_ema20_ext_atr4h: Decimal = D("1.25")
    max_pivot_ext_atr4h: Decimal = D("0.85")
    max_bar_range_atr4h: Decimal = D("1.35")
    max_upper_wick_ratio: Decimal = D("0.45")
    min_ret_24h: Decimal = D("-0.030")
    max_ret_24h: Decimal = D("0.095")
    max_quote_overrun_atr4h: Decimal = D("0.35")
    min_btc_rel_6h_pp: Decimal = D("1.0")
    min_turnover_accel_6h: Decimal = D("1.25")
    min_quote_volume_24h: Decimal = D("1200000")
    min_retention_quote_volume_24h: Decimal = D("500000")
    max_spread_bps: Decimal = D("25")
    entry_buffer_atr4h: Decimal = D("0.04")
    stop_buffer_atr4h: Decimal = D("0.15")
    min_stop_atr4h: Decimal = D("0.50")
    max_risk_atr4h: Decimal = D("2.50")
    max_risk_pct: Decimal = D("0.085")
    min_first_res_rr: Decimal = D("1.50")
    min_high30d_rr: Decimal = D("2.00")
    trigger_wait_hours: int = 12
    max_funding_rate: Decimal = D("0.00015")
    min_oi_1h: Decimal = D("-0.025")
    path_a_min_oi_24h: Decimal = D("0.020")
    path_a_min_oi_4h: Decimal = D("0.005")
    path_a_min_taker_4h: Decimal = D("1.05")
    path_a_min_taker_1h: Decimal = D("1.08")
    path_a_floor_taker_4h: Decimal = D("0.99")
    path_b_min_oi_4h: Decimal = D("0.015")
    path_b_min_oi_1h: Decimal = D("0.0")
    path_b_min_taker_4h: Decimal = D("1.02")
    path_b_floor_taker_1h: Decimal = D("1.00")


DEFAULT_EARLY_RECOVERY_POLICY = EarlyRecoveryPolicy()


@dataclass(frozen=True, slots=True)
class EarlyRecoveryPlan:
    pivot: Decimal
    break_bar_at: datetime
    local_low: Decimal
    entry: Decimal
    retest_entry: Decimal
    stop: Decimal
    t1: Decimal
    t2: Decimal
    t3: Decimal
    atr_4h: Decimal
    first_resistance: Decimal
    ema50_4h_ref: Decimal
    poc_30d_ref: Decimal
    r_t1: Decimal
    rr_t2: Decimal
    r_t3: Decimal

    @property
    def targets(self) -> tuple[Decimal, Decimal, Decimal]:
        return (self.t1, self.t2, self.t3)

    @property
    def risk_unit(self) -> Decimal:
        return self.entry - self.stop

    def as_dict(self) -> dict[str, Any]:
        return {
            "pivot": str(self.pivot),
            "break_bar_at": self.break_bar_at.isoformat(),
            "local_low": str(self.local_low),
            "entry": str(self.entry),
            "retest_entry": str(self.retest_entry),
            "stop": str(self.stop),
            "t1": str(self.t1),
            "t2": str(self.t2),
            "t3": str(self.t3),
            "atr_4h": str(self.atr_4h),
            "first_resistance": str(self.first_resistance),
            "ema50_4h_ref": str(self.ema50_4h_ref),
            "poc_30d_ref": str(self.poc_30d_ref),
            "r_t1": str(self.r_t1),
            "rr_t2": str(self.rr_t2),
            "r_t3": str(self.r_t3),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> EarlyRecoveryPlan:
        return cls(
            pivot=D(str(value["pivot"])),
            break_bar_at=datetime.fromisoformat(str(value["break_bar_at"])),
            local_low=D(str(value["local_low"])),
            entry=D(str(value["entry"])),
            retest_entry=D(str(value["retest_entry"])),
            stop=D(str(value["stop"])),
            t1=D(str(value["t1"])),
            t2=D(str(value["t2"])),
            t3=D(str(value["t3"])),
            atr_4h=D(str(value["atr_4h"])),
            first_resistance=D(str(value["first_resistance"])),
            ema50_4h_ref=D(str(value["ema50_4h_ref"])),
            poc_30d_ref=D(str(value["poc_30d_ref"])),
            r_t1=D(str(value["r_t1"])),
            rr_t2=D(str(value["rr_t2"])),
            r_t3=D(str(value["r_t3"])),
        )


@dataclass(frozen=True, slots=True)
class EarlyRecoveryEvaluation:
    plan: EarlyRecoveryPlan | None
    gates: tuple[bool | None, ...]
    details: tuple[str, ...]
    reason: str
    metrics: dict[str, str]

    @property
    def structurally_ready(self) -> bool:
        return self.plan is not None and len(self.gates) == 5 and all(g is True for g in self.gates[:4])

    @property
    def confirmed(self) -> bool:
        return self.structurally_ready and self.gates[4] is True


def _contiguous_closed_bars(raw: Sequence[Candle], now: datetime, seconds: int) -> list[Candle]:
    bars = sorted((c for c in raw if c.closed and c.closed_at <= now), key=lambda c: c.closed_at)
    if not bars or not timedelta(seconds=-5) <= now - bars[-1].closed_at <= timedelta(seconds=seconds + 300):
        return []
    for c in bars:
        if (
            c.opened_at.tzinfo is None
            or c.opened_at.utcoffset() is None
            or c.closed_at.tzinfo is None
            or c.closed_at.utcoffset() is None
            or not all(x.is_finite() for x in (c.open, c.high, c.low, c.close, c.volume, c.quote_volume))
            or not 0 < c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high
            or c.volume < 0
            or c.quote_volume < 0
            or abs((c.closed_at - c.opened_at).total_seconds() - seconds) > 2
        ):
            return []
    if any(
        abs((b.opened_at - a.opened_at).total_seconds() - seconds) > 2
        for a, b in zip(bars, bars[1:], strict=False)
    ):
        return []
    return bars


def _evaluate_r5_positioning(
    derivatives: DerivativesSnapshot | None,
    *,
    now: datetime,
    policy: EarlyRecoveryPolicy,
    is_retention: bool,
    is_triggered_in_profit: bool,
) -> tuple[bool | None, str, dict[str, str]]:
    """Evaluate R5 Positioning Confirmation. Unknown/stale/degraded telemetry returns None."""
    if derivatives is None:
        return None, "Pending Stage-2 derivatives enrichment", {}
    if (
        derivatives.degraded_telemetry
        or derivatives.observed_at.tzinfo is None
        or derivatives.observed_at.utcoffset() is None
        or not -5.0 <= (now - derivatives.observed_at).total_seconds() <= 300.0
    ):
        return None, "Fresh non-degraded derivatives telemetry unavailable", {}

    funding = derivatives.funding_rate
    oi1 = derivatives.open_interest_change_1h
    oi4 = derivatives.open_interest_change_4h
    oi24 = derivatives.open_interest_change_24h
    tk1 = derivatives.taker_buy_sell_ratio
    tk4 = derivatives.taker_buy_sell_ratio_4h

    if (
        funding is None
        or oi1 is None
        or oi4 is None
        or not all(x.is_finite() for x in (funding, oi1, oi4))
        or (tk4 is None and tk1 is None)
        or (tk4 is not None and not tk4.is_finite())
        or (tk1 is not None and not tk1.is_finite())
    ):
        return None, "Incomplete OI/funding/taker fields", {}

    metrics: dict[str, str] = {
        "funding_bps": f"{float(funding * D('10000')):+.2f}",
        "oi_change_1h_pct": f"{float(oi1 * D('100')):+.2f}%",
        "oi_change_4h_pct": f"{float(oi4 * D('100')):+.2f}%",
    }
    if oi24 is not None and oi24.is_finite():
        metrics["oi_change_24h_pct"] = f"{float(oi24 * D('100')):+.2f}%"
    if tk4 is not None:
        metrics["taker_ratio_4h"] = f"{float(tk4):.4f}"
    if tk1 is not None:
        metrics["taker_ratio_1h"] = f"{float(tk1):.4f}"
    eff_taker = max(x for x in (tk1, tk4) if x is not None)
    metrics["taker_buy_sell_ratio"] = f"{float(eff_taker):.4f}"

    if is_retention:
        # Retention health check: acute liquidation/crowding vetoes + active flow floor
        if oi1 <= D("-0.050"):
            return False, f"Acute 1h OI liquidation ({metrics['oi_change_1h_pct']})", metrics
        if funding >= D("0.00020"):
            return False, f"Funding crowded ({metrics['funding_bps']} bps)", metrics
        if oi4 < D("-0.015") and not is_triggered_in_profit:
            return False, f"4h OI unwinding ({metrics['oi_change_4h_pct']})", metrics
        min_ret_taker = D("0.85") if is_triggered_in_profit else D("0.92")
        if eff_taker < min_ret_taker:
            return (
                False,
                f"Orderflow weakened (4h OI {metrics['oi_change_4h_pct']}, 1h OI {metrics['oi_change_1h_pct']}, funding {metrics['funding_bps']} bps, taker {float(eff_taker):.2f})",
                metrics,
            )
        return (
            True,
            f"4h OI {metrics['oi_change_4h_pct']}, 1h OI {metrics['oi_change_1h_pct']}, funding {metrics['funding_bps']} bps, taker {float(eff_taker):.2f}",
            metrics,
        )

    # Fresh initial discovery confirmation: block acute liquidations, crowded longs, and short-covering-only bounces
    if oi1 < policy.min_oi_1h:
        return False, f"Acute 1h OI flush ({metrics['oi_change_1h_pct']} < -2.5%)", metrics
    if funding > policy.max_funding_rate:
        return False, f"Crowded positive funding ({metrics['funding_bps']} bps > +1.50 bps)", metrics

    path_a = bool(
        oi24 is not None
        and oi24.is_finite()
        and oi24 >= policy.path_a_min_oi_24h
        and oi4 >= policy.path_a_min_oi_4h
        and (
            (tk4 is not None and tk4 >= policy.path_a_min_taker_4h)
            or (
                tk1 is not None
                and tk1 >= policy.path_a_min_taker_1h
                and (tk4 is None or tk4 >= policy.path_a_floor_taker_4h)
            )
        )
    )
    path_b = bool(
        oi4 >= policy.path_b_min_oi_4h
        and oi1 >= policy.path_b_min_oi_1h
        and tk4 is not None
        and tk4 >= policy.path_b_min_taker_4h
        and (tk1 is None or tk1 >= policy.path_b_floor_taker_1h)
    )
    if not (path_a or path_b):
        return (
            False,
            f"Insufficient net-new buyer positioning (4h OI {metrics['oi_change_4h_pct']}, 1h OI {metrics['oi_change_1h_pct']}, 4h taker {metrics.get('taker_ratio_4h', 'N/A')})",
            metrics,
        )
    path_tag = "24h+4h OI inflection" if path_a else "4h+1h OI expansion"
    return (
        True,
        f"Confirmed {path_tag} (4h OI {metrics['oi_change_4h_pct']}, funding {metrics['funding_bps']} bps, 4h taker {metrics.get('taker_ratio_4h', 'N/A')})",
        metrics,
    )


def evaluate_early_recovery(
    candles_1h: Sequence[Candle],
    candles_4h: Sequence[Candle],
    btc_candles_1h: Sequence[Candle],
    *,
    now: datetime,
    quote: Quote,
    tick: Decimal,
    derivatives: DerivativesSnapshot | None = None,
    is_btc_cascade: bool = False,
    policy: EarlyRecoveryPolicy = DEFAULT_EARLY_RECOVERY_POLICY,
    existing_plan: EarlyRecoveryPlan | None = None,
    is_triggered: bool = False,
    trigger_entry_price: Decimal | None = None,
) -> EarlyRecoveryEvaluation:
    """Evaluate the 5 explicit Early-Recovery gates (`R1..R5`) for discovery or retention."""

    def _fail_all(reason: str, *, r5: bool | None = None) -> EarlyRecoveryEvaluation:
        return EarlyRecoveryEvaluation(
            plan=None,
            gates=(False, False, False, False, r5),
            details=(reason,) * 4 + ("Unavailable" if r5 is None else reason,),
            reason=reason,
            metrics={},
        )

    if is_triggered and existing_plan is None:
        return _fail_all("trigger_requires_frozen_plan")
    if now.tzinfo is None or now.utcoffset() is None:
        return _fail_all("naive_evaluation_time")
    if is_btc_cascade and existing_plan is None:
        return _fail_all("btc_velocity_cascade_veto")

    if (
        quote.observed_at.tzinfo is None
        or quote.observed_at.utcoffset() is None
        or not quote.is_valid_book
        or not quote.is_fresh_at(now, max_age_seconds=300.0, max_future_seconds=5.0)
        or not tick.is_finite()
        or tick <= 0
        or not quote.last.is_finite()
        or quote.last <= 0
        or not quote.quote_volume_24h.is_finite()
        or quote.spread_bps < 0
    ):
        return _fail_all("quote_or_tick_unavailable")

    h = _contiguous_closed_bars(candles_1h, now, 3600)
    q = _contiguous_closed_bars(candles_4h, now, 14400)
    if len(h) < policy.min_closed_1h_bars or len(q) < policy.min_closed_4h_bars:
        return _fail_all("closed_history_unavailable")

    atr_4h = atr(q, 14)[-1]
    ema50_4h = ema([c.close for c in q], 50)[-1]
    ema20_1h = ema([c.close for c in h], 20)[-1]
    vp = volume_profile(q[-180:], tick_size=tick)
    poc_30d, vah_30d = vp.poc_price, vp.vah_price
    high_30d = max(c.high for c in q[-180:])
    if any(not v.is_finite() or v <= 0 for v in (atr_4h, ema50_4h, ema20_1h, poc_30d, vah_30d, high_30d)):
        return _fail_all("structural_indicators_unavailable")

    close = h[-1].close
    ret_24h = (close - h[-24].open) / h[-24].open if h[-24].open > 0 else ZERO

    # Retention revalidation on an immutable frozen plan
    if existing_plan is not None:
        plan = existing_plan
        if (
            plan.break_bar_at.tzinfo is None
            or plan.break_bar_at > now
            or not all(
                v.is_finite() and v > 0
                for v in (plan.pivot, plan.entry, plan.stop, plan.t1, plan.t2, plan.t3, plan.atr_4h)
            )
            or not valid_execution_prices(Direction.LONG, plan.entry, plan.stop, plan.targets)
        ):
            return _fail_all("invalid_frozen_recovery_plan")

        after = [c for c in h if c.opened_at >= plan.break_bar_at]
        in_profit = bool(
            is_triggered
            and trigger_entry_price is not None
            and trigger_entry_price > 0
            and quote.last > trigger_entry_price * D("1.02")
        )
        r1 = (
            quote.last > plan.stop
            and all(c.low > plan.stop for c in after)
            and (
                is_triggered
                or (
                    close >= ema20_1h * D("0.992")
                    and close >= plan.pivot * D("0.985")
                )
            )
        )
        r1_detail = (
            f"Holding recovery base above pivot ${float(plan.pivot):.4f} & 1H EMA20 ${float(ema20_1h):.4f}"
            if r1
            else f"Lost recovery pivot (${float(plan.pivot):.4f}) or 1H EMA20 (${float(ema20_1h):.4f})"
        )

        not_expired_wait = is_triggered or (now - plan.break_bar_at <= timedelta(hours=policy.trigger_wait_hours))
        r2 = (
            not_expired_wait
            and quote.last > plan.stop
            and (
                is_triggered
                or (
                    quote.last < plan.t3
                    and quote.last - plan.entry <= policy.max_quote_overrun_atr4h * plan.atr_4h
                )
            )
        )
        r2_detail = (
            "Extension bounded within frozen recovery plan"
            if r2
            else ("Trigger wait window elapsed" if not not_expired_wait else "Price outran untriggered entry bound")
        )

        btc_closed = _contiguous_closed_bars(btc_candles_1h, now, 3600) if btc_candles_1h else []
        if btc_closed:
            temp = discovery_temporal_features(h, btc_closed)
            if temp.get("available") is True:
                rs6 = D(str(temp["btc_relative_6h_pp"]))
                va6 = D(str(temp["turnover_acceleration_6h_vs_prior18h"]))
                r3: bool | None = True if in_profit else bool(va6 >= D("0.90") and rs6 >= D("-1.5"))
                r3_detail = f"6h RS vs BTC {float(rs6):+.1f}pp, turnover accel {float(va6):.2f}x"
            else:
                r3 = True if in_profit else None
                r3_detail = "BTC relative participation unavailable"
        else:
            # In lifecycle reconciliation without BTC feed, check self turnover retention >= 0.85x
            prev_t = sum((c.quote_volume for c in h[-24:-6]), ZERO) / D("18")
            rec_t = sum((c.quote_volume for c in h[-6:]), ZERO) / D("6")
            if prev_t > 0:
                va6 = rec_t / prev_t
                r3 = True if in_profit else bool(va6 >= D("0.85") and ret_24h >= D("-0.05"))
                r3_detail = f"6h turnover retention {float(va6):.2f}x, 24h ret {float(ret_24h*100):+.1f}%"
            else:
                r3 = None
                r3_detail = "Turnover baseline unavailable"

        r4 = bool(
            quote.quote_volume_24h >= policy.min_retention_quote_volume_24h
            and ZERO <= quote.spread_bps <= policy.max_spread_bps
        )
        r4_detail = (
            f"Vol ${float(quote.quote_volume_24h)/1e6:.2f}M, spread {float(quote.spread_bps):.1f} bps, R:R {float(plan.rr_t2):.2f}"
            if r4
            else f"Liquidity/spread degraded (Vol ${float(quote.quote_volume_24h)/1e6:.2f}M, spread {float(quote.spread_bps):.1f} bps)"
        )

        r5, r5_detail, r5_metrics = _evaluate_r5_positioning(
            derivatives,
            now=now,
            policy=policy,
            is_retention=True,
            is_triggered_in_profit=in_profit,
        )
        gates = (r1, r2, r3, r4, r5)
        details = (r1_detail, r2_detail, r3_detail, r4_detail, r5_detail)
        failed_idx = next((idx for idx, g in enumerate(gates, 1) if g is not True), None)
        reason = "confirmed" if failed_idx is None else f"R{failed_idx}_failed_or_unknown"
        return EarlyRecoveryEvaluation(
            plan=plan,
            gates=gates,
            details=details,
            reason=reason,
            metrics=r5_metrics,
        )

    # --- Fresh Discovery Evaluation (`existing_plan=None`) ---
    # R1: Local Structural Recovery (below 4H EMA50 or 30d POC, reclaiming 3-bar pivot + 1H EMA20 + higher-low base)
    below_long_term_ref = (close < ema50_4h) or (close < poc_30d)
    pivot = max(c.high for c in h[-(policy.pivot_lookback_bars + 1) : -1])
    recent_low = min(c.low for c in h[-policy.recent_low_bars :])
    prior_low = min(c.low for c in h[-(policy.recent_low_bars + policy.prior_low_bars) : -policy.recent_low_bars])
    green_reclaim_bar = h[-1].close > h[-1].open

    severe_crash_collapse = False
    if len(h) >= 168 and h[-168].open > 0:
        ret_7d = (close - h[-168].open) / h[-168].open
        low_48h = min(c.low for c in h[-48:])
        if ret_7d <= D("-0.35") and close < low_48h * D("1.02"):
            severe_crash_collapse = True

    r1_ok = bool(
        below_long_term_ref
        and close > pivot
        and close >= ema20_1h
        and recent_low > prior_low
        and green_reclaim_bar
        and not severe_crash_collapse
    )
    if not r1_ok:
        r1_reason = (
            "above_ema50_and_poc_not_recovery_domain"
            if not below_long_term_ref
            else "severe_markdown_collapse"
            if severe_crash_collapse
            else "local_pivot_or_higher_low_absent"
        )
        return _fail_all(r1_reason)
    r1_detail = (
        f"Reclaimed 3h pivot ${float(pivot):.4f} & 1H EMA20 ${float(ema20_1h):.4f} with higher low "
        f"(below {'4H EMA50' if close < ema50_4h else '30d POC'})"
    )

    # R4 Plan Construction first so R2 can also verify quote overrun vs planned entry
    entry = ceil_to_increment(max(close, pivot) + policy.entry_buffer_atr4h * atr_4h, tick)
    stop_buf = policy.stop_buffer_atr4h * atr_4h
    max_allowed_risk = min(policy.max_risk_atr4h * atr_4h, policy.max_risk_pct * entry)
    twelve_bar_low = min(c.low for c in h[-12:])
    six_bar_low = min(c.low for c in h[-6:])
    if (entry - (twelve_bar_low - stop_buf)) <= max_allowed_risk:
        candidate_low = twelve_bar_low
    elif (entry - (six_bar_low - stop_buf)) <= max_allowed_risk:
        candidate_low = six_bar_low
    else:
        candidate_low = recent_low
    min_structural_dist = min(
        max_allowed_risk * D("0.92"),
        max(policy.min_stop_atr4h * atr_4h, D("0.95") * atr_4h, entry * D("0.042")),
    )
    stop = floor_to_increment(
        min(candidate_low - stop_buf, entry - min_structural_dist),
        tick,
    )
    if entry - stop > max_allowed_risk and (entry - max_allowed_risk) < entry - tick:
        stop = ceil_to_increment(entry - max_allowed_risk, tick)
    risk = entry - stop

    overhead_levels = sorted(
        lvl for lvl in (ema50_4h, poc_30d, vah_30d, high_30d) if lvl > entry
    )
    first_res = overhead_levels[0] if overhead_levels else high_30d

    # R2: Controlled Extension (4H ATR bounded + upper-wick bull-trap veto + quote overrun check)
    bar_range = h[-1].high - h[-1].low
    upper_wick = h[-1].high - h[-1].close
    wick_ok = True
    if bar_range > D("0.50") * atr_4h and bar_range > 0:
        wick_ok = (upper_wick / bar_range) <= policy.max_upper_wick_ratio

    ema20_ext = close - ema20_1h
    pivot_ext = close - pivot
    r2_ok = bool(
        ZERO <= ema20_ext <= policy.max_ema20_ext_atr4h * atr_4h
        and ZERO < pivot_ext <= policy.max_pivot_ext_atr4h * atr_4h
        and bar_range <= policy.max_bar_range_atr4h * atr_4h
        and wick_ok
        and policy.min_ret_24h <= ret_24h <= policy.max_ret_24h
        and quote.last > stop
        and (quote.last - entry) <= policy.max_quote_overrun_atr4h * atr_4h
        and (quote.last - ema20_1h) <= (policy.max_ema20_ext_atr4h + D("0.10")) * atr_4h
    )
    r2_detail = (
        f"24h ret {float(ret_24h*100):+.1f}%, EMA20 ext {float(ema20_ext/atr_4h):.2f}x 4H ATR, bar {float(bar_range/atr_4h):.2f}x 4H ATR"
        if r2_ok
        else "Extension, 24h return, upper-wick rejection, or quote overrun exceeded bounds"
    )

    # R3: Relative Participation (aligned closed 1H BTC benchmark + warmed 24h turnover baseline)
    btc_closed = _contiguous_closed_bars(btc_candles_1h, now, 3600)
    temp = discovery_temporal_features(h, btc_closed)
    if temp.get("available") is True:
        rs6 = D(str(temp["btc_relative_6h_pp"]))
        va6 = D(str(temp["turnover_acceleration_6h_vs_prior18h"]))
        r3_ok = bool(rs6 >= policy.min_btc_rel_6h_pp and va6 >= policy.min_turnover_accel_6h)
        r3_detail = (
            f"6h RS vs BTC {float(rs6):+.2f}pp (>= +1.0pp), 6h/18h turnover {float(va6):.2f}x (>= 1.25x)"
            if r3_ok
            else f"Insufficient relative participation (6h RS {float(rs6):+.2f}pp, accel {float(va6):.2f}x)"
        )
    else:
        rs6 = ZERO
        va6 = ZERO
        r3_ok = False
        r3_detail = f"Aligned BTC/turnover baseline unavailable ({temp.get('reason', 'unavailable')})"

    # R4: Executable Analytical Geometry (strict liquidity/spread + real overhead resistance room; T1 never pushed past first_res)
    liq_ok = (
        quote.quote_volume_24h >= policy.min_quote_volume_24h
        and ZERO <= quote.spread_bps <= policy.max_spread_bps
    )
    risk_ok = ZERO < risk <= max_allowed_risk and stop > ZERO
    room_ok = (
        risk > ZERO
        and (first_res - entry) >= policy.min_first_res_rr * risk
        and (high_30d - entry) >= policy.min_high30d_rr * risk
    )

    plan_obj: EarlyRecoveryPlan | None = None
    r4_ok = False
    r4_detail = "Invalid analytical recovery bracket"
    if liq_ok and risk_ok and room_ok:
        # T1 stops at or before nearest overhead structural resistance (EMA50_4h / POC_30d / VAH_30d / High_30d)
        t1 = floor_to_increment(min(first_res, entry + D("2.00") * risk), tick)
        t2_cap = max(t1 + tick, min(high_30d, entry + D("3.00") * risk))
        t2 = floor_to_increment(t2_cap, tick)
        if t2 <= t1:
            t2 = ceil_to_increment(t1 + tick, tick)
        t3_cap = max(t2 + tick, min(high_30d, entry + D("4.00") * risk))
        t3 = floor_to_increment(t3_cap, tick)
        if t3 <= t2:
            t3 = ceil_to_increment(t2 + tick, tick)

        if (
            t3 <= high_30d + tick
            and valid_execution_prices(Direction.LONG, entry, stop, (t1, t2, t3))
        ):
            r_t1 = ((t1 - entry) / risk).quantize(D("0.01"))
            rr_t2 = ((t2 - entry) / risk).quantize(D("0.01"))
            r_t3 = ((t3 - entry) / risk).quantize(D("0.01"))
            if r_t1 >= D("1.45") and rr_t2 >= D("2.00"):
                plan_obj = EarlyRecoveryPlan(
                    pivot=pivot,
                    break_bar_at=h[-1].closed_at,
                    local_low=candidate_low,
                    entry=entry,
                    retest_entry=floor_to_increment(pivot, tick),
                    stop=stop,
                    t1=t1,
                    t2=t2,
                    t3=t3,
                    atr_4h=atr_4h,
                    first_resistance=first_res,
                    ema50_4h_ref=ema50_4h,
                    poc_30d_ref=poc_30d,
                    r_t1=r_t1,
                    rr_t2=rr_t2,
                    r_t3=r_t3,
                )
                r4_ok = True
                r4_detail = (
                    f"Risk {float(risk/entry*100):.2f}%, room to overhead res ${float(first_res):.4f} "
                    f"({float(r_t1):.2f}R T1 / {float(rr_t2):.2f}R T2), Vol ${float(quote.quote_volume_24h)/1e6:.2f}M"
                )
    if not r4_ok:
        if not liq_ok:
            r4_detail = f"Liquidity/spread below threshold (Vol ${float(quote.quote_volume_24h)/1e6:.2f}M, spread {float(quote.spread_bps):.1f} bps)"
        elif not risk_ok:
            r4_detail = f"Local structural stop too wide ({float(risk/max(entry, D('1e-8'))*100):.1f}% > 8.5% or 2.5x 4H ATR)"
        elif not room_ok:
            r4_detail = f"Overhead structural resistance (${float(first_res):.4f}) leaves < 1.5R room"

    r5_ok, r5_detail, r5_metrics = _evaluate_r5_positioning(
        derivatives,
        now=now,
        policy=policy,
        is_retention=False,
        is_triggered_in_profit=False,
    )
    metrics = {
        "btc_relative_6h_pp": f"{float(rs6):+.2f}",
        "turnover_accel_6h": f"{float(va6):.2f}",
        "ret_24h_pct": f"{float(ret_24h*100):+.2f}%",
        **r5_metrics,
    }
    fresh_gates: tuple[bool | None, ...] = (r1_ok, r2_ok, r3_ok, r4_ok, r5_ok)
    details = (r1_detail, r2_detail, r3_detail, r4_detail, r5_detail)
    if not (r1_ok and r2_ok and r3_ok and r4_ok):
        first_fail = next(idx for idx, g in enumerate(fresh_gates[:4], 1) if not g)
        return EarlyRecoveryEvaluation(
            plan=None,
            gates=fresh_gates,
            details=details,
            reason=f"R{first_fail}_failed",
            metrics=metrics,
        )
    reason = "confirmed" if r5_ok is True else "structurally_ready_awaiting_r5" if r5_ok is None else "R5_failed"
    return EarlyRecoveryEvaluation(
        plan=plan_obj,
        gates=fresh_gates,
        details=details,
        reason=reason,
        metrics=metrics,
    )

