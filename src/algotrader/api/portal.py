from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

if TYPE_CHECKING:
    from algotrader.application.macro_service import MacroIntelligenceService

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field

from algotrader.application.auto_watchlist_service import AutoWatchlistService
from algotrader.application.bearish_continuation import publication_inputs_current
from algotrader.application.early_recovery import recovery_publication_inputs_current
from algotrader.application.kinetic_scout_service import KineticScoutService
from algotrader.application.radar_service import reconcile_radar_cohort
from algotrader.application.top_gainer_short_watch import TopGainerShortWatchService
from algotrader.application.watchlist_service import WatchlistService
from algotrader.config import Settings, load_settings
from algotrader.domain.crypto_screener_models import (
    ScreenerMode,
    calculate_opportunity_win_rate_and_ev,
)
from algotrader.domain.errors import ConfigurationError
from algotrader.domain.models import Quote
from algotrader.domain.radar_insights import record_radar_publication
from algotrader.domain.radar_lifecycle import (
    RadarLifecycleState,
    same_radar_episode,
)
from algotrader.domain.screener_policy import (
    SCREENING_POLICY_VERSION as SCREENING_POLICY_VERSION,
)
from algotrader.domain.screener_policy import (
    screening_policy_version_for,
)
from algotrader.domain.watchlist_models import WatchlistItem, WatchlistPriority, WyckoffStage
from algotrader.infrastructure.persistence import atomic_write_json
from algotrader.infrastructure.runtime import Runtime, build_runtime
from algotrader.infrastructure.scan_archive import LocalScanArchive
from algotrader.infrastructure.top_gainer_short_watch import TopGainerShortWatchStore

portal_router = APIRouter(prefix="/api")

# Global singleton cache for screener results & runtime
_runtime_lock = asyncio.Lock()
_shared_runtime: Runtime | None = None
RADAR_PUBLICATION_VERSION = "radar-v5"
_screener_cache: dict[str, Any] = {}
_cache_timestamp: datetime | None = None
_episode_roster: list[dict[str, Any]] = []
_episode_policy_version: str | None = None
_episode_macro_narrative: str | None = None
_cached_instruments: Any = None
_watchlist_service = WatchlistService()
_auto_watchlist_service: AutoWatchlistService | None = None
_auto_watchlist_backfilled: bool = False
_short_watch_lock = asyncio.Lock()
_short_watch_service: TopGainerShortWatchService | None = None
_kinetic_scout_task: asyncio.Task[None] | None = None
_kinetic_scout_service: KineticScoutService | None = None


def _live_radar_symbols_from_cache() -> list[str]:
    opps = _screener_cache.get("opportunities")
    if not isinstance(opps, list):
        return []
    return [
        str(o.get("symbol", "")).upper().strip()
        for o in opps
        if isinstance(o, dict) and o.get("symbol")
    ]


def _get_auto_watchlist_service(runtime: Runtime | None = None) -> AutoWatchlistService:
    global _auto_watchlist_service, _auto_watchlist_backfilled
    if _auto_watchlist_service is None:
        settings = runtime.settings if runtime is not None else load_settings()
        raw_db_path = getattr(settings, "state_db_path", None)
        db_path = raw_db_path if isinstance(raw_db_path, (str, Path)) else load_settings().state_db_path
        _auto_watchlist_service = AutoWatchlistService(db_path)
    if not _auto_watchlist_backfilled:
        _auto_watchlist_backfilled = True
        with contextlib.suppress(Exception):
            _auto_watchlist_service.backfill_from_radar_events_dir(
                Path("data/radar_events"),
                live_radar_symbols=_live_radar_symbols_from_cache(),
            )
    return _auto_watchlist_service


def _get_short_watch_service(runtime: Runtime | None = None) -> TopGainerShortWatchService:
    global _short_watch_service
    if _short_watch_service is None:
        settings = runtime.settings if runtime is not None else load_settings()
        macro_provider = runtime.macro_provider if runtime is not None else None
        store = TopGainerShortWatchStore(settings.state_db_path)
        _short_watch_service = TopGainerShortWatchService(
            store, settings, macro_provider=macro_provider
        )
    if runtime is not None:
        _short_watch_service.macro_provider = runtime.macro_provider
    return _short_watch_service


async def _run_short_watch_cycle() -> dict[str, Any]:
    async with _short_watch_lock:
        runtime = await get_portal_runtime()
        service = _get_short_watch_service(runtime)
        return await service.run_cycle(
            runtime.futures_market,
            derivatives_port=runtime.futures_market,
        )


async def get_portal_runtime(settings: Settings | None = None) -> Runtime:
    global _shared_runtime
    async with _runtime_lock:
        if _shared_runtime is None:
            _shared_runtime = await build_runtime(settings or load_settings())
        return _shared_runtime


class AddWatchlistRequest(BaseModel):
    symbol: str = Field(min_length=2, max_length=20, pattern=r"^[A-Za-z0-9_]+$")
    name: str = Field(default="", max_length=120)
    sector: str = Field(default="DeFi / Altcoins", max_length=120)
    stage: str = Field(default="Phase C: Spring / Shakeout Test", max_length=120)
    timeframe: str = Field(default="1–3 Days", max_length=60)
    current_price: float = Field(default=0.0, ge=0.0, le=1e12, allow_inf_nan=False)
    trigger_price: float = Field(default=0.0, ge=0.0, le=1e12, allow_inf_nan=False)
    support_price: float = Field(default=0.0, ge=0.0, le=1e12, allow_inf_nan=False)
    invalidation_price: float = Field(default=0.0, ge=0.0, le=1e12, allow_inf_nan=False)
    target_1: float = Field(default=0.0, ge=0.0, le=1e12, allow_inf_nan=False)
    target_2: float = Field(default=0.0, ge=0.0, le=1e12, allow_inf_nan=False)
    distance_to_trigger_pct: float = Field(default=0.0, ge=0.0, le=1e6, allow_inf_nan=False)
    score: float = Field(default=60.0, ge=0.0, le=100.0, allow_inf_nan=False)
    thesis: str = Field(default="", max_length=2000)
    checklist_passed: int = Field(default=5, ge=0, le=10)
    checklist_total: int = Field(default=5, ge=1, le=10)
    priority: str = Field(default="HIGH", max_length=32)
    notes: str = Field(default="", max_length=2000)


class UpdateNotesRequest(BaseModel):
    notes: str = Field(min_length=0, max_length=1000)


class BatchAuditWatchlistRequest(BaseModel):
    items: list[AddWatchlistRequest] = Field(default_factory=list, max_length=25)
    refresh: bool = False


_is_refreshing: bool = False


def _enrich_opp_with_win_rate(opp: dict[str, Any]) -> dict[str, Any]:
    if "estimated_win_rate_pct" not in opp or "expected_value_r" not in opp:
        try:
            score_val = Decimal(str(opp.get("score", 65.0)))
        except Exception:
            score_val = Decimal("65.0")
        checklist = opp.get("checklist", [])
        checklist_passed = (
            sum(1 for c in checklist if isinstance(c, dict) and c.get("passed")) if checklist else 4
        )
        checklist_total = len(checklist) if checklist else 5
        rr_raw = opp.get("risk_reward_ratio", 3.0)
        try:
            rr_val = Decimal(str(rr_raw))
        except Exception:
            rr_val = Decimal("3.0")
        taker_raw = opp.get("taker_buy_sell_ratio") or opp.get("taker_ratio")
        try:
            taker_val = Decimal(str(taker_raw)) if taker_raw and taker_raw != "N/A" else None
        except Exception:
            taker_val = None

        win_rate, ev_r = calculate_opportunity_win_rate_and_ev(
            setup_label=str(opp.get("setup_label", "COIL_SPRING_LONG")),
            score=score_val,
            checklist_passed=checklist_passed,
            checklist_total=checklist_total,
            risk_reward_ratio=rr_val,
            taker_ratio=taker_val,
        )
        opp["estimated_win_rate_pct"] = str(win_rate)
        opp["expected_value_r"] = str(ev_r)
    return opp


def _get_fresh_baseline_opportunities() -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "timestamp": now.isoformat(),
        "total_scanned": 0,
        "qualifying_count": 0,
        "macro_narrative": "Awaiting initial live algorithmic scan across Binance universe...",
        "opportunities": [],
    }


_PRIVATE_CACHE_KEYS = frozenset({"_prior_episodes", "_prior_policy_version"})
WITHHELD_SCREENER_STATUSES = frozenset({
    "scan_failed", "degraded_zero_usable_inputs", "startup_revalidation_required",
})


def _strip_private_cache_keys(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k not in _PRIVATE_CACHE_KEYS}


