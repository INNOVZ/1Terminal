"""Domain logic for the disjoint 5/5 Radar -> 48h Potential Watchlist -> Resolved Ledger state machine.

Institutional design & measurement guarantees:
- ZERO DUPLICATION: Live 5/5 Radar candidates stay in `ON_RADAR` state and never appear
  in the Watchlist while live on the Radar.
- STRUCTURAL FILTERING ON EVICTION: When a candidate leaves the strict 5/5 Radar, it is
  promoted to `WATCHLIST` (for the remaining 48h window from publication) ONLY IF all 4
  structural gates (Trend Baseline, Anti-Chasing, Value Acceptance, Volatility Compression)
  remain positively verified and the eviction was caused solely by a mild soft-flow dip
  (not stop breach, structural break, acute liquidation, or severe orderflow dump).
- IMMEDIATE PRUNING: Any `WATCHLIST` (or `ON_RADAR`) episode that hits `TARGET_REACHED`,
  `STOP_BREACHED`, or reaches its 48-hour clock (`EXPIRED_48H`) is removed immediately from
  the active Watchlist and frozen into the Resolved Performance Ledger.
- CERTIFIED MEASUREMENT INTEGRITY (W1-W5):
  - Never fabricates an expiry endpoint price from stale/missing candles.
  - Only certifies `barrier_order_certified = True` when post-publication candle coverage
    is contiguous from `published_at` up to the barrier hit.
  - Excludes uncertified barrier hits, zero-observation expiries, and legacy unverified
    rows from measured denominators (`measured_resolved`, `target_hit_rate_pct`, `avg_return_pct`).
  - Requires explicit fresh in-window `live_observed_at` timestamps for live quotes.
  - Conservative stop priority applies if both stop and target touch in the same bar.
"""

from __future__ import annotations

import hashlib
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from algotrader.domain.gate_contract import is_soft_only_checklist_failure
from algotrader.domain.indicators import ema
from algotrader.domain.models import Candle

AUTO_WATCH_WINDOW_HOURS = 48
MEASUREMENT_SCHEMA_VERSION = "auto-watch-measurement-v2"
BREAKOUT_ONLY_MODES = frozenset(
    {
        "CEILING_BREAKOUT",
        "BREAKOUT_TRIGGER",
        "BREAKDOWN_TRIGGER",
        "BREAKOUT_EXPANSION",
        "BREAKDOWN_EXPANSION",
    }
)
STRUCTURAL_GATE_TITLES = frozenset(
    {
        "Trend Baseline",
        "Anti-Chasing Validated",
        "Value Acceptance",
        "Volatility Compression",
    }
)
HARD_TERMINATION_STATES = frozenset(
    {
        "COMPLETED_TARGET",
        "EXPIRED",
        "SUPERSEDED",
        "POLICY_CHANGED",
    }
)
HARD_INVALIDATION_REASONS = frozenset(
    {
        "long_stop_loss_breached",
        "short_stop_loss_breached",
        "long_value_area_breakdown",
        "short_reclaimed_above_value",
        "max_retention_age_exceeded",
        "radar_data_unavailable",
        "invalid_discovery_timestamp",
        "missing_price_or_stop",
    }
)


class AutoWatchStatus(StrEnum):
    ON_RADAR = "ON_RADAR"  # Live on 5/5 Radar — NOT duplicated in Watchlist
    WATCHLIST = "WATCHLIST"  # Soft-evicted from Radar, 4/4 structure intact, inside 48h window
    TARGET_REACHED = "TARGET_REACHED"
    STOP_BREACHED = "STOP_BREACHED"
    EXPIRED_48H = "EXPIRED_48H"
    HARD_INVALIDATED = "HARD_INVALIDATED"


class RadarPresence(StrEnum):
    ON_RADAR = "ON_RADAR"
    EVICTED_FROM_RADAR = "EVICTED_FROM_RADAR"