def _get_prior_episodes_for_reconciliation() -> tuple[list[dict[str, Any]], str | None]:
    """Return active episode roster across both healthy and failed/degraded cycles."""
    if not _screener_cache:
        return [], None
    status = _screener_cache.get("data_status")
    if status in WITHHELD_SCREENER_STATUSES:
        if _episode_roster:
            return list(_episode_roster), _episode_policy_version
        return [], None
    if isinstance(_screener_cache.get("opportunities"), list) and _screener_cache["opportunities"]:
        return (
            list(_screener_cache["opportunities"]),
            cast(str | None, _screener_cache.get("screening_policy_version")),
        )
    return [], None


def _snapshot_episode_roster_from_cache() -> None:
    """Preserve prior active episodes in private memory before replacing public cache on outage."""
    global _episode_roster, _episode_policy_version, _episode_macro_narrative
    status = _screener_cache.get("data_status")
    if (
        status not in WITHHELD_SCREENER_STATUSES
        and isinstance(_screener_cache.get("opportunities"), list)
        and _screener_cache["opportunities"]
    ):
        _episode_roster = list(_screener_cache["opportunities"])
        _episode_policy_version = cast(
            str | None, _screener_cache.get("screening_policy_version")
        )
        if _screener_cache.get("macro_narrative"):
            _episode_macro_narrative = str(_screener_cache["macro_narrative"])


def warm_screener_cache(path: Path | str = "data/screener_latest.json") -> dict[str, Any] | None:
    """Pre-populates the in-memory screener cache and private episode roster from disk on cold start.

    Disk episodes are historical evidence, never current boot-time qualifications. Even a
    healthy file may precede an outage whose failure envelope could not be written.
    """
    global _screener_cache, _cache_timestamp
    global _episode_roster, _episode_policy_version, _episode_macro_narrative
    global _consecutive_scan_failures, _last_scan_error
    p = Path(path)
    if p.exists():
        try:
            raw = p.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict) and (
                "opportunities" in data or "data_status" in data
            ):
                status = data.get("data_status")
                if status in WITHHELD_SCREENER_STATUSES:
                    prior_eps = data.get("_prior_episodes")
                    if isinstance(prior_eps, list):
                        _episode_roster = list(prior_eps)
                        _episode_policy_version = cast(
                            str | None,
                            data.get("_prior_policy_version")
                            or data.get("screening_policy_version"),
                        )
                    _consecutive_scan_failures = max(
                        1, int(data.get("consecutive_scan_failures", 1))
                    )
                    if data.get("last_scan_error"):
                        _last_scan_error = str(data["last_scan_error"])
                elif isinstance(data.get("opportunities"), list):
                    _episode_roster = list(data["opportunities"])
                    _episode_policy_version = cast(
                        str | None, data.get("screening_policy_version")
                    )
                    if data.get("macro_narrative"):
                        _episode_macro_narrative = str(data["macro_narrative"])

                _screener_cache = _strip_private_cache_keys(data)
                if status not in WITHHELD_SCREENER_STATUSES:
                    _screener_cache.update({
                        "data_status": "startup_revalidation_required",
                        "stale": True,
                        "stale_reason": "restart_requires_live_revalidation",
                        "macro_narrative": "Saved episodes await live re-verification after restart.",
                        "opportunities": [],
                        "qualifying_count": 0,
                    })
                _cache_timestamp = None
                ts_str = _screener_cache.get("timestamp")
                if isinstance(ts_str, str) and ts_str:
                    with contextlib.suppress(Exception):
                        _cache_timestamp = datetime.fromisoformat(ts_str)
                return _screener_cache
        except Exception:
            pass
    return None


# Screener background and on-demand execution state
_screener_task: asyncio.Task[dict[str, Any]] | None = None
_screener_task_lock = asyncio.Lock()
_scan_execution_lock = asyncio.Lock()
_last_scan_attempted_at: datetime | None = None
_last_scan_succeeded_at: datetime | None = None
_last_scan_error: str | None = None
_consecutive_scan_failures: int = 0
_last_manual_refresh_at: datetime | None = None
MANUAL_REFRESH_COOLDOWN_SECONDS: float = 60.0


def screener_cache_ttl_seconds(interval_hours: int) -> float:
    """Maximum allowed age for published radar qualifications before withholding as stale."""
    return float(max(1, interval_hours) * 2 * 3600 + 900)


def _resolve_cache_timestamp(cache: dict[str, Any]) -> datetime | None:
    ts_raw = cache.get("timestamp")
    if isinstance(ts_raw, str) and ts_raw:
        try:
            parsed = datetime.fromisoformat(ts_raw)
            return parsed if parsed.tzinfo is not None else None
        except ValueError:
            return None
    if _cache_timestamp is not None and _cache_timestamp.tzinfo is not None:
        return _cache_timestamp
    return None


def is_screener_cache_stale(
    settings: Settings | None = None,
    now: datetime | None = None,
) -> bool:
    """Returns True when the current cached publication is expired or lacks a valid timestamp."""
    if not _screener_cache:
        return False
    cache_ts = _resolve_cache_timestamp(_screener_cache)
    if cache_ts is None:
        return True
    active_settings = settings or (_shared_runtime.settings if _shared_runtime is not None else load_settings())
    ttl = screener_cache_ttl_seconds(active_settings.screener_interval_hours)
    ref_now = now or datetime.now(UTC)
    age = (ref_now - cache_ts).total_seconds()
    return age < -300.0 or age > ttl


def _project_public_screener_state(
    settings: Settings,
    now: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    """Single authoritative publication projection shared by /screener/live and /chart/{symbol}.

    Enforces screening_policy_version, radar_lifecycle_version, operational failure/degraded
    withholding, cache freshness TTL, and disabled-continuation rollback identically everywhere.
    """
    active_policy_ver = screening_policy_version_for(settings)
    if (
        not _screener_cache
        or _screener_cache.get("radar_lifecycle_version") != RADAR_PUBLICATION_VERSION
        or _screener_cache.get("screening_policy_version") != active_policy_ver
    ):
        return "needs_refresh", {}

    cache_status = _screener_cache.get("data_status")
    if cache_status in WITHHELD_SCREENER_STATUSES:
        failed_payload = _strip_private_cache_keys(_screener_cache)
        failed_payload["opportunities"] = []
        failed_payload["qualifying_count"] = 0
        failed_payload["is_scanning"] = False
        failed_payload["stale"] = True
        return str(cache_status), failed_payload

    if is_screener_cache_stale(settings, now=now):
        stale_ts = _screener_cache.get("timestamp")
        return "stale", {
            "timestamp": stale_ts or datetime.now(UTC).isoformat(),
            "total_scanned": 0,
            "qualifying_count": 0,
            "data_status": "stale",
            "stale": True,
            "stale_reason": "cache_ttl_exceeded",
            "screening_policy_version": active_policy_ver,
            "radar_lifecycle_version": RADAR_PUBLICATION_VERSION,
            "macro_narrative": "Cached qualifications expired past freshness TTL; awaiting fresh live scan.",
            "opportunities": [],
            "is_scanning": False,
        }

    result = _strip_private_cache_keys(_screener_cache)
    result.setdefault("data_status", "current")
    result.setdefault("stale", False)
    opps = list(result.get("opportunities", []))
    if any(o.get("setup_label") == "BREAKDOWN_RETEST_SHORT" for o in opps):
        if getattr(settings, "bearish_continuation_enabled", False) is not True:
            # A rollback also withholds previously cached experimental cards across all routes.
            opps = [o for o in opps if o.get("setup_label") != "BREAKDOWN_RETEST_SHORT"]
    if any(o.get("setup_label") == "RECOVERY_RECLAIM_LONG" for o in opps):
        if getattr(settings, "early_recovery_enabled", False) is not True:
            opps = [o for o in opps if o.get("setup_label") != "RECOVERY_RECLAIM_LONG"]
    result["opportunities"] = opps
    result["qualifying_count"] = len(opps)
    return "current", result


async def _persist_degraded_state(
    *,
    data_status: str,
    stale_reason: str,
    error_message: str,
    macro_narrative: str,
    scan_counts: dict[str, Any] | None = None,
    scan_id: str | None = None,
    scan_archive: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a failed or zero-usable-input scan while preserving private episode continuity."""
    global _screener_cache, _cache_timestamp, _last_scan_error, _consecutive_scan_failures
    _snapshot_episode_roster_from_cache()
    _last_scan_error = error_message
    _consecutive_scan_failures += 1
    failed_at = datetime.now(UTC)

    active_settings = _shared_runtime.settings if _shared_runtime is not None else load_settings()
    public_payload: dict[str, Any] = {
        "timestamp": failed_at.isoformat(),
        "total_scanned": 0,
        "qualifying_count": 0,
        "data_status": data_status,
        "stale": True,
        "stale_reason": stale_reason,
        "last_scan_error": error_message,
        "consecutive_scan_failures": _consecutive_scan_failures,
        "screening_policy_version": screening_policy_version_for(active_settings),
        "radar_lifecycle_version": RADAR_PUBLICATION_VERSION,
        "macro_narrative": macro_narrative,
        "opportunities": [],
    }
    if scan_counts is not None:
        public_payload["scan_counts"] = scan_counts
    if scan_id is not None:
        public_payload["scan_id"] = scan_id
    if scan_archive is not None:
        public_payload["scan_archive"] = scan_archive

    _screener_cache = public_payload
    _cache_timestamp = failed_at

    # Persist the operational failure envelope + private episode roster atomically so a restart
    # never resurrects pre-failure cards as current and retains episode continuity on recovery.
    disk_envelope = {
        **public_payload,
        "_prior_episodes": list(_episode_roster),
        "_prior_policy_version": _episode_policy_version,
    }
    try:
        cache_file = Path("data/screener_latest.json")
        await asyncio.to_thread(atomic_write_json, cache_file, disk_envelope)
        public_payload["cache_persisted"] = True
    except Exception:
        public_payload["cache_persisted"] = False

    return dict(public_payload)


def _require_operator_auth(authorization: str | None) -> None:
    """Enforces constant-time Bearer operator authentication for privileged control/write routes."""
    try:
        token = load_settings().require_operator_token()
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=503, detail="Operator authentication is not configured"
        ) from exc
    if authorization is None or not secrets.compare_digest(
        authorization.encode(), f"Bearer {token}".encode()
    ):
        raise HTTPException(status_code=401, detail="Operator authentication required")


async def _do_crypto_screen(
    mode: str = "all",
    min_score: float = 50.0,
    max_symbols: int = 320,
) -> dict[str, Any]:
    global _screener_cache, _cache_timestamp, _is_refreshing
    async with _scan_execution_lock:
        return await _do_crypto_screen_locked(
            mode=mode,
            min_score=min_score,
            max_symbols=max_symbols,
        )


async def _do_crypto_screen_locked(
    mode: str = "all",
    min_score: float = 50.0,
    max_symbols: int = 320,
) -> dict[str, Any]:
    global _screener_cache, _cache_timestamp, _is_refreshing, _cached_instruments
    global _episode_roster, _episode_policy_version, _episode_macro_narrative
    global _last_scan_attempted_at, _last_scan_succeeded_at, _last_scan_error, _consecutive_scan_failures
    _is_refreshing = True
    _last_scan_attempted_at = datetime.now(UTC)
    try:
        runtime = await get_portal_runtime()
        settings = runtime.settings
        screener_mode = ScreenerMode(mode.lower())

        futures = runtime.futures_market
        run = await runtime.run_crypto_scan(
            settings=settings,
            min_score=Decimal(str(min_score)),
            max_symbols=max_symbols,
            mode=screener_mode,
            top_n=None,
            feature_contract="discovery_v2",
        )
        opportunities = run.opportunities
        counts = run.counts or {}
        attempted_cnt = int(counts.get("attempted", 0))
        selected_cnt = int(counts.get("selected", 0))
        failed_cnt = int(counts.get("failed", 0))
        evaluated_cnt = int(counts.get("evaluated", 0))
        if (
            (attempted_cnt > 0 or selected_cnt > 0 or failed_cnt > 0)
            and evaluated_cnt == 0
            and not opportunities
        ):
            return await _persist_degraded_state(
                data_status="degraded_zero_usable_inputs",
                stale_reason="all_inputs_unavailable",
                error_message="All market inputs failed or were unavailable (evaluated=0)",
                macro_narrative="Live scan degraded: 0 usable market inputs evaluated.",
                scan_counts=counts,
                scan_id=run.receipt.scan_id,
                scan_archive=run.receipt.as_dict(),
            )

        inst_map = {opp.instrument.id.symbol: opp.instrument for opp in opportunities}
        opps_list: list[dict[str, Any]] = []

        if opportunities:
            # Runtime applies the shared publication policy and desk ranking.
            top_picks = [
                opp
                for opp in opportunities
                if len(opp.checklist) == 5 and all(item[1] is True for item in opp.checklist)
            ][:16]

            # Pre-fetch 1H candles for top opportunities concurrently (paced with semaphore)
            candle_sem = asyncio.Semaphore(10)

            async def _fetch_pick_candles(opp: Any) -> dict[str, Any]:
                opp_dict = cast(dict[str, Any], opp.as_dict())
                if opp_dict.get("candles"):
                    return opp_dict
                sym = opp.instrument.id.symbol
                inst = inst_map.get(sym)
                candles_payload = []
                if inst:
                    try:
                        async with candle_sem:
                            raw_candles = await asyncio.wait_for(
                                futures.candles(inst, "1h", 250), timeout=3.0
                            )
                            candles_payload = [
                                {
                                    "time": int(c.closed_at.timestamp()),
                                    "open": float(c.open),
                                    "high": float(c.high),
                                    "low": float(c.low),
                                    "close": float(c.close),
                                    "volume": float(c.volume),
                                }
                                for c in raw_candles
                                if c.closed
                            ]
                    except Exception:
                        pass
                opp_dict["candles"] = candles_payload
                return opp_dict

            pick_dicts = await asyncio.gather(*(_fetch_pick_candles(opp) for opp in top_picks))
            opps_list = list(pick_dicts)
        else:
            opps_list = []

        # Deduplicate by base asset preserving order (retaining single canonical contract per token)
        v33_kinetic_on = getattr(settings, "kinetic_ignition_v33_enabled", False) is True
        blocked_radar_pairs: dict[tuple[str, str], str] = {}
        if v33_kinetic_on:
            with contextlib.suppress(Exception):
                blocked_radar_pairs = _get_auto_watchlist_service(runtime).blocked_from_radar_symbols(
                    now=datetime.now(UTC),
                    cooldown_hours=12.0,
                )
        pre_ignition_coils: list[dict[str, Any]] = []
        seen_bases: set[str] = set()
        deduped_opps: list[dict[str, Any]] = []
        for o in opps_list:
            if o.get("setup_label") == "BREAKDOWN_RETEST_SHORT" and not publication_inputs_current(
                o.get("continuation_evidence"), datetime.now(UTC)
            ):
                continue
            if o.get("setup_label") == "RECOVERY_RECLAIM_LONG" and (
                getattr(settings, "early_recovery_enabled", False) is not True
                or not recovery_publication_inputs_current(
                    o.get("recovery_evidence"), datetime.now(UTC)
                )
            ):
                continue
            checklist = o.get("checklist")
            if not (
                isinstance(checklist, list)
                and len(checklist) == 5
                and all(isinstance(item, dict) and item.get("passed") is True for item in checklist)
            ):
                continue
            sym = str(o.get("symbol", ""))
            direction_key = str(o.get("direction", "long")).lower().strip()
            if v33_kinetic_on and (sym.upper().strip(), direction_key) in blocked_radar_pairs:
                continue
            if v33_kinetic_on and o.get("publication_lane") == "WATCHLIST_COIL":
                pre_ignition_coils.append(o)
                continue
            base = o.get("base_asset") or sym.removesuffix("USDT").removesuffix("USDC")
            if base and base not in seen_bases:
                seen_bases.add(base)
                o["radar_origin"] = "fresh"
                o.setdefault("origin_scan_id", run.receipt.scan_id)
                o.setdefault("discovery_price", o.get("current_price"))
                o.setdefault("radar_status", RadarLifecycleState.FRESH_COIL.value)
                o.setdefault("radar_status_label", "FRESH COIL")
                raw_cl = o.get("checklist") or []
                if isinstance(raw_cl, list) and raw_cl:
                    passed_cnt = sum(1 for c in raw_cl if isinstance(c, dict) and c.get("passed"))
                    failed_list = [
                        str(c.get("title", ""))
                        for c in raw_cl
                        if isinstance(c, dict) and not c.get("passed")
                    ]
                    o.setdefault("gates_passed", passed_cnt)
                    o.setdefault("gates_total", len(raw_cl))
                    o.setdefault("failed_gates", failed_list)
                else:
                    o.setdefault("gates_passed", 0)
                    o.setdefault("gates_total", 5)
                    o.setdefault("failed_gates", [])
                deduped_opps.append(o)

        active_policy_ver = screening_policy_version_for(settings)
        cached_prior, prior_policy_ver = _get_prior_episodes_for_reconciliation()
        prior_policy_current = prior_policy_ver == active_policy_ver
        prior_by_base = {
            str(
                p.get("base_asset")
                or str(p.get("symbol", "")).removesuffix("USDT").removesuffix("USDC")
            ): p
            for p in cached_prior if prior_policy_current
        }
        for fresh in deduped_opps:
            base = str(
                fresh.get("base_asset")
                or str(fresh.get("symbol", "")).removesuffix("USDT").removesuffix("USDC")
            )
            prev = prior_by_base.get(base)
            fresh["first_seen_at"] = fresh.get("evaluated_at")
            if prev and same_radar_episode(prev, fresh):
                fresh["first_seen_at"] = prev.get("first_seen_at") or prev.get("evaluated_at")
                if prev.get("radar_triggered_at"):
                    fresh["radar_triggered_at"] = prev["radar_triggered_at"]
        now = datetime.now(UTC)
        radar_events: list[dict[str, Any]] = []

        # Reconcile previously discovered radar candidates so active trades (AT_ENTRY_ZONE,
        # IN_ACTIVE_TRADE, ACTIVE_COIL) are retained until they hit stop loss or final target.
        prior_opps = cached_prior if prior_policy_current else []
        if not prior_policy_current:
            radar_events.extend(
                {
                    "symbol": candidate.get("symbol"), "at": now.isoformat(),
                    "state": "POLICY_CHANGED", "retained": False,
                    "reason": "screening_policy_changed_requires_fresh_qualification",
                    "candidate": {k: v for k, v in candidate.items() if k != "candles"},
                }
                for candidate in cached_prior
            )
        publication_quotes: dict[str, Quote] = {}
        raw_retained_prior = await reconcile_radar_cohort(
            prior_opportunities=prior_opps,
            seen_bases=set(),
            futures=futures,
            events=radar_events,
            bearish_continuation_enabled=getattr(settings, "bearish_continuation_enabled", False) is True,
            early_recovery_enabled=getattr(settings, "early_recovery_enabled", False) is True,
            additional_instruments=[inst_map[o["symbol"]] for o in deduped_opps
                                    if o.get("symbol") in inst_map],
            quote_sink=publication_quotes,
        )
        retained_prior: list[dict[str, Any]] = []
        for p in raw_retained_prior:
            failed_g = p.get("failed_gates") or []
            first_seen_raw = p.get("first_seen_at") or p.get("evaluated_at")
            age_hours = float("inf")
            if isinstance(first_seen_raw, str) and first_seen_raw:
                with contextlib.suppress(ValueError, TypeError):
                    discovered_at = datetime.fromisoformat(first_seen_raw)
                    if discovered_at.tzinfo is not None and discovered_at <= now:
                        age_hours = (now - discovered_at).total_seconds() / 3600.0
            checklist = p.get("checklist") or []
            gates_verified = (
                p.get("radar_data_status") == "current"
                and len(checklist) == 5
                and all(item.get("passed") is True and item.get("status", "passed") == "passed"
                        for item in checklist)
            )
            if failed_g or not gates_verified:
                radar_events.append(
                    {
                        "symbol": p.get("symbol"),
                        "at": now.isoformat(),
                        "state": "INVALIDATED" if failed_g else "DATA_UNAVAILABLE",
                        "retained": False,
                        "reason": "radar_gates_degraded" if failed_g else "radar_gates_unverified",
                        "failed_gates": list(failed_g),
                        "first_seen_at": first_seen_raw,
                        "candidate": {k: v for k, v in p.items() if k != "candles"},
                    }
                )
            elif age_hours > 96.0:
                radar_events.append(
                    {
                        "symbol": p.get("symbol"),
                        "at": now.isoformat(),
                        "state": "EXPIRED",
                        "retained": False,
                        "reason": "max_trade_horizon_exceeded",
                        "first_seen_at": first_seen_raw,
                        "candidate": {k: v for k, v in p.items() if k != "candles"},
                    }
                )
            else:
                retained_prior.append(p)

        macro_narrative = (
            opportunities[0].macro_narrative
            if opportunities
            else (
                str(_screener_cache.get("macro_narrative"))
                if retained_prior
                and isinstance(_screener_cache, dict)
                and _screener_cache.get("data_status") not in ("scan_failed", "degraded_zero_usable_inputs")
                and _screener_cache.get("macro_narrative")
                else (
                    _episode_macro_narrative
                    if retained_prior and _episode_macro_narrative
                    else "No qualifying candidates; macro context unavailable in this result."
                )
            )
        )
        # Freshly screened setups lead first, followed by active retained setups
        retained_by_base = {
            str(
                p.get("base_asset")
                or str(p.get("symbol", "")).removesuffix("USDT").removesuffix("USDC")
            ): p
            for p in retained_prior
        }
        final_opps = []
        for fresh in deduped_opps:
            base = str(
                fresh.get("base_asset")
                or str(fresh.get("symbol", "")).removesuffix("USDT").removesuffix("USDC")
            )
            prev = prior_by_base.get(base)
            same_episode = prev is not None and fresh.get("first_seen_at") == (
                prev.get("first_seen_at") or prev.get("evaluated_at")
            )
            if same_episode:
                if base in retained_by_base:
                    final_opps.append(retained_by_base.pop(base))
            else:
                record_radar_publication(
                    fresh, publication_quotes.get(str(fresh.get("symbol"))),
                    datetime.now(UTC), run.receipt.scan_id,
                )
                final_opps.append(fresh)
                superseded = retained_by_base.pop(base, None)
                if superseded is not None:
                    radar_events.append(
                        {
                            "symbol": superseded.get("symbol"),
                            "at": now.isoformat(),
                            "state": "SUPERSEDED",
                            "retained": False,
                            "reason": "replaced_by_new_thesis",
                            "candidate": {k: v for k, v in superseded.items() if k != "candles"},
                        }
                    )
                radar_events.append(
                    {
                        "symbol": fresh.get("symbol"),
                        "at": now.isoformat(),
                        "state": "FRESH_COIL",
                        "retained": True,
                        "reason": "new_setup" if prev is None else "replaced_by_new_setup",
                        "first_seen_at": fresh.get("first_seen_at"),
                        "candidate": {k: v for k, v in fresh.items() if k != "candles"},
                    }
                )
        final_opps.extend(retained_by_base.values())

        result = {
            "timestamp": now.isoformat(),
            "total_scanned": run.counts.get("evaluated", 0),
            "scan_counts": run.counts,
            "scan_id": run.receipt.scan_id,
            "scan_archive": run.receipt.as_dict(),
            "mode": "screener",
            "order_execution_enabled": False,
            "validation_status": "uncalibrated_research",
            "screening_policy_version": active_policy_ver,
            "screening_feature_contract": "discovery_v2",
            "qualifying_count": len(final_opps),
            "fresh_count": sum(p.get("radar_origin") == "fresh" for p in final_opps),
            "retained_count": sum(p.get("radar_origin") == "retained" for p in final_opps),
            "data_unavailable_count": sum(
                p.get("radar_data_status") == "unavailable" for p in final_opps
            ),
            "macro_narrative": macro_narrative,
            "opportunities": final_opps,
            "radar_events": radar_events,
            "radar_lifecycle_version": RADAR_PUBLICATION_VERSION,
        }
        # Persist every reconciliation, including removals, independently of the sidebar.
        # This is an operational audit trail, not prospective study enrollment.
        try:
            await asyncio.to_thread(
                atomic_write_json,
                Path("data/radar_events") / f"{uuid4()}.json",
                {
                    "schema": "atlas-radar-events-v2",
                    "scan_id": run.receipt.scan_id,
                    "at": now.isoformat(),
                    "events": radar_events,
                },
            )
            result["radar_audit_status"] = "persisted"
        except Exception:
            result["radar_audit_status"] = "unavailable"
        result["data_status"] = "current"
        result["stale"] = False
        result["coi_status"] = (
            "active" if run.counts.get("coi_regime_active") else "neutral_unconfigured"
        )
        _screener_cache = result
        _cache_timestamp = now
        _episode_roster = list(final_opps)
        _episode_policy_version = active_policy_ver
        _episode_macro_narrative = macro_narrative
        _last_scan_succeeded_at = now
        _last_scan_error = None
        _consecutive_scan_failures = 0

        # Automatically enroll verified 5/5 episodes into the 48h Auto-Watchlist & update outcomes
        with contextlib.suppress(Exception):
            auto_watch = _get_auto_watchlist_service(runtime)
            await asyncio.to_thread(
                auto_watch.sync_from_scan,
                published_opportunities=final_opps,
                radar_events=radar_events,
                now=now,
                scan_id=run.receipt.scan_id,
            )
            if pre_ignition_coils:
                for coil_cand in pre_ignition_coils:
                    await asyncio.to_thread(
                        auto_watch.enroll_pre_ignition_coil,
                        coil_cand,
                        published_at=now,
                        scan_id=run.receipt.scan_id,
                    )
            futures_market = runtime.futures_market
            if _cached_instruments is None:
                with contextlib.suppress(Exception):
                    _cached_instruments = await asyncio.wait_for(
                        futures_market.instruments(), timeout=3.0
                    )
            if _cached_instruments:
                await asyncio.wait_for(
                    auto_watch.refresh_and_evaluate(
                        futures_market,
                        _cached_instruments,
                        force_refresh=True,
                        internal_scheduler=True,
                        now=now,
                    ),
                    timeout=10.0,
                )

        # Persist latest live scan to disk atomically off the main event loop thread
        try:
            cache_file = Path("data/screener_latest.json")
            await asyncio.to_thread(atomic_write_json, cache_file, result)
            result["cache_persisted"] = True
        except Exception:
            result["cache_persisted"] = False

        # Automatically prune old/excess raw scan archives:
        # Keep up to 49 hourly scans (inclusive 48h window: 0h..48h) / 8 GiB cap,
        # and ALWAYS pin scan IDs referenced by active episodes.
        pinned_scan_ids = frozenset(
            {run.receipt.scan_id}
            | {
                str(opp["origin_scan_id"])
                for opp in final_opps
                if isinstance(opp.get("origin_scan_id"), str) and opp["origin_scan_id"]
            }
        )
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                LocalScanArchive(settings.research_archive_dir).prune_scans,
                max_age_days=5,
                max_scans=49,
                max_bytes=8_589_934_592,
                preserve_scan_ids=pinned_scan_ids,
                exclude_pinned_from_count=True,
            )

        return result
    except Exception as exc:
        # Preserve private episode roster for recovery while withholding unverified cards
        # and atomically persisting the operational failure envelope across restarts.
        return await _persist_degraded_state(
            data_status="scan_failed",
            stale_reason="last_scan_failed",
            error_message=str(exc),
            macro_narrative=f"Live scan issue: {exc}",
        )
    finally:
        _is_refreshing = False


@portal_router.get("/screener/live")
async def get_live_screener(
    mode: str = "all",
    min_score: float = 50.0,
    max_symbols: int = 320,
    scope: str = "core",
    refresh: bool = False,
    wait: bool = False,
    response: Response = None,  # type: ignore[assignment]
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Retrieves verified live pre-breakout institutional screener results with candles.

    Anonymous GET requests are strictly read-only. Explicit `refresh=True` requires operator auth.
    """
    global _screener_cache, _cache_timestamp, _screener_task, _last_manual_refresh_at

    if response:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"

    try:
        ScreenerMode(mode.lower())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Unsupported screener mode: {mode}") from exc
    bounded_max = max(1, min(max_symbols, 500))
    effective_max = 500 if scope.lower() == "full" or bounded_max >= 500 else bounded_max

    if refresh:
        _require_operator_auth(authorization)
        now_req = datetime.now(UTC)
        if (
            _last_manual_refresh_at is not None
            and (now_req - _last_manual_refresh_at).total_seconds() < MANUAL_REFRESH_COOLDOWN_SECONDS
            and (_screener_task is None or _screener_task.done())
        ):
            raise HTTPException(
                status_code=429,
                detail="Manual refresh cooldown active; wait before triggering another universe scan",
            )
        # Launch non-blocking background scan so HTTP request never times out
        # If a scheduled cron sweep is already executing, do not enqueue a redundant duplicate scan.
        async with _screener_task_lock:
            if (_screener_task is None or _screener_task.done()) and not _is_refreshing:
                _last_manual_refresh_at = now_req
                _screener_task = asyncio.create_task(
                    _do_crypto_screen(mode, min_score, effective_max)
                )
        if wait and _screener_task:
            await _screener_task

    is_manual_scanning = _screener_task is not None and not _screener_task.done()

    if is_manual_scanning:
        if _screener_cache.get("data_status") == "startup_revalidation_required":
            active_settings = (
                _shared_runtime.settings if _shared_runtime is not None else load_settings()
            )
            _, projected = _project_public_screener_state(active_settings)
            projected["is_scanning"] = True
            projected["scan_source"] = "scheduled"
            return projected
        # Never serve cached candidate results while an explicit manual/init scan is running
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "total_scanned": 0,
            "qualifying_count": 0,
            "macro_narrative": "Live algorithmic universe scan in progress...",
            "opportunities": [],
            "is_scanning": True,
            "scan_source": "manual_or_init",
        }

    # If no live scan exists yet in memory, attempt warm-up from persistent disk before initiating scan
    if not _screener_cache:
        await asyncio.to_thread(warm_screener_cache)

    # Use the single authoritative publication projection shared with /chart/{symbol}.
    active_settings = _shared_runtime.settings if _shared_runtime is not None else load_settings()
    pub_status, projected = _project_public_screener_state(active_settings)

    if pub_status == "needs_refresh":
        async with _screener_task_lock:
            if _screener_task is None or _screener_task.done():
                _screener_task = asyncio.create_task(
                    _do_crypto_screen("all", 50.0, 320)
                )
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "total_scanned": 0,
            "qualifying_count": 0,
            "macro_narrative": "Initiating live algorithmic scan across Binance universe...",
            "opportunities": [],
            "is_scanning": True,
        }

    if pub_status == "startup_revalidation_required":
        async with _screener_task_lock:
            if _screener_task is None or _screener_task.done():
                _screener_task = asyncio.create_task(_do_crypto_screen("all", 50.0, 320))
        projected["is_scanning"] = True
        projected["scan_source"] = "scheduled"
        return projected

    if pub_status != "current":
        return projected

    projected["is_scanning"] = bool(_is_refreshing)
    if _is_refreshing:
        projected["scan_source"] = "scheduled"
    return projected