def _to_dec(value: Any) -> Decimal | None:
    if value is None or value == "" or value == "N/A":
        return None
    try:
        cleaned = (
            str(value)
            .replace("%", "")
            .replace("bps", "")
            .replace("$", "")
            .replace("+", "")
            .strip()
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
        if dt.tzinfo is None or dt.utcoffset() is None:
            return None
        return dt.astimezone(UTC)
    except ValueError:
        return None


def directional_return_pct(
    *,
    direction: str,
    reference_price: Decimal,
    price: Decimal,
) -> Decimal:
    if reference_price <= 0 or price <= 0:
        return Decimal("0")
    sign = Decimal("-1") if direction.lower() == "short" else Decimal("1")
    return (sign * (price - reference_price) / reference_price * Decimal("100")).quantize(
        Decimal("0.01")
    )


def _clean_checklist(raw: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, Mapping):
            out.append(
                {
                    "title": str(item.get("title", "")),
                    "passed": bool(item.get("passed") is True),
                    "status": str(
                        item.get("status")
                        or ("passed" if item.get("passed") is True else "failed")
                    ),
                    "detail": str(item.get("detail", "")),
                }
            )
    return tuple(out)


@dataclass(frozen=True, slots=True)
class AutoWatchEpisode:
    """One 48-hour tracked Radar episode across ON_RADAR -> WATCHLIST -> RESOLVED."""

    episode_id: str
    symbol: str
    direction: str
    setup_label: str
    execution_mode: str
    score: Decimal
    tick_size: Decimal
    published_at: datetime
    expires_at: datetime
    signal_price: Decimal
    entry_price: Decimal
    trigger_price: Decimal | None
    stop_price: Decimal
    target_1: Decimal
    target_2: Decimal | None
    target_3: Decimal | None
    poc_price: Decimal | None
    vah_price: Decimal | None
    val_price: Decimal | None
    status: AutoWatchStatus = AutoWatchStatus.ON_RADAR
    radar_presence: RadarPresence = RadarPresence.ON_RADAR
    radar_status_label: str = "FRESH COIL"
    eviction_reason: str | None = None
    soft_gate_summary: str | None = None
    checklist: tuple[dict[str, Any], ...] = ()
    last_price: Decimal = Decimal("0")
    last_observed_price: Decimal | None = None
    last_checked_at: datetime | None = None
    current_return_pct: Decimal | None = None
    peak_mfe_pct: Decimal | None = None
    peak_mfe_at: datetime | None = None
    worst_mae_pct: Decimal | None = None
    worst_mae_at: datetime | None = None
    retest_touched: bool = False
    breakout_touched: bool = False
    was_promoted_to_watchlist: bool = False
    graduation_count: int = 0
    has_observations: bool = False
    coverage_complete: bool = False
    coverage_gaps: int = 0
    barrier_order_certified: bool = False
    endpoint_observed_at: datetime | None = None
    measurement_status: str = "pending"
    resolved_at: datetime | None = None
    resolved_price: Decimal | None = None
    resolved_return_pct: Decimal | None = None
    resolution_reason: str | None = None
    source_scan_id: str | None = None
    policy_version: str | None = None

    @property
    def is_live_tracked(self) -> bool:
        """True while the episode is either live ON_RADAR or in the 48h WATCHLIST."""
        return self.status in (AutoWatchStatus.ON_RADAR, AutoWatchStatus.WATCHLIST)

    @property
    def is_active(self) -> bool:
        """Compatibility alias for live tracked (not yet resolved) episodes."""
        return self.is_live_tracked

    @property
    def is_watchlist(self) -> bool:
        """True ONLY when soft-evicted from 5/5 Radar and actively monitored in 48h Watchlist."""
        return self.status is AutoWatchStatus.WATCHLIST

    @property
    def is_resolved(self) -> bool:
        return not self.is_live_tracked

    @property
    def is_measured_outcome(self) -> bool:
        """True only when a resolved episode has a certified, verified measurement."""
        if not self.is_resolved or self.measurement_status == "legacy_unverified":
            return False
        if self.resolved_return_pct is None:
            return False
        if self.status in (AutoWatchStatus.TARGET_REACHED, AutoWatchStatus.STOP_BREACHED):
            return self.barrier_order_certified
        if self.status is AutoWatchStatus.EXPIRED_48H:
            return self.endpoint_observed_at is not None
        return self.status is AutoWatchStatus.HARD_INVALIDATED

    def measurement_dict(self) -> dict[str, Any]:
        return {
            "version": MEASUREMENT_SCHEMA_VERSION,
            "has_observations": self.has_observations,
            "coverage_complete": self.coverage_complete,
            "coverage_gaps": self.coverage_gaps,
            "barrier_order_certified": self.barrier_order_certified,
            "endpoint_observed_at": (
                self.endpoint_observed_at.isoformat() if self.endpoint_observed_at else None
            ),
            "last_observed_price": (
                str(self.last_observed_price) if self.last_observed_price is not None else None
            ),
            "measurement_status": self.measurement_status,
        }

    def as_dict(self, *, now: datetime | None = None) -> dict[str, Any]:
        ref_now = now or datetime.now(UTC)
        remaining_sec = max(0, int((self.expires_at - ref_now).total_seconds()))
        elapsed_sec = max(
            0,
            int(((self.resolved_at or ref_now) - self.published_at).total_seconds()),
        )

        is_legacy = self.measurement_status == "legacy_unverified"
        if is_legacy:
            wire_resolved_price: float | None = None
            wire_resolved_ret: float | None = None
            wire_cur_ret: float | None = None
            wire_mfe: float | None = None
            wire_mae: float | None = None
            wire_mfe_at: str | None = None
            wire_mae_at: str | None = None
            wire_last_obs: float | None = None
        else:
            wire_resolved_price = (
                float(self.resolved_price) if self.resolved_price is not None else None
            )
            wire_resolved_ret = (
                float(self.resolved_return_pct) if self.resolved_return_pct is not None else None
            )
            wire_cur_ret = (
                wire_resolved_ret
                if self.is_resolved
                else (float(self.current_return_pct) if self.current_return_pct is not None else None)
            )
            wire_mfe = float(self.peak_mfe_pct) if (self.has_observations and self.peak_mfe_pct is not None) else None
            wire_mae = float(self.worst_mae_pct) if (self.has_observations and self.worst_mae_pct is not None) else None
            wire_mfe_at = self.peak_mfe_at.isoformat() if (self.has_observations and self.peak_mfe_at) else None
            wire_mae_at = self.worst_mae_at.isoformat() if (self.has_observations and self.worst_mae_at) else None
            wire_last_obs = (
                float(self.last_observed_price) if self.last_observed_price is not None else None
            )

        effective_price_dec: Decimal | None
        if self.is_resolved:
            effective_price_dec = self.resolved_price if not is_legacy else None
        else:
            effective_price_dec = (
                self.last_observed_price
                if self.last_observed_price is not None and self.last_observed_price > 0
                else (self.last_price if self.last_price > 0 else None)
            )

        sign = Decimal("-1") if self.direction.lower() == "short" else Decimal("1")
        dist_t1_pct = (
            (sign * (self.target_1 - effective_price_dec) / effective_price_dec * Decimal("100")).quantize(
                Decimal("0.01")
            )
            if effective_price_dec is not None and effective_price_dec > 0 and self.target_1 > 0
            else None
        )
        dist_stop_pct = (
            (sign * (effective_price_dec - self.stop_price) / effective_price_dec * Decimal("100")).quantize(
                Decimal("0.01")
            )
            if effective_price_dec is not None and effective_price_dec > 0 and self.stop_price > 0
            else None
        )
        passed_gates = sum(1 for item in self.checklist if item.get("passed") is True)
        total_gates = len(self.checklist) if self.checklist else 5
        display_last_price = (
            float(effective_price_dec)
            if effective_price_dec is not None
            else (
                wire_last_obs
                if wire_last_obs is not None
                else float(self.signal_price)
            )
        )
        return {
            "episode_id": self.episode_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "setup_label": self.setup_label,
            "execution_mode": self.execution_mode,
            "score": float(self.score),
            "tick_size": str(self.tick_size),
            "published_at": self.published_at.isoformat(),
            "published_date": self.published_at.strftime("%Y-%m-%d"),
            "expires_at": self.expires_at.isoformat(),
            "remaining_seconds": remaining_sec if self.is_live_tracked else 0,
            "elapsed_seconds": elapsed_sec,
            "signal_price": float(self.signal_price),
            "entry_price": float(self.entry_price),
            "trigger_price": float(self.trigger_price) if self.trigger_price is not None else None,
            "stop_price": float(self.stop_price),
            "target_1": float(self.target_1),
            "target_2": float(self.target_2) if self.target_2 is not None else None,
            "target_3": float(self.target_3) if self.target_3 is not None else None,
            "poc_price": float(self.poc_price) if self.poc_price is not None else None,
            "vah_price": float(self.vah_price) if self.vah_price is not None else None,
            "val_price": float(self.val_price) if self.val_price is not None else None,
            "status": self.status.value,
            "radar_presence": self.radar_presence.value,
            "radar_status_label": self.radar_status_label,
            "eviction_reason": self.eviction_reason,
            "soft_gate_summary": self.soft_gate_summary,
            "checklist": list(self.checklist),
            "gates_passed": passed_gates,
            "gates_total": total_gates,
            "last_price": display_last_price,
            "last_observed_price": wire_last_obs,
            "last_checked_at": self.last_checked_at.isoformat() if self.last_checked_at else None,
            "current_return_pct": wire_cur_ret,
            "peak_mfe_pct": wire_mfe,
            "peak_mfe_at": wire_mfe_at,
            "worst_mae_pct": wire_mae,
            "worst_mae_at": wire_mae_at,
            "retest_touched": self.retest_touched,
            "breakout_touched": self.breakout_touched,
            "was_promoted_to_watchlist": self.was_promoted_to_watchlist,
            "graduation_count": self.graduation_count,
            "has_observations": self.has_observations,
            "coverage_complete": self.coverage_complete,
            "coverage_gaps": self.coverage_gaps,
            "barrier_order_certified": self.barrier_order_certified,
            "endpoint_observed_at": (
                self.endpoint_observed_at.isoformat() if self.endpoint_observed_at else None
            ),
            "measurement_status": self.measurement_status,
            "dist_to_t1_pct": float(dist_t1_pct) if dist_t1_pct is not None else None,
            "dist_to_stop_pct": float(dist_stop_pct) if dist_stop_pct is not None else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolved_price": wire_resolved_price,
            "resolved_return_pct": wire_resolved_ret,
            "resolution_reason": self.resolution_reason,
            "source_scan_id": self.source_scan_id,
            "policy_version": self.policy_version,
        }


def build_auto_watch_episode(
    candidate: Mapping[str, Any],
    *,
    published_at: datetime | None = None,
    scan_id: str | None = None,
    window_hours: int = AUTO_WATCH_WINDOW_HOURS,
) -> AutoWatchEpisode | None:
    """Create an AutoWatchEpisode in ON_RADAR state from a verified 5/5 Radar candidate."""
    symbol = str(candidate.get("symbol", "")).upper().strip()
    if not symbol:
        return None
    direction = str(candidate.get("direction", "long")).lower().strip()
    if direction not in ("long", "short"):
        return None

    checklist = _clean_checklist(candidate.get("checklist"))
    if len(checklist) != 5 or any(
        not item["passed"] or item["status"] != "passed" for item in checklist
    ):
        return None

    radar_signal = candidate.get("radar_signal")
    sig_map = radar_signal if isinstance(radar_signal, Mapping) else {}
    pub_dt = (
        _parse_iso(sig_map.get("published_at"))
        or published_at
        or _parse_iso(candidate.get("first_seen_at"))
        or _parse_iso(candidate.get("evaluated_at"))
    )
    if pub_dt is None:
        return None
    pub_dt = pub_dt.astimezone(UTC)

    signal_price = (
        _to_dec(sig_map.get("price"))
        or _to_dec(candidate.get("radar_trigger_entry"))
        or _to_dec(candidate.get("current_price"))
    )
    entry_price = _to_dec(candidate.get("suggested_entry")) or _to_dec(candidate.get("pullback_entry"))
    trigger_price = _to_dec(candidate.get("breakout_trigger"))
    stop_price = _to_dec(candidate.get("radar_trigger_stop")) or _to_dec(candidate.get("suggested_stop"))
    raw_targets = candidate.get("suggested_targets") or ()
    targets = [t for t in (_to_dec(x) for x in raw_targets) if t is not None and t > 0]

    if (
        signal_price is None
        or signal_price <= 0
        or entry_price is None
        or entry_price <= 0
        or stop_price is None
        or stop_price <= 0
        or not targets
    ):
        return None

    t1 = targets[0]
    t2 = targets[1] if len(targets) > 1 else None
    t3 = targets[2] if len(targets) > 2 else None

    if direction == "long" and not (stop_price < t1):
        return None
    if direction == "short" and not (t1 < stop_price):
        return None

    setup_label = str(candidate.get("setup_label", "UNKNOWN")).strip() or "UNKNOWN"
    execution_mode = str(candidate.get("execution_mode", "RETEST_ACCUMULATION")).upper().strip()
    score = _to_dec(candidate.get("score")) or Decimal("0")
    tick_size = _to_dec(candidate.get("tick_size")) or Decimal("0.0001")

    episode_id = str(sig_map.get("episode_id") or "").strip()
    if not episode_id:
        origin_iso = (
            (_parse_iso(candidate.get("first_seen_at")) or _parse_iso(candidate.get("evaluated_at")) or pub_dt)
            .astimezone(UTC)
            .isoformat()
        )
        raw_key = f"{symbol}|{direction}|{setup_label}|{origin_iso}"
        episode_id = hashlib.sha256(raw_key.encode()).hexdigest()[:32]

    return AutoWatchEpisode(
        episode_id=episode_id,
        symbol=symbol,
        direction=direction,
        setup_label=setup_label,
        execution_mode=execution_mode,
        score=score,
        tick_size=tick_size,
        published_at=pub_dt,
        expires_at=pub_dt + timedelta(hours=window_hours),
        signal_price=signal_price,
        entry_price=entry_price,
        trigger_price=trigger_price,
        stop_price=stop_price,
        target_1=t1,
        target_2=t2,
        target_3=t3,
        poc_price=_to_dec(candidate.get("poc_price")),
        vah_price=_to_dec(candidate.get("vah_price")),
        val_price=_to_dec(candidate.get("val_price")),
        status=AutoWatchStatus.ON_RADAR,
        radar_presence=RadarPresence.ON_RADAR,
        radar_status_label=str(candidate.get("radar_status_label") or "FRESH COIL"),
        checklist=checklist,
        last_price=signal_price,
        last_observed_price=None,
        last_checked_at=pub_dt,
        current_return_pct=None,
        peak_mfe_pct=None,
        peak_mfe_at=None,
        worst_mae_pct=None,
        worst_mae_at=None,
        has_observations=False,
        coverage_complete=False,
        coverage_gaps=0,
        barrier_order_certified=False,
        endpoint_observed_at=None,
        measurement_status="pending",
        source_scan_id=scan_id or str(sig_map.get("scan_id") or "") or None,
        policy_version=(
            str(candidate["screening_policy_version"]).strip()
            if candidate.get("screening_policy_version")
            else None
        ),
    )


def classify_radar_eviction(
    episode: AutoWatchEpisode,
    *,
    event: Mapping[str, Any] | None,
    now: datetime,
) -> tuple[AutoWatchStatus, str, str | None, tuple[dict[str, Any], ...]]:
    """Decide whether a candidate evicted from 5/5 Radar qualifies for the 48h Potential Watchlist.

    Promotes to WATCHLIST ONLY when:
      1. Episode is still inside its 48h window.
      2. Eviction was NOT a hard termination (stop hit, target hit, expiry, value-area loss, unknown data).
      3. All 4 structural gates (Trend, Anti-Chasing, Value Acceptance, Volatility Compression)
         are positively verified (`passed == True`).
      4. Only Smart Money Absorption failed AND the flow dip is soft (not acute liquidation or
         severe taker dump: Long taker >= 0.78, 1h OI > -5%, 7d OI >= -8%, funding < 15 bps;
         Short taker <= 1.32, funding > -5 bps).
      5. Price remains strictly intact between stop_price and target_1.
    """
    ref_now = now.astimezone(UTC)
    if ref_now >= episode.expires_at:
        return AutoWatchStatus.EXPIRED_48H, "window_48h_elapsed", None, episode.checklist

    if not event:
        return AutoWatchStatus.HARD_INVALIDATED, "eviction_telemetry_missing", None, episode.checklist

    state = str(event.get("state") or "").upper().strip()
    reason = str(event.get("reason") or state or "evicted_from_radar").strip()
    raw_cand = event.get("candidate")
    cand: Mapping[str, Any] = raw_cand if isinstance(raw_cand, Mapping) else {}
    checklist = _clean_checklist(cand.get("checklist")) or episode.checklist

    if state == "COMPLETED_TARGET" or "target_reached" in reason:
        return AutoWatchStatus.TARGET_REACHED, reason, None, checklist
    if "stop_loss_breached" in reason or state == "STOP_HIT":
        return AutoWatchStatus.STOP_BREACHED, reason, None, checklist
    if state in HARD_TERMINATION_STATES or reason in HARD_INVALIDATION_REASONS:
        return AutoWatchStatus.HARD_INVALIDATED, reason, None, checklist

    cur_px = _to_dec(cand.get("current_price")) or episode.last_observed_price or episode.last_price
    is_short = episode.direction.lower() == "short"
    if cur_px is not None and cur_px > 0:
        if not is_short:
            if cur_px <= episode.stop_price:
                return AutoWatchStatus.STOP_BREACHED, "stop_breached_at_eviction", None, checklist
            if cur_px >= episode.target_1:
                return AutoWatchStatus.TARGET_REACHED, "target_1_reached_at_eviction", None, checklist
        else:
            if cur_px >= episode.stop_price:
                return AutoWatchStatus.STOP_BREACHED, "stop_breached_at_eviction", None, checklist
            if cur_px <= episode.target_1:
                return AutoWatchStatus.TARGET_REACHED, "target_1_reached_at_eviction", None, checklist

    if len(checklist) != 5:
        return AutoWatchStatus.HARD_INVALIDATED, "incomplete_gate_checklist", None, checklist

    failed_items = [item for item in checklist if not item["passed"] or item["status"] != "passed"]
    if not failed_items:
        return AutoWatchStatus.WATCHLIST, reason, "Structure 5/5 intact", checklist

    failed_titles = {item["title"] for item in failed_items}
    unknown_items = [item for item in checklist if item["status"] == "unknown"]
    if unknown_items:
        return AutoWatchStatus.HARD_INVALIDATED, "unknown_telemetry_at_eviction", None, checklist

    is_recovery_family = episode.setup_label == "RECOVERY_RECLAIM_LONG"
    if not is_soft_only_checklist_failure(failed_titles, setup_label=episode.setup_label):
        fail_str = ",".join(sorted(failed_titles))
        return AutoWatchStatus.HARD_INVALIDATED, f"structural_gate_failed:{fail_str}", None, checklist

    raw_metrics = cand.get("radar_metrics")
    metrics: Mapping[str, Any] = raw_metrics if isinstance(raw_metrics, Mapping) else {}
    taker = _to_dec(metrics.get("taker_buy_sell_ratio"))
    oi_1h = _to_dec(metrics.get("oi_change_1h_pct"))
    oi_4h = _to_dec(metrics.get("oi_change_4h_pct"))
    oi_7d = _to_dec(metrics.get("oi_change_7d_pct"))
    funding_bps = _to_dec(metrics.get("funding_rate_bps")) or _to_dec(metrics.get("funding_bps"))
    vcr = _to_dec(metrics.get("volume_contraction_ratio"))

    if not is_short:
        if oi_1h is not None and oi_1h <= Decimal("-5.0"):
            return AutoWatchStatus.HARD_INVALIDATED, "acute_1h_oi_liquidation", None, checklist
        if is_recovery_family:
            if oi_4h is not None and oi_4h <= Decimal("-4.0"):
                return AutoWatchStatus.HARD_INVALIDATED, "oi_4h_collapse", None, checklist
        elif oi_7d is not None and oi_7d < Decimal("-8.0"):
            return AutoWatchStatus.HARD_INVALIDATED, "oi_7d_collapse", None, checklist
        if funding_bps is not None and funding_bps >= Decimal("15.0"):
            return AutoWatchStatus.HARD_INVALIDATED, "funding_crowded", None, checklist
        if taker is not None and taker < Decimal("0.78"):
            return AutoWatchStatus.HARD_INVALIDATED, f"severe_seller_dump_taker_{float(taker):.2f}", None, checklist
        is_constructive_prior_volume = (
            cur_px is not None
            and cur_px >= episode.signal_price * Decimal("0.985")
            and (taker is None or taker >= Decimal("0.90"))
            and vcr is not None
            and vcr <= Decimal("2.60")
        )
        if vcr is not None and vcr > Decimal("1.75") and not is_constructive_prior_volume:
            return AutoWatchStatus.HARD_INVALIDATED, f"volume_blowoff_vcr_{float(vcr):.2f}", None, checklist
    else:
        if taker is not None and taker > Decimal("1.32"):
            return AutoWatchStatus.HARD_INVALIDATED, f"severe_short_squeeze_taker_{float(taker):.2f}", None, checklist
        if funding_bps is not None and funding_bps <= Decimal("-5.0"):
            return AutoWatchStatus.HARD_INVALIDATED, "short_funding_squeeze_risk", None, checklist

    parts: list[str] = []
    if taker is not None:
        parts.append(f"Taker {float(taker):.2f}")
    if oi_7d is not None:
        parts.append(f"7d OI {float(oi_7d):+.1f}%")
    if funding_bps is not None:
        parts.append(f"Fund {float(funding_bps):+.1f}bps")
    summary = " · ".join(parts) if parts else failed_items[0]["detail"][:80]
    return AutoWatchStatus.WATCHLIST, reason, f"Soft flow dip ({summary})", checklist


def _is_valid_live_quote(
    *,
    live_price: Decimal | None,
    live_observed_at: datetime | None,
    episode: AutoWatchEpisode,
    ref_now: datetime,
) -> bool:
    if live_price is None or not live_price.is_finite() or live_price <= 0:
        return False
    if live_observed_at is None or live_observed_at.tzinfo is None:
        return False
    obs = live_observed_at.astimezone(UTC)
    if obs < episode.published_at - timedelta(seconds=5):
        return False
    if obs > episode.expires_at + timedelta(seconds=5):
        return False
    age = (ref_now - obs).total_seconds()
    return -5.0 <= age <= 300.0


def _resample_1h_to_4h_closes(closed_1h: Sequence[Candle]) -> list[Decimal]:
    """Aggregate contiguous complete 4-hour UTC buckets from closed 1H candles into 4H closes."""
    buckets: dict[datetime, list[Candle]] = {}
    for c in closed_1h:
        ts = c.opened_at.astimezone(UTC)
        key = ts.replace(hour=(ts.hour // 4) * 4, minute=0, second=0, microsecond=0)
        buckets.setdefault(key, []).append(c)
    closes_4h: list[Decimal] = []
    for bucket_start in sorted(buckets):
        grp = sorted(buckets[bucket_start], key=lambda x: x.opened_at)
        if len(grp) == 4:
            closes_4h.append(grp[-1].close)
    return closes_4h


def _check_watchlist_structural_invalidation(
    episode: AutoWatchEpisode,
    *,
    all_closed_1h: Sequence[Candle],
    post_bars: Sequence[Candle],
    effective_price: Decimal,
    breakout_touched: bool,
) -> tuple[str | None, tuple[dict[str, Any], ...]]:
    """ALFA v3.6 Watchlist live structural guard: evict broken 4H EMA50 bases and failed breakouts."""
    if (
        episode.status is not AutoWatchStatus.WATCHLIST
        or episode.direction.lower() == "short"
        or effective_price <= Decimal("0")
    ):
        return None, episode.checklist

    if episode.setup_label != "RECOVERY_RECLAIM_LONG" and len(all_closed_1h) >= 200:
        closes_4h = _resample_1h_to_4h_closes(all_closed_1h)
        if len(closes_4h) >= 50:
            ema50_4h = ema(closes_4h, 50)[-1]
            if (
                ema50_4h > Decimal("0")
                and closes_4h[-1] < ema50_4h * Decimal("0.985")
                and effective_price < ema50_4h * Decimal("0.985")
            ):
                updated_chk = tuple(
                    (
                        {
                            **item,
                            "passed": False,
                            "status": "failed",
                            "detail": f"Lost 4H EMA50 (${float(ema50_4h):.4f}) — Watchlist invalidated",
                        }
                        if item.get("title") == "Trend Baseline"
                        else item
                    )
                    for item in episode.checklist
                )
                return "watchlist_trend_baseline_lost", updated_chk

    if (
        episode.graduation_count == 0
        and episode.trigger_price is not None
        and episode.trigger_price > Decimal("0")
        and post_bars
    ):
        peak_high = max(b.high for b in post_bars)
        tested_breakout = breakout_touched or (peak_high >= episode.trigger_price * Decimal("0.992"))
        if (
            tested_breakout
            and effective_price <= peak_high * Decimal("0.955")
            and effective_price <= episode.signal_price * Decimal("0.970")
        ):
            return "failed_breakout_rejection", episode.checklist

    return None, episode.checklist


def evaluate_auto_watch_episode(
    episode: AutoWatchEpisode,
    *,
    candles: Sequence[Candle],
    live_price: Decimal | None,
    now: datetime,
    live_observed_at: datetime | None = None,
) -> AutoWatchEpisode:
    """Advance an ON_RADAR or WATCHLIST episode against post-publication candles and live price."""
    if episode.is_resolved:
        return episode

    ref_now = now.astimezone(UTC)
    is_short = episode.direction.lower() == "short"
    is_breakout_only = episode.execution_mode in BREAKOUT_ONLY_MODES

    has_obs = episode.has_observations
    peak_mfe = episode.peak_mfe_pct
    peak_mfe_at = episode.peak_mfe_at
    worst_mae = episode.worst_mae_pct
    worst_mae_at = episode.worst_mae_at
    retest_touched = episode.retest_touched
    breakout_touched = episode.breakout_touched
    last_obs_price = episode.last_observed_price

    # Expected first 1h bar open covering publication
    all_closed_1h = sorted(
        (c for c in candles if c.closed and c.high > 0 and c.low > 0 and c.close > 0),
        key=lambda c: c.opened_at,
    )
    pub_hour_floor = episode.published_at.replace(minute=0, second=0, microsecond=0)
    pub_floor = episode.published_at - timedelta(seconds=59)
    post_bars = sorted(
        (
            c
            for c in candles
            if c.opened_at.astimezone(UTC) >= pub_floor
            and c.opened_at.astimezone(UTC) < episode.expires_at
            and c.closed
            and c.high > 0
            and c.low > 0
            and c.close > 0
        ),
        key=lambda c: c.opened_at,
    )

    gaps = 0
    expected_next_open = pub_hour_floor
    if post_bars:
        first_open = post_bars[0].opened_at.astimezone(UTC)
        if first_open > episode.published_at + timedelta(minutes=60):
            # Missing leading hour(s) between publication and first observed bar
            missing_hours = int((first_open - pub_hour_floor).total_seconds() // 3600)
            gaps += max(1, missing_hours)
        elif first_open > pub_hour_floor and (first_open - episode.published_at).total_seconds() > 60:
            # Published at e.g. 08:00:00, first bar starts at 09:00:00 -> 08:00 bar is missing!
            gaps += max(1, int((first_open - pub_hour_floor).total_seconds() // 3600))
        expected_next_open = first_open

    last_bar_close: Decimal | None = None
    last_bar_closed_at: datetime | None = None

    for bar in post_bars:
        bar_open = bar.opened_at.astimezone(UTC)
        bar_close_at = min(bar.closed_at.astimezone(UTC), episode.expires_at)
        if bar_open > expected_next_open + timedelta(seconds=120):
            missing = max(1, int((bar_open - expected_next_open).total_seconds() // 3600))
            gaps += missing
        expected_next_open = bar_open + timedelta(hours=1)

        has_obs = True
        last_bar_close = bar.close
        last_bar_closed_at = bar_close_at
        last_obs_price = bar.close

        fav_price = bar.low if is_short else bar.high
        adv_price = bar.high if is_short else bar.low
        bar_mfe = directional_return_pct(
            direction=episode.direction,
            reference_price=episode.signal_price,
            price=fav_price,
        )
        bar_mae = directional_return_pct(
            direction=episode.direction,
            reference_price=episode.signal_price,
            price=adv_price,
        )
        if peak_mfe is None or bar_mfe > peak_mfe:
            peak_mfe = bar_mfe
            peak_mfe_at = bar_close_at
        if worst_mae is None or bar_mae < worst_mae:
            worst_mae = bar_mae
            worst_mae_at = bar_close_at

        if not is_short:
            if not is_breakout_only and bar.low <= episode.entry_price:
                retest_touched = True
            if episode.trigger_price is not None and bar.high >= episode.trigger_price:
                breakout_touched = True
            stop_hit = bar.low <= episode.stop_price
            target_hit = bar.high >= episode.target_1
        else:
            if not is_breakout_only and bar.high >= episode.entry_price:
                retest_touched = True
            if episode.trigger_price is not None and bar.low <= episode.trigger_price:
                breakout_touched = True
            stop_hit = bar.high >= episode.stop_price
            target_hit = bar.low <= episode.target_1

        if stop_hit:
            ret = directional_return_pct(
                direction=episode.direction,
                reference_price=episode.signal_price,
                price=episode.stop_price,
            )
            certified = gaps == 0
            final_mae = ret if worst_mae is None else min(worst_mae, ret)
            return replace(
                episode,
                status=AutoWatchStatus.STOP_BREACHED,
                last_price=episode.stop_price,
                last_observed_price=episode.stop_price,
                last_checked_at=ref_now,
                current_return_pct=ret,
                peak_mfe_pct=peak_mfe,
                peak_mfe_at=peak_mfe_at,
                worst_mae_pct=final_mae,
                worst_mae_at=bar_close_at if (worst_mae is None or ret < worst_mae) else worst_mae_at,
                retest_touched=retest_touched,
                breakout_touched=breakout_touched,
                has_observations=True,
                coverage_complete=certified,
                coverage_gaps=gaps,
                barrier_order_certified=certified,
                endpoint_observed_at=bar_close_at,
                measurement_status="measured" if certified else "uncertified_gap",
                resolved_at=bar_close_at,
                resolved_price=episode.stop_price,
                resolved_return_pct=ret,
                resolution_reason=(
                    "stop_and_target_same_bar_conservative_stop"
                    if target_hit
                    else "invalidation_stop_breached"
                ),
            )

        if target_hit:
            ret = directional_return_pct(
                direction=episode.direction,
                reference_price=episode.signal_price,
                price=episode.target_1,
            )
            certified = gaps == 0
            final_mfe = ret if peak_mfe is None else max(peak_mfe, ret)
            return replace(
                episode,
                status=AutoWatchStatus.TARGET_REACHED,
                last_price=episode.target_1,
                last_observed_price=episode.target_1,
                last_checked_at=ref_now,
                current_return_pct=ret,
                peak_mfe_pct=final_mfe,
                peak_mfe_at=bar_close_at if (peak_mfe is None or ret > peak_mfe) else peak_mfe_at,
                worst_mae_pct=worst_mae,
                worst_mae_at=worst_mae_at,
                retest_touched=retest_touched,
                breakout_touched=breakout_touched,
                has_observations=True,
                coverage_complete=certified,
                coverage_gaps=gaps,
                barrier_order_certified=certified,
                endpoint_observed_at=bar_close_at,
                measurement_status="measured" if certified else "uncertified_gap",
                resolved_at=bar_close_at,
                resolved_price=episode.target_1,
                resolved_return_pct=ret,
                resolution_reason="target_1_reached",
            )

    # Check 48-hour expiry boundary strictly — never fabricate an endpoint from a stale bar!
    if ref_now >= episode.expires_at:
        endpoint_px: Decimal | None = None
        endpoint_at: datetime | None = None
        # Only accept a bar that actually closes at/within 60s of the 48h boundary
        if (
            last_bar_close is not None
            and last_bar_closed_at is not None
            and abs((episode.expires_at - last_bar_closed_at).total_seconds()) <= 120
        ):
            endpoint_px = last_bar_close
            endpoint_at = episode.expires_at
        elif _is_valid_live_quote(
            live_price=live_price,
            live_observed_at=live_observed_at,
            episode=episode,
            ref_now=ref_now,
        ) and live_observed_at is not None and abs(
            (episode.expires_at - live_observed_at.astimezone(UTC)).total_seconds()
        ) <= 120:
            endpoint_px = live_price
            endpoint_at = live_observed_at.astimezone(UTC)

        # Count trailing missing bars up to expires_at
        if not post_bars:
            gaps = max(gaps, int((episode.expires_at - pub_hour_floor).total_seconds() // 3600))
        elif last_bar_closed_at is not None and last_bar_closed_at < episode.expires_at - timedelta(seconds=120):
            gaps += max(1, int((episode.expires_at - last_bar_closed_at).total_seconds() // 3600))

        full_coverage = bool(post_bars) and gaps == 0 and endpoint_px is not None
        if endpoint_px is not None:
            ret = directional_return_pct(
                direction=episode.direction,
                reference_price=episode.signal_price,
                price=endpoint_px,
            )
            has_obs = True
            last_obs_price = endpoint_px
            if peak_mfe is None or ret > peak_mfe:
                peak_mfe = ret
                peak_mfe_at = endpoint_at
            if worst_mae is None or ret < worst_mae:
                worst_mae = ret
                worst_mae_at = endpoint_at
            return replace(
                episode,
                status=AutoWatchStatus.EXPIRED_48H,
                last_price=endpoint_px,
                last_observed_price=endpoint_px,
                last_checked_at=ref_now,
                current_return_pct=ret,
                peak_mfe_pct=peak_mfe,
                peak_mfe_at=peak_mfe_at,
                worst_mae_pct=worst_mae,
                worst_mae_at=worst_mae_at,
                retest_touched=retest_touched,
                breakout_touched=breakout_touched,
                has_observations=True,
                coverage_complete=full_coverage,
                coverage_gaps=gaps,
                barrier_order_certified=full_coverage,
                endpoint_observed_at=endpoint_at,
                measurement_status="measured" if full_coverage else "partial_endpoint_only",
                resolved_at=episode.expires_at,
                resolved_price=endpoint_px,
                resolved_return_pct=ret,
                resolution_reason="window_48h_elapsed",
            )

        # Missing 48h endpoint observation -> preserve NULL return (never fabricate from stale T+1h candle!)
        return replace(
            episode,
            status=AutoWatchStatus.EXPIRED_48H,
            last_price=last_obs_price if last_obs_price is not None else episode.signal_price,
            last_observed_price=last_obs_price,
            last_checked_at=ref_now,
            current_return_pct=None,
            peak_mfe_pct=peak_mfe if has_obs else None,
            peak_mfe_at=peak_mfe_at if has_obs else None,
            worst_mae_pct=worst_mae if has_obs else None,
            worst_mae_at=worst_mae_at if has_obs else None,
            retest_touched=retest_touched,
            breakout_touched=breakout_touched,
            has_observations=has_obs,
            coverage_complete=False,
            coverage_gaps=gaps,
            barrier_order_certified=False,
            endpoint_observed_at=None,
            measurement_status="missing_endpoint",
            resolved_at=episode.expires_at,
            resolved_price=None,
            resolved_return_pct=None,
            resolution_reason="window_48h_elapsed_missing_endpoint",
        )

    # Still within 48h window: only apply live_price when live_observed_at is fresh & in-window
    live_valid = _is_valid_live_quote(
        live_price=live_price,
        live_observed_at=live_observed_at,
        episode=episode,
        ref_now=ref_now,
    )
    if live_valid and live_price is not None and live_observed_at is not None:
        obs_at = live_observed_at.astimezone(UTC)
        has_obs = True
        last_obs_price = live_price
        live_ret = directional_return_pct(
            direction=episode.direction,
            reference_price=episode.signal_price,
            price=live_price,
        )
        if peak_mfe is None or live_ret > peak_mfe:
            peak_mfe = live_ret
            peak_mfe_at = obs_at
        if worst_mae is None or live_ret < worst_mae:
            worst_mae = live_ret
            worst_mae_at = obs_at

        if not is_short:
            if not is_breakout_only and live_price <= episode.entry_price:
                retest_touched = True
            if episode.trigger_price is not None and live_price >= episode.trigger_price:
                breakout_touched = True
            live_stop_hit = live_price <= episode.stop_price
            live_target_hit = live_price >= episode.target_1
        else:
            if not is_breakout_only and live_price >= episode.entry_price:
                retest_touched = True
            if episode.trigger_price is not None and live_price <= episode.trigger_price:
                breakout_touched = True
            live_stop_hit = live_price >= episode.stop_price
            live_target_hit = live_price <= episode.target_1

        # Trailing gap check up to current open hour
        expected_closed_through = ref_now.replace(minute=0, second=0, microsecond=0)
        if last_bar_closed_at is not None and last_bar_closed_at < expected_closed_through - timedelta(seconds=120):
            gaps += max(1, int((expected_closed_through - last_bar_closed_at).total_seconds() // 3600))
        elif not post_bars and expected_closed_through > episode.published_at + timedelta(minutes=60):
            gaps += max(1, int((expected_closed_through - pub_hour_floor).total_seconds() // 3600))
        certified = gaps == 0

        if live_stop_hit:
            return replace(
                episode,
                status=AutoWatchStatus.STOP_BREACHED,
                last_price=live_price,
                last_observed_price=live_price,
                last_checked_at=ref_now,
                current_return_pct=live_ret,
                peak_mfe_pct=peak_mfe,
                peak_mfe_at=peak_mfe_at,
                worst_mae_pct=worst_mae,
                worst_mae_at=worst_mae_at,
                retest_touched=retest_touched,
                breakout_touched=breakout_touched,
                has_observations=True,
                coverage_complete=certified,
                coverage_gaps=gaps,
                barrier_order_certified=certified,
                endpoint_observed_at=obs_at,
                measurement_status="measured" if certified else "uncertified_gap",
                resolved_at=obs_at,
                resolved_price=live_price,
                resolved_return_pct=live_ret,
                resolution_reason="live_invalidation_stop_breached",
            )

        if live_target_hit:
            return replace(
                episode,
                status=AutoWatchStatus.TARGET_REACHED,
                last_price=live_price,
                last_observed_price=live_price,
                last_checked_at=ref_now,
                current_return_pct=live_ret,
                peak_mfe_pct=peak_mfe,
                peak_mfe_at=peak_mfe_at,
                worst_mae_pct=worst_mae,
                worst_mae_at=worst_mae_at,
                retest_touched=retest_touched,
                breakout_touched=breakout_touched,
                has_observations=True,
                coverage_complete=certified,
                coverage_gaps=gaps,
                barrier_order_certified=certified,
                endpoint_observed_at=obs_at,
                measurement_status="measured" if certified else "uncertified_gap",
                resolved_at=obs_at,
                resolved_price=live_price,
                resolved_return_pct=live_ret,
                resolution_reason="live_target_1_reached",
            )

        inv_reason, inv_chk = _check_watchlist_structural_invalidation(
            episode,
            all_closed_1h=all_closed_1h,
            post_bars=post_bars,
            effective_price=live_price,
            breakout_touched=breakout_touched,
        )
        if inv_reason is not None:
            return replace(
                episode,
                status=AutoWatchStatus.HARD_INVALIDATED,
                radar_status_label="HARD INVALIDATED",
                eviction_reason=inv_reason,
                checklist=inv_chk,
                last_price=live_price,
                last_observed_price=live_price,
                last_checked_at=ref_now,
                current_return_pct=live_ret,
                peak_mfe_pct=peak_mfe,
                peak_mfe_at=peak_mfe_at,
                worst_mae_pct=worst_mae,
                worst_mae_at=worst_mae_at,
                retest_touched=retest_touched,
                breakout_touched=breakout_touched,
                has_observations=True,
                coverage_complete=certified,
                coverage_gaps=gaps,
                endpoint_observed_at=obs_at,
                measurement_status="measured" if certified else "uncertified_gap",
                resolved_at=obs_at,
                resolved_price=live_price,
                resolved_return_pct=live_ret,
                resolution_reason=inv_reason,
            )

        return replace(
            episode,
            last_price=live_price,
            last_observed_price=live_price,
            last_checked_at=ref_now,
            current_return_pct=live_ret,
            peak_mfe_pct=peak_mfe,
            peak_mfe_at=peak_mfe_at,
            worst_mae_pct=worst_mae,
            worst_mae_at=worst_mae_at,
            retest_touched=retest_touched,
            breakout_touched=breakout_touched,
            has_observations=True,
            coverage_complete=certified,
            coverage_gaps=gaps,
            measurement_status="tracking",
        )

    # No valid live quote: update only from observed post-publication candles if present
    cur_ret = (
        directional_return_pct(
            direction=episode.direction,
            reference_price=episode.signal_price,
            price=last_obs_price,
        )
        if has_obs and last_obs_price is not None and last_obs_price > 0
        else None
    )
    if has_obs and last_obs_price is not None and last_obs_price > 0:
        inv_reason, inv_chk = _check_watchlist_structural_invalidation(
            episode,
            all_closed_1h=all_closed_1h,
            post_bars=post_bars,
            effective_price=last_obs_price,
            breakout_touched=breakout_touched,
        )
        if inv_reason is not None:
            return replace(
                episode,
                status=AutoWatchStatus.HARD_INVALIDATED,
                radar_status_label="HARD INVALIDATED",
                eviction_reason=inv_reason,
                checklist=inv_chk,
                last_price=last_obs_price,
                last_observed_price=last_obs_price,
                last_checked_at=ref_now,
                current_return_pct=cur_ret,
                peak_mfe_pct=peak_mfe,
                peak_mfe_at=peak_mfe_at,
                worst_mae_pct=worst_mae,
                worst_mae_at=worst_mae_at,
                retest_touched=retest_touched,
                breakout_touched=breakout_touched,
                has_observations=True,
                coverage_complete=bool(post_bars) and gaps == 0,
                coverage_gaps=gaps,
                endpoint_observed_at=last_bar_closed_at,
                measurement_status="measured" if (bool(post_bars) and gaps == 0) else "uncertified_gap",
                resolved_at=last_bar_closed_at or ref_now,
                resolved_price=last_obs_price,
                resolved_return_pct=cur_ret,
                resolution_reason=inv_reason,
            )
    return replace(
        episode,
        last_price=last_obs_price if last_obs_price is not None else episode.signal_price,
        last_observed_price=last_obs_price,
        last_checked_at=ref_now,
        current_return_pct=cur_ret,
        peak_mfe_pct=peak_mfe if has_obs else None,
        peak_mfe_at=peak_mfe_at if has_obs else None,
        worst_mae_pct=worst_mae if has_obs else None,
        worst_mae_at=worst_mae_at if has_obs else None,
        retest_touched=retest_touched,
        breakout_touched=breakout_touched,
        has_observations=has_obs,
        coverage_complete=bool(post_bars) and gaps == 0,
        coverage_gaps=gaps,
        measurement_status="tracking" if has_obs else "pending",
    )


def _stats_for_group(items: Sequence[AutoWatchEpisode]) -> dict[str, Any]:
    total = len(items)
    on_radar = [e for e in items if e.status is AutoWatchStatus.ON_RADAR]
    watchlist = [e for e in items if e.status is AutoWatchStatus.WATCHLIST]
    resolved = [e for e in items if e.is_resolved]
    measured_resolved = [e for e in resolved if e.is_measured_outcome]
    unknown_outcomes = len(resolved) - len(measured_resolved)
    legacy_unverified = sum(1 for e in items if e.measurement_status == "legacy_unverified")
    uncertified_barriers = sum(
        1
        for e in resolved
        if e.status in (AutoWatchStatus.TARGET_REACHED, AutoWatchStatus.STOP_BREACHED)
        and not e.barrier_order_certified
    )

    t1_hits = [e for e in measured_resolved if e.status is AutoWatchStatus.TARGET_REACHED]
    stops = [e for e in measured_resolved if e.status is AutoWatchStatus.STOP_BREACHED]
    expired = [e for e in resolved if e.status is AutoWatchStatus.EXPIRED_48H]
    hard_inv = [e for e in resolved if e.status is AutoWatchStatus.HARD_INVALIDATED]

    resolved_returns = [
        float(e.resolved_return_pct)
        for e in measured_resolved
        if e.resolved_return_pct is not None
    ]
    all_returns: list[float] = []
    for e in items:
        if e.measurement_status == "legacy_unverified":
            continue
        if e.is_resolved:
            if e.is_measured_outcome and e.resolved_return_pct is not None:
                all_returns.append(float(e.resolved_return_pct))
        elif e.has_observations and e.current_return_pct is not None:
            all_returns.append(float(e.current_return_pct))

    observed_extrema = [
        e
        for e in items
        if e.has_observations
        and e.measurement_status != "legacy_unverified"
        and e.peak_mfe_pct is not None
        and e.worst_mae_pct is not None
    ]
    mfes = [float(e.peak_mfe_pct) for e in observed_extrema if e.peak_mfe_pct is not None]
    maes = [float(e.worst_mae_pct) for e in observed_extrema if e.worst_mae_pct is not None]
    complete_cov = sum(1 for e in items if e.coverage_complete)
    decisive = len(t1_hits) + len(stops)

    return {
        "total": total,
        "on_radar": len(on_radar),
        "watchlist": len(watchlist),
        "active": len(watchlist),
        "resolved": len(resolved),
        "measured_resolved": len(measured_resolved),
        "unknown_outcome_count": unknown_outcomes,
        "observed_extrema_count": len(observed_extrema),
        "complete_coverage_count": complete_cov,
        "uncertified_barrier_count": uncertified_barriers,
        "legacy_unverified_count": legacy_unverified,
        "target_reached": len(t1_hits),
        "stop_breached": len(stops),
        "expired_48h": len(expired),
        "hard_invalidated": len(hard_inv),
        "target_hit_rate_pct": (
            round(len(t1_hits) / len(measured_resolved) * 100, 1) if measured_resolved else None
        ),
        "stop_rate_pct": (
            round(len(stops) / len(measured_resolved) * 100, 1) if measured_resolved else None
        ),
        "decisive_t1_vs_stop_pct": (
            round(len(t1_hits) / decisive * 100, 1) if decisive else None
        ),
        "resolved_positive_rate_pct": (
            round(sum(1 for r in resolved_returns if r > 0) / len(resolved_returns) * 100, 1)
            if resolved_returns
            else None
        ),
        "all_positive_rate_pct": (
            round(sum(1 for r in all_returns if r > 0) / len(all_returns) * 100, 1)
            if all_returns
            else None
        ),
        "avg_return_pct": round(statistics.mean(all_returns), 2) if all_returns else None,
        "median_return_pct": round(statistics.median(all_returns), 2) if all_returns else None,
        "avg_resolved_return_pct": (
            round(statistics.mean(resolved_returns), 2) if resolved_returns else None
        ),
        "avg_peak_mfe_pct": round(statistics.mean(mfes), 2) if mfes else None,
        "median_peak_mfe_pct": round(statistics.median(mfes), 2) if mfes else None,
        "avg_worst_mae_pct": round(statistics.mean(maes), 2) if maes else None,
        "retest_touch_rate_pct": (
            round(sum(1 for e in items if e.retest_touched) / total * 100, 1) if total else None
        ),
        "breakout_touch_rate_pct": (
            round(sum(1 for e in items if e.breakout_touched) / total * 100, 1) if total else None
        ),
    }


def compute_performance_summary(episodes: Sequence[AutoWatchEpisode]) -> dict[str, Any]:
    """Compute overall, daily, archetype, and Radar-vs-Promoted-Watchlist performance metrics."""
    overall = _stats_for_group(episodes)
    stayed_on_radar = [e for e in episodes if not e.was_promoted_to_watchlist]
    promoted_watchlist = [e for e in episodes if e.was_promoted_to_watchlist]

    by_day: dict[str, list[AutoWatchEpisode]] = defaultdict(list)
    by_arch: dict[str, list[AutoWatchEpisode]] = defaultdict(list)
    for ep in episodes:
        by_day[ep.published_at.strftime("%Y-%m-%d")].append(ep)
        by_arch[ep.setup_label].append(ep)

    daily_breakdown = [
        {"date": day, **_stats_for_group(group)}
        for day, group in sorted(by_day.items(), reverse=True)
    ]
    archetype_breakdown = [
        {"setup_label": label, **_stats_for_group(group)}
        for label, group in sorted(by_arch.items(), key=lambda kv: -len(kv[1]))
    ]

    return {
        "overall": overall,
        "on_radar_cohort": _stats_for_group(stayed_on_radar),
        "evicted_from_radar_cohort": _stats_for_group(promoted_watchlist),
        "daily_breakdown": daily_breakdown,
        "archetype_breakdown": archetype_breakdown,
        "semantics": (
            "Disjoint lifecycle: live 5/5 Radar setups stay in Radar (never duplicated in Watchlist). "
            "Soft-evicted setups with 4/4 structural gates intact enter the 48h Watchlist until "
            "TARGET_REACHED, STOP_BREACHED, EXPIRED_48H, or 5/5 Radar re-entry. "
            "Measured statistics require certified contiguous post-publication coverage; barrier touches are market observations, not executed fills."
        ),
    }