@portal_router.post("/screener/rescan")
async def trigger_screener_rescan(
    mode: str = "all",
    min_score: float = 50.0,
    max_symbols: int = 320,
    scope: str = "core",
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Operator-only synchronous deep rescan returning newly qualified candidates."""
    _require_operator_auth(authorization)
    return await get_live_screener(
        mode=mode,
        min_score=min_score,
        max_symbols=max_symbols,
        scope=scope,
        refresh=True,
        wait=True,
        authorization=authorization,
    )


# --- Automated Hourly Screener Cron Service ---
_cron_task: asyncio.Task[None] | None = None
_last_cron_run: datetime | None = None
_next_cron_run: datetime | None = None


def seconds_until_next_cycle(
    now: datetime, interval_hours: int = 1, grace_seconds: int = 60
) -> float:
    """Calculates seconds until the next aligned UTC cycle (e.g., at XX:01:00 UTC for 1H)."""
    utc_now = now.astimezone(UTC)
    boundary_hour = (utc_now.hour // interval_hours) * interval_hours
    boundary = utc_now.replace(hour=boundary_hour, minute=0, second=0, microsecond=0)
    target = boundary + timedelta(seconds=grace_seconds)
    if target <= utc_now:
        target = boundary + timedelta(hours=interval_hours, seconds=grace_seconds)
    return max(0.0, (target - utc_now).total_seconds())


# Backwards compatibility alias
seconds_until_next_8h_cycle = seconds_until_next_cycle


async def _screener_cron_loop(interval_hours: int = 1) -> None:
    global _last_cron_run, _next_cron_run, _screener_task
    # If cache on startup is stale (>interval_hours) or lacks the current publication policy, refresh immediately
    now_init = datetime.now(UTC)
    cache_age_hours = (
        (now_init - _cache_timestamp).total_seconds() / 3600.0
        if _cache_timestamp is not None
        else 999.0
    )
    cron_settings = _shared_runtime.settings if _shared_runtime is not None else load_settings()
    if (
        not _screener_cache
        or _screener_cache.get("data_status") == "startup_revalidation_required"
        or cache_age_hours >= float(interval_hours)
        or _screener_cache.get("radar_lifecycle_version") != RADAR_PUBLICATION_VERSION
        or _screener_cache.get("screening_policy_version") != screening_policy_version_for(cron_settings)
    ):
        try:
            async with _screener_task_lock:
                if _screener_task is None or _screener_task.done():
                    _screener_task = asyncio.create_task(
                        _do_crypto_screen(mode="all", min_score=50.0, max_symbols=320)
                    )
            if _screener_task:
                await _screener_task
            try:
                await _run_short_watch_cycle()
            except Exception:
                logging.getLogger(__name__).exception("Top-gainer short-watch cycle failed")
        except Exception as init_exc:
            print(f"❌ [Cron] Initial startup refresh error: {init_exc}")

    while True:
        try:
            delay = seconds_until_next_cycle(
                datetime.now(UTC), interval_hours=interval_hours, grace_seconds=60
            )
            _next_cron_run = datetime.now(UTC) + timedelta(seconds=delay)
            print(
                f"⏰ [Cron] Sleeping {delay:.0f}s ({delay / 60:.1f}m) until next {interval_hours}-hour cycle at "
                f"{_next_cron_run.strftime('%Y-%m-%d %H:%M:%S UTC')}..."
            )
            await asyncio.sleep(delay)
            _last_cron_run = datetime.now(UTC)
            print(
                f"🚀 [Cron] Waking up for aligned {interval_hours}-hour USD-M screener sweep (all liquid contracts)..."
            )
            async with _screener_task_lock:
                active_manual_scan = (
                    _screener_task
                    if _screener_task is not None and not _screener_task.done()
                    else None
                )
            if active_manual_scan is not None:
                await active_manual_scan
            else:
                await _do_crypto_screen(mode="all", min_score=50.0, max_symbols=320)
            try:
                await _run_short_watch_cycle()
            except Exception:
                logging.getLogger(__name__).exception("Top-gainer short-watch cycle failed")
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"❌ [Cron] Error during scheduled scan cycle: {exc}")
            await asyncio.sleep(60.0)


async def _merge_scout_promotions(
    promoted: list[dict[str, Any]],
    promoted_at: datetime,
    scan_id: str,
) -> None:
    """Merge 5/5 quarter-hour kinetic promotions into live Radar cache without clobbering retained cards."""
    global _screener_cache, _cache_timestamp, _episode_roster
    if not promoted or not _screener_cache:
        return
    existing = list(_screener_cache.get("opportunities") or [])
    seen_syms = {str(p.get("symbol", "")).upper() for p in promoted}
    merged = list(promoted) + [
        o for o in existing if str(o.get("symbol", "")).upper() not in seen_syms
    ]
    merged = merged[:16]
    updated = dict(_screener_cache)
    updated["opportunities"] = merged
    updated["qualifying_count"] = len(merged)
    updated["fresh_count"] = sum(p.get("radar_origin") == "fresh" for p in merged)
    updated["retained_count"] = sum(p.get("radar_origin") == "retained" for p in merged)
    updated["last_scout_scan_id"] = scan_id
    updated["last_scout_at"] = promoted_at.isoformat()
    _screener_cache = updated
    _cache_timestamp = promoted_at
    _episode_roster = list(merged)
    with contextlib.suppress(Exception):
        await asyncio.to_thread(atomic_write_json, Path("data/screener_latest.json"), updated)


async def _kinetic_scout_loop() -> None:
    """Aligned :16/:31/:46 UTC 15-minute Kinetic Scout loop (skips :01 full-sweep window)."""
    global _kinetic_scout_service
    while True:
        try:
            now = datetime.now(UTC)
            # Next quarter-hour + 60s (:01, :16, :31, :46)
            epoch = int(now.timestamp())
            next_q = ((epoch // 900) + 1) * 900 + 60
            delay = max(5.0, float(next_q - now.timestamp()))
            await asyncio.sleep(delay)
            tick_now = datetime.now(UTC)
            if tick_now.minute < 6 or tick_now.minute >= 58:
                continue
            runtime = await get_portal_runtime()
            if getattr(runtime.settings, "kinetic_scout_15m_enabled", False) is not True:
                continue
            if _kinetic_scout_service is None:
                _kinetic_scout_service = KineticScoutService(
                    settings=runtime.settings,
                    market=runtime.futures_market,
                    derivatives=runtime.futures_market,
                    macro_provider=runtime.macro_provider,
                    auto_watchlist=_get_auto_watchlist_service(runtime),
                    scan_lock=_scan_execution_lock,
                    on_promoted=_merge_scout_promotions,
                )
            await _kinetic_scout_service.run_quarter_hour_tick(now=tick_now)
        except asyncio.CancelledError:
            break
        except Exception:
            logging.getLogger(__name__).exception("Kinetic 15m scout cycle failed")
            await asyncio.sleep(30.0)


def start_screener_cron(interval_hours: int = 1) -> None:
    """Starts the background screener daemon (and optional 15m kinetic scout) if not already running."""
    global _cron_task, _kinetic_scout_task
    if not _screener_cache:
        warm_screener_cache()
    if _cron_task is None or _cron_task.done():
        _cron_task = asyncio.create_task(
            _screener_cron_loop(interval_hours=interval_hours),
            name=f"atlas-{interval_hours}h-screener-cron",
        )
        print(
            f"✅ [Cron] Atlas {interval_hours}-hour background screener cron started successfully."
        )
    active_settings = _shared_runtime.settings if _shared_runtime is not None else load_settings()
    if getattr(active_settings, "kinetic_scout_15m_enabled", False) is True:
        if _kinetic_scout_task is None or _kinetic_scout_task.done():
            _kinetic_scout_task = asyncio.create_task(
                _kinetic_scout_loop(),
                name="atlas-15m-kinetic-scout",
            )


async def stop_portal() -> None:
    """Cancel scans before closing their shared public-data clients."""
    global _cron_task, _screener_task, _kinetic_scout_task, _kinetic_scout_service
    global _shared_runtime, _cached_instruments, _short_watch_service
    global _auto_watchlist_service, _auto_watchlist_backfilled
    global _cache_timestamp, _last_cron_run, _next_cron_run
    global _episode_roster, _episode_policy_version, _episode_macro_narrative
    global _last_scan_attempted_at, _last_scan_succeeded_at, _last_scan_error
    global _consecutive_scan_failures, _last_manual_refresh_at
    tasks = [task for task in (_cron_task, _screener_task, _kinetic_scout_task) if task is not None]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _cron_task = None
    _screener_task = None
    _kinetic_scout_task = None
    _kinetic_scout_service = None
    _short_watch_service = None
    _auto_watchlist_service = None
    _auto_watchlist_backfilled = False
    async with _runtime_lock:
        if _shared_runtime is not None:
            await _shared_runtime.close()
            _shared_runtime = None
    _cached_instruments = None
    _screener_cache.clear()
    _episode_roster = []
    _episode_policy_version = None
    _episode_macro_narrative = None
    _cache_timestamp = _last_cron_run = _next_cron_run = None
    _last_scan_attempted_at = _last_scan_succeeded_at = _last_scan_error = None
    _consecutive_scan_failures = 0
    _last_manual_refresh_at = None


@portal_router.get("/screener/short-watch")
async def get_short_watch() -> dict[str, Any]:
    """Returns bounded Top-Gainer Short Watch episodes (never alters live 5/5 publication)."""
    service = _get_short_watch_service()
    return service.snapshot()


@portal_router.post("/screener/short-watch/tick")
async def trigger_short_watch_tick(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Operator-only manual cycle; public readers cannot enqueue market scans."""
    _require_operator_auth(authorization)
    return await _run_short_watch_cycle()


@portal_router.get("/screener/cron/status")
async def get_cron_status() -> dict[str, Any]:
    """Returns the current operational status and next run time of the screener cron."""
    global _last_cron_run, _next_cron_run, _cron_task
    now = datetime.now(UTC)
    settings = load_settings()
    interval_hours = settings.screener_interval_hours
    delay = seconds_until_next_cycle(now, interval_hours=interval_hours, grace_seconds=60)
    next_run = _next_cron_run or (now + timedelta(seconds=delay))
    is_running = _cron_task is not None and not _cron_task.done()
    unit = "hour" if interval_hours == 1 else "hours"
    return {
        "status": "running" if is_running else "idle",
        "interval_hours": interval_hours,
        "schedule": f"Every {interval_hours} {unit} at aligned UTC candle closes plus 60 seconds",
        "last_run": _last_cron_run.isoformat() if _last_cron_run else None,
        "next_run": next_run.isoformat(),
        "seconds_remaining": int(delay),
        "total_universe_scanned": _screener_cache.get("total_scanned", 0),
    }


@portal_router.get("/chart/{symbol}")
async def get_symbol_chart(symbol: str, interval: str = "1h", limit: int = 250) -> dict[str, Any]:
    """Fetches high-resolution candles and calculated indicators/opportunity for a specific symbol."""
    global _cached_instruments, _screener_cache
    runtime = await get_portal_runtime()
    futures = runtime.futures_market

    sym_clean = symbol.upper()
    if _cached_instruments is None:
        try:
            _cached_instruments = await asyncio.wait_for(futures.instruments(), timeout=5.0)
        except Exception:
            _cached_instruments = []

    inst = next((i for i in _cached_instruments if i.id.symbol == sym_clean), None)
    if not inst:
        try:
            _cached_instruments = await asyncio.wait_for(futures.instruments(), timeout=5.0)
            inst = next((i for i in _cached_instruments if i.id.symbol == sym_clean), None)
        except Exception:
            pass

    if not inst:
        from algotrader.domain.models import AssetClass, Instrument, InstrumentId, MarketKind

        inst = Instrument(
            id=InstrumentId(futures.venue, sym_clean),
            base_asset=sym_clean.replace("USDT", ""),
            quote_asset="USDT",
            asset_class=AssetClass.CRYPTO,
            market_kind=MarketKind.FUTURES,
            trading=True,
            price_tick=Decimal("0.0001"),
            quantity_step=Decimal("0.1"),
            min_quantity=Decimal("0.1"),
            min_notional=Decimal("5.0"),
        )

    candles_payload: list[dict[str, Any]] = []
    try:
        raw_candles = await futures.candles(inst, interval, limit)
        candles_payload = [
            {
                "time": int(c.closed_at.timestamp()),
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": float(c.volume),
            }
            for c in raw_candles
            if c.closed
        ]
    except Exception as exc:
        print(f"Error fetching candles for {sym_clean}: {exc}")

    # Discriminated response provenance:
    # - published_radar_candidate: present in current, non-stale screener publication
    # - watchlist_view: saved watchlist item (never synthesizes passing audit checks or EV)
    # - chart_inspection: raw Binance USD-M futures chart inspection (never synthesizes signal metadata)
    opp_dict: dict[str, Any] | None = None
    watchlist_view: dict[str, Any] | None = None
    inspection: dict[str, Any] | None = None
    record_kind = "chart_inspection"

    # 1. Check ONLY the single authoritative current publication projection (identical to /screener/live).
    active_settings = _shared_runtime.settings if _shared_runtime is not None else load_settings()
    pub_status, projected = _project_public_screener_state(active_settings)
    if pub_status == "current":
        matched_opp = next(
            (
                o
                for o in projected.get("opportunities", [])
                if o.get("symbol", "").upper() == sym_clean
            ),
            None,
        )
        if matched_opp:
            opp_dict = dict(matched_opp)
            opp_dict["record_kind"] = "published_radar_candidate"
            record_kind = "published_radar_candidate"

    # 2. Check if it's on the watchlist (never fabricates passing checks when audit is absent)
    watchlist_item = _watchlist_service.get_item(sym_clean)
    if watchlist_item:
        cur_p = float(watchlist_item.current_price)
        if candles_payload:
            cur_p = candles_payload[-1]["close"]

        trigger_p = float(watchlist_item.trigger_price)
        support_p = float(watchlist_item.support_price)
        stop_p = float(watchlist_item.invalidation_price)
        t1 = float(watchlist_item.target_1)
        t2 = float(watchlist_item.target_2)

        risk = abs(support_p - stop_p)
        reward = abs(t1 - support_p)
        rr: float | None = round(reward / risk, 2) if risk > 0 and reward > 0 else None

        gate_statuses = (
            getattr(watchlist_item.audit, "gate_statuses", None) if watchlist_item.audit else None
        )
        if gate_statuses:
            checklist = [
                {
                    "title": gate_name,
                    "passed": bool(is_passed),
                    "status": "passed" if is_passed else "failed",
                    "detail": "Holding key structural threshold"
                    if is_passed
                    else "Pending threshold trigger",
                }
                for gate_name, is_passed in gate_statuses.items()
            ]
            audit_verified = True
        else:
            checklist = [
                {
                    "title": "Live Audit Pending",
                    "passed": False,
                    "status": "unknown",
                    "detail": "No live audit recorded; saved watchlist levels are unverified.",
                }
            ]
            audit_verified = False

        if opp_dict is None:
            record_kind = "watchlist_view"
            watchlist_view = {
                "record_kind": "watchlist_view",
                "symbol": sym_clean,
                "venue": futures.venue.value,
                "market_kind": "futures",
                "qualified": False,
                "audit_verified": audit_verified,
                "current_price": str(cur_p),
                "tick_size": str(inst.price_tick),
                "stage": str(watchlist_item.stage),
                "timeframe": watchlist_item.timeframe,
                "thesis": watchlist_item.thesis,
                "support_price": str(support_p),
                "trigger_price": str(trigger_p),
                "invalidation_price": str(stop_p),
                "target_1": str(t1),
                "target_2": str(t2),
                "risk_reward_ratio": str(rr) if rr is not None else None,
                "checklist": checklist,
                "checklist_passed": sum(1 for c in checklist if c["passed"]),
                "checklist_total": len(checklist),
                "estimated_win_rate_pct": None,
                "expected_value_r": None,
                "breakout_readiness_status": "WATCHLIST_AUDITED" if audit_verified else "WATCHLIST_UNVERIFIED",
                "notes": watchlist_item.notes,
            }

    # 3. Unqualified symbol inspection: raw futures candles + instrument metadata only.
    if opp_dict is None and watchlist_view is None:
        c_price = candles_payload[-1]["close"] if candles_payload else None
        inspection = {
            "record_kind": "chart_inspection",
            "symbol": sym_clean,
            "venue": futures.venue.value,
            "market_kind": "futures",
            "qualified": False,
            "current_price": str(c_price) if c_price is not None else None,
            "tick_size": str(inst.price_tick),
            "note": f"Direct Binance USD-M futures chart inspection for {sym_clean}. Not a qualified Atlas radar signal.",
        }

    return {
        "record_kind": record_kind,
        "symbol": sym_clean,
        "venue": futures.venue.value,
        "market_kind": "futures",
        "timeframe": interval,
        "count": len(candles_payload),
        "candles": candles_payload,
        "opportunity": opp_dict,
        "watchlist_view": watchlist_view,
        "inspection": inspection,
    }


@portal_router.get("/watchlist/auto")
async def get_auto_watchlist(
    refresh: bool = False,
    days: int = 14,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Returns 48h auto-tracked Radar candidates, immediately resolved outcomes, and performance summary.

    Security invariant (W4):
    - Public reads respect the server-side singleflight cooldown (cache-only when fresh).
    - Explicit forced refresh (`refresh=true`) requires operator authorization and server cooldown.
    """
    global _cached_instruments
    bounded_days = max(1, min(90, days))
    if refresh:
        _require_operator_auth(authorization)
    active_settings = _shared_runtime.settings if _shared_runtime is not None else load_settings()
    rec_enabled = getattr(active_settings, "early_recovery_enabled", False) is True
    auto_watch = _get_auto_watchlist_service()
    live_syms = _live_radar_symbols_from_cache()
    if not refresh and not auto_watch.refresh_due():
        return auto_watch.build_response(
            days=bounded_days,
            live_radar_symbols=live_syms,
            early_recovery_enabled=rec_enabled,
        )
    try:
        runtime = await get_portal_runtime()
        rec_enabled = getattr(runtime.settings, "early_recovery_enabled", False) is True
        auto_watch = _get_auto_watchlist_service(runtime)
        futures = runtime.futures_market
        if _cached_instruments is None:
            _cached_instruments = await asyncio.wait_for(futures.instruments(), timeout=3.0)
        if _cached_instruments:
            await asyncio.wait_for(
                auto_watch.refresh_and_evaluate(
                    futures,
                    _cached_instruments,
                    force_refresh=refresh,
                ),
                timeout=12.0,
            )
        return auto_watch.build_response(
            days=bounded_days,
            live_radar_symbols=_live_radar_symbols_from_cache(),
            early_recovery_enabled=rec_enabled,
        )
    except HTTPException:
        raise
    except Exception as exc:
        payload = auto_watch.build_response(
            days=bounded_days,
            live_radar_symbols=_live_radar_symbols_from_cache(),
            early_recovery_enabled=rec_enabled,
        )
        payload["refresh_warning"] = f"{type(exc).__name__}: {exc}"
        return payload


@portal_router.get("/watchlist")
async def get_watchlist(
    live_audit: bool = True,
    refresh: bool = False,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Returns all tokens on institutional watch for coming days, enriched with live gate audits."""
    global _cached_instruments
    if refresh:
        _require_operator_auth(authorization)
    if not live_audit:
        items = _watchlist_service.list_items()
        return {
            "items": [item.as_dict() for item in items],
            "total": len(items),
        }

    if not _watchlist_service.audit_refresh_due():
        items = _watchlist_service.list_items()
        fresh_cached = _watchlist_service.get_fresh_audit_cache()
        enriched_cached = []
        for item in items:
            aud = fresh_cached.get(item.symbol) or _watchlist_service.audit_item(
                item, None, [], [], None
            )
            eff_price = aud.live_price if (aud.live_price is not None and aud.live_price > 0) else item.current_price
            enriched_cached.append(
                replace(
                    item,
                    current_price=eff_price,
                    distance_to_trigger_pct=(
                        abs(item.trigger_price - eff_price) / eff_price * Decimal("100")
                        if eff_price > 0
                        else item.distance_to_trigger_pct
                    ),
                    audit=aud,
                )
            )
        return {
            "items": [item.as_dict() for item in enriched_cached],
            "total": len(enriched_cached),
        }

    try:
        runtime = await get_portal_runtime()
        futures = runtime.futures_market
        if _cached_instruments is None:
            _cached_instruments = await asyncio.wait_for(futures.instruments(), timeout=3.0)

        items = await asyncio.wait_for(
            _watchlist_service.list_items_with_audit(
                futures, _cached_instruments, force_refresh=refresh
            ),
            timeout=12.0,
        )
        return {
            "items": [item.as_dict() for item in items],
            "total": len(items),
        }
    except HTTPException:
        raise
    except Exception as exc:
        items = _watchlist_service.list_items()
        cached = _watchlist_service.get_fresh_audit_cache()
        enriched = []
        for item in items:
            aud = cached.get(item.symbol) or _watchlist_service.audit_item(
                item, None, [], [], None
            )
            eff_price = aud.live_price if (aud.live_price is not None and aud.live_price > 0) else item.current_price
            enriched.append(
                replace(
                    item,
                    current_price=eff_price,
                    distance_to_trigger_pct=(
                        abs(item.trigger_price - eff_price) / eff_price * Decimal("100")
                        if eff_price > 0
                        else item.distance_to_trigger_pct
                    ),
                    audit=aud,
                )
            )
        return {
            "items": [item.as_dict() for item in enriched],
            "total": len(enriched),
            "audit_warning": f"{type(exc).__name__}: {exc}",
        }


@portal_router.get("/watchlist/audit")
async def get_watchlist_audit(
    refresh: bool = False,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Runs or retrieves real-time technical health and gate diagnostics for all watched positions."""
    global _cached_instruments
    if refresh:
        _require_operator_auth(authorization)
    if not _watchlist_service.audit_refresh_due():
        fresh_cached = _watchlist_service.get_fresh_audit_cache()
        if fresh_cached:
            return {
                "audits": {sym: audit.as_dict() for sym, audit in fresh_cached.items()},
                "count": len(fresh_cached),
            }
    try:
        runtime = await get_portal_runtime()
        futures = runtime.futures_market
        if _cached_instruments is None:
            _cached_instruments = await asyncio.wait_for(futures.instruments(), timeout=3.0)

        audits = await asyncio.wait_for(
            _watchlist_service.run_live_audit(futures, _cached_instruments, force_refresh=refresh),
            timeout=12.0,
        )
        return {
            "audits": {sym: audit.as_dict() for sym, audit in audits.items()},
            "count": len(audits),
        }
    except HTTPException:
        raise
    except Exception as exc:
        items = _watchlist_service.list_items()
        cached = _watchlist_service.get_fresh_audit_cache()
        results = {}
        for item in items:
            aud = cached.get(item.symbol) or _watchlist_service.audit_item(
                item, None, [], [], None
            )
            results[item.symbol] = aud.as_dict()
        return {"audits": results, "count": len(results), "warning": f"{type(exc).__name__}: {exc}"}


@portal_router.post("/watchlist/audit")
async def post_watchlist_audit(payload: BatchAuditWatchlistRequest) -> dict[str, Any]:
    """Stateless per-user live gate audit endpoint for user-specific Supabase watchlist items."""
    global _cached_instruments
    domain_items: list[WatchlistItem] = []
    for req in payload.items:
        win_rate, _ = calculate_opportunity_win_rate_and_ev(
            setup_label=req.stage,
            score=Decimal(str(req.score)),
            checklist_passed=req.checklist_passed,
            checklist_total=req.checklist_total,
            risk_reward_ratio=Decimal("3.0"),
        )
        domain_items.append(
            WatchlistItem(
                symbol=req.symbol.upper(),
                name=req.name or req.symbol.upper(),
                sector=req.sector,
                stage=WyckoffStage(req.stage)
                if req.stage in [s.value for s in WyckoffStage]
                else req.stage,
                timeframe=req.timeframe,
                current_price=Decimal(str(req.current_price)),
                trigger_price=Decimal(str(req.trigger_price)),
                support_price=Decimal(str(req.support_price)),
                invalidation_price=Decimal(str(req.invalidation_price)),
                target_1=Decimal(str(req.target_1)),
                target_2=Decimal(str(req.target_2)),
                distance_to_trigger_pct=Decimal(str(req.distance_to_trigger_pct)),
                score=Decimal(str(req.score)),
                thesis=req.thesis,
                checklist_passed=req.checklist_passed,
                checklist_total=req.checklist_total,
                priority=WatchlistPriority(req.priority)
                if req.priority in [p.value for p in WatchlistPriority]
                else WatchlistPriority.HIGH,
                notes=req.notes,
                estimated_win_rate_pct=win_rate,
            )
        )

    if not domain_items:
        return {"items": [], "audits": {}, "total": 0}

    try:
        runtime = await get_portal_runtime()
        futures = runtime.futures_market
        if _cached_instruments is None:
            _cached_instruments = await asyncio.wait_for(futures.instruments(), timeout=3.0)

        enriched = await asyncio.wait_for(
            _watchlist_service.enrich_items_with_audit(
                domain_items, futures, _cached_instruments, force_refresh=False
            ),
            timeout=12.0,
        )
        return {
            "items": [item.as_dict() for item in enriched],
            "audits": {
                item.symbol: item.audit.as_dict()
                for item in enriched
                if item.audit is not None
            },
            "total": len(enriched),
        }
    except Exception as exc:
        fallback_items: list[WatchlistItem] = []
        audits_map: dict[str, Any] = {}
        for item in domain_items:
            aud = _watchlist_service.audit_item(item, None, [], [], None)
            audits_map[item.symbol] = aud.as_dict()
            fallback_items.append(replace(item, audit=aud))
        return {
            "items": [item.as_dict() for item in fallback_items],
            "audits": audits_map,
            "total": len(fallback_items),
            "warning": f"{type(exc).__name__}: {exc}",
        }


@portal_router.post("/watchlist")
async def add_to_watchlist(
    payload: AddWatchlistRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Operator-only route to add or update a token in the watchlist."""
    _require_operator_auth(authorization)
    win_rate, _ = calculate_opportunity_win_rate_and_ev(
        setup_label=payload.stage,
        score=Decimal(str(payload.score)),
        checklist_passed=payload.checklist_passed,
        checklist_total=payload.checklist_total,
        risk_reward_ratio=Decimal("3.0"),
    )
    item = WatchlistItem(
        symbol=payload.symbol.upper(),
        name=payload.name or payload.symbol.upper(),
        sector=payload.sector,
        stage=WyckoffStage(payload.stage)
        if payload.stage in [s.value for s in WyckoffStage]
        else WyckoffStage.COIL_IMMINENT,
        timeframe=payload.timeframe,
        current_price=Decimal(str(payload.current_price)),
        trigger_price=Decimal(str(payload.trigger_price)),
        support_price=Decimal(str(payload.support_price)),
        invalidation_price=Decimal(str(payload.invalidation_price)),
        target_1=Decimal(str(payload.target_1)),
        target_2=Decimal(str(payload.target_2)),
        distance_to_trigger_pct=Decimal(str(payload.distance_to_trigger_pct)),
        score=Decimal(str(payload.score)),
        thesis=payload.thesis,
        checklist_passed=payload.checklist_passed,
        checklist_total=payload.checklist_total,
        priority=WatchlistPriority(payload.priority)
        if payload.priority in [p.value for p in WatchlistPriority]
        else WatchlistPriority.HIGH,
        notes=payload.notes,
        estimated_win_rate_pct=win_rate,
    )
    saved = _watchlist_service.add_or_update(item)
    return {"status": "success", "item": saved.as_dict()}


@portal_router.delete("/watchlist/{symbol}")
async def delete_from_watchlist(
    symbol: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Operator-only route to delete a token from the watchlist."""
    _require_operator_auth(authorization)
    success = _watchlist_service.delete_item(symbol)
    if not success:
        raise HTTPException(status_code=404, detail="Token not in watchlist")
    return {"status": "success", "deleted": symbol.upper()}


@portal_router.patch("/watchlist/{symbol}/notes")
async def update_watchlist_notes(
    symbol: str,
    payload: UpdateNotesRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Operator-only route to update custom notes/thesis for a watchlist token."""
    _require_operator_auth(authorization)
    success = _watchlist_service.update_notes(symbol, payload.notes)
    if not success:
        raise HTTPException(status_code=404, detail="Token not in watchlist")
    return {"status": "success", "symbol": symbol.upper(), "notes": payload.notes}


_macro_service: MacroIntelligenceService | None = None


def get_macro_service() -> MacroIntelligenceService:
    global _macro_service
    if _macro_service is None:
        from algotrader.application.macro_service import MacroIntelligenceService

        _macro_service = MacroIntelligenceService()
    return _macro_service


@portal_router.get("/macro")
async def get_macro_context(refresh: bool = False) -> dict[str, Any]:
    """Returns the comprehensive macro sentiment and market overextension intelligence snapshot."""
    service = get_macro_service()
    report = await service.get_overextension_report(force_refresh=refresh)
    data = report.as_dict()
    # Backwards-compatibility fields for baseline cards
    data["fear_greed_score"] = report.liquidity_divergence.fear_greed_score
    data["fear_greed_classification"] = report.liquidity_divergence.fear_greed_classification
    data["btc_dominance"] = report.liquidity_divergence.btc_dominance
    data["stablecoin_net_flow_7d"] = report.liquidity_divergence.stablecoin_net_flow_7d
    data["stablecoin_total_usd"] = report.liquidity_divergence.stablecoin_total_usd
    data["narrative"] = (
        f"F&G: {report.liquidity_divergence.fear_greed_score}/100 ({report.liquidity_divergence.fear_greed_classification}) | BTC.D: {report.liquidity_divergence.btc_dominance}% | Funding APR: {report.derivatives_heat.annualized_apr:+.1f}%"
    )
    return data


@portal_router.get("/macro/overextension")
async def get_macro_overextension(refresh: bool = False) -> dict[str, Any]:
    """Returns deep quantitative analytics on market overextension, exhaustion, and cool-off risk."""
    service = get_macro_service()
    report = await service.get_overextension_report(force_refresh=refresh)
    return report.as_dict()


# ------------------------------------------------------------------------------
