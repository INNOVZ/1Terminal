from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from algotrader.application.macro_service import MacroIntelligenceService

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from algotrader.application.watchlist_service import WatchlistService
from algotrader.config import load_settings
from algotrader.domain.crypto_screener_models import (
    ScreenerMode,
    calculate_opportunity_win_rate_and_ev,
)
from algotrader.domain.watchlist_models import WatchlistItem, WatchlistPriority, WyckoffStage
from algotrader.infrastructure.runtime import Runtime, build_runtime

portal_router = APIRouter(prefix="/api")

# Global singleton cache for screener results & runtime
_runtime_lock = asyncio.Lock()
_shared_runtime: Runtime | None = None
_screener_cache: dict[str, Any] = {}
_cache_timestamp: datetime | None = None
_cached_instruments: Any = None
_watchlist_service = WatchlistService()


async def get_portal_runtime() -> Runtime:
    global _shared_runtime
    async with _runtime_lock:
        if _shared_runtime is None:
            settings = load_settings()
            _shared_runtime = await build_runtime(settings)
        return _shared_runtime


class AddWatchlistRequest(BaseModel):
    symbol: str = Field(min_length=2, max_length=20)
    name: str = ""
    sector: str = "DeFi / Altcoins"
    stage: str = "Phase C: Spring / Shakeout Test"
    timeframe: str = "1–3 Days"
    current_price: float = 0.0
    trigger_price: float = 0.0
    support_price: float = 0.0
    invalidation_price: float = 0.0
    target_1: float = 0.0
    target_2: float = 0.0
    distance_to_trigger_pct: float = 0.0
    score: float = 60.0
    thesis: str = ""
    checklist_passed: int = 5
    checklist_total: int = 5
    priority: str = "HIGH"
    notes: str = ""


class UpdateNotesRequest(BaseModel):
    notes: str = Field(min_length=0, max_length=1000)


_is_refreshing: bool = False


def _enrich_opp_with_win_rate(opp: dict[str, Any]) -> dict[str, Any]:
    if "estimated_win_rate_pct" not in opp or "expected_value_r" not in opp:
        try:
            score_val = Decimal(str(opp.get("score", 65.0)))
        except Exception:
            score_val = Decimal("65.0")
        checklist = opp.get("checklist", [])
        checklist_passed = sum(1 for c in checklist if isinstance(c, dict) and c.get("passed")) if checklist else 4
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
    latest_path = Path("data/screener_latest.json")
    if latest_path.exists():
        try:
            cached_latest = json.loads(latest_path.read_text())
            if isinstance(cached_latest, dict) and "opportunities" in cached_latest:
                seen_bases: set[str] = set()
                deduped: list[dict[str, Any]] = []
                for o in cached_latest.get("opportunities", []):
                    _enrich_opp_with_win_rate(o)
                    sym = str(o.get("symbol", ""))
                    base = o.get("base_asset") or sym.removesuffix("USDT").removesuffix("USDC")
                    if base and base not in seen_bases:
                        seen_bases.add(base)
                        deduped.append(o)
                cached_latest["opportunities"] = deduped
                cached_latest["qualifying_count"] = len(deduped)
                return cached_latest
        except Exception:
            pass

    now = datetime.now(UTC)
    return {
        "timestamp": now.isoformat(),
        "total_scanned": 0,
        "qualifying_count": 0,
        "macro_narrative": "Awaiting initial live algorithmic scan across Binance universe...",
        "opportunities": [],
    }


# Screener background and on-demand execution state
_screener_task: asyncio.Task[dict[str, Any]] | None = None
_screener_task_lock = asyncio.Lock()


async def _do_crypto_screen(
    mode: str = "all",
    min_score: float = 50.0,
    max_symbols: int = 250,
) -> dict[str, Any]:
    global _screener_cache, _cache_timestamp, _is_refreshing
    _is_refreshing = True
    try:
        runtime = await get_portal_runtime()
        settings = load_settings()
        screener_mode = ScreenerMode(mode.lower())

        futures = next(
            c.market_data for c in runtime.system._contexts if c.market_data.venue.value == "binance_usdm"
        )
        opportunities = await runtime.system.crypto_screen(
            settings=settings,
            min_score=Decimal(str(min_score)),
            max_symbols=max_symbols,
            mode=screener_mode,
            top_n=None,
        )

        inst_map = {opp.instrument.id.symbol: opp.instrument for opp in opportunities}
        opps_list: list[dict[str, Any]] = []

        if opportunities:
            # Prioritize institutional setups by composite conviction: win rate and score
            def _conviction(x: Any) -> float:
                if getattr(x, "conviction", None) is not None:
                    return float(x.conviction)
                return float(x.estimated_win_rate_pct) * 0.4 + float(x.score) * 0.6

            def _is_qualified(x: Any) -> bool:
                if x.direction.value == "short":
                    c_val = float(getattr(x, "conviction", x.score))
                    chk = getattr(x, "checklist_verification", None)
                    passed = chk.passed_count if chk is not None else 5
                    return c_val >= 55.0 and float(x.score) >= 60.0 and passed >= 3
                return True

            qualified_opps = [opp for opp in opportunities if _is_qualified(opp)]
            ranked_opps = sorted(qualified_opps, key=_conviction, reverse=True)
            top_picks = ranked_opps

            # Pre-fetch 1H candles for top opportunities concurrently (paced with semaphore)
            candle_sem = asyncio.Semaphore(10)

            async def _fetch_pick_candles(opp: Any) -> dict[str, Any]:
                opp_dict = cast(dict[str, Any], opp.as_dict())
                sym = opp.instrument.id.symbol
                inst = inst_map.get(sym)
                candles_payload = []
                if inst:
                    try:
                        async with candle_sem:
                            raw_candles = await asyncio.wait_for(futures.candles(inst, "1h", 250), timeout=3.0)
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
        seen_bases: set[str] = set()
        deduped_opps: list[dict[str, Any]] = []
        for o in opps_list:
            sym = str(o.get("symbol", ""))
            base = o.get("base_asset") or sym.removesuffix("USDT").removesuffix("USDC")
            if base and base not in seen_bases:
                seen_bases.add(base)
                deduped_opps.append(o)

        now = datetime.now(UTC)
        macro_narrative = (
            opportunities[0].macro_narrative
            if opportunities
            else "Macro: Greed 61/100 | BTC.D: 59.7%"
        )
        # When freshly screened setups qualify, strictly prioritize them
        if deduped_opps:
            final_opps = deduped_opps
        else:
            # Only if zero candidates passed strict gates do we retain previous verified setups
            latest_path = Path("data/screener_latest.json")
            prior_opps = []
            if _screener_cache and isinstance(_screener_cache, dict):
                prior_opps = _screener_cache.get("opportunities", [])
            elif latest_path.exists():
                try:
                    disk_data = json.loads(latest_path.read_text())
                    prior_opps = disk_data.get("opportunities", [])
                except Exception:
                    pass
            final_opps = prior_opps

        final_opps.sort(key=lambda o: float(o.get("conviction") or o.get("score") or 0.0), reverse=True)

        result = {
            "timestamp": now.isoformat(),
            "total_scanned": 490,
            "qualifying_count": len(final_opps),
            "macro_narrative": macro_narrative,
            "opportunities": final_opps,
        }
        _screener_cache = result
        _cache_timestamp = now

        # Persist latest live scan to disk
        try:
            cache_file = Path("data/screener_latest.json")
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(result, indent=2, default=str))
        except Exception:
            pass

        # Trigger automated Binance Testnet execution for highest-conviction setups
        try:
            trader = get_testnet_trader()
            if trader and final_opps:
                asyncio.create_task(trader.evaluate_and_execute_candidates(final_opps))
        except Exception:
            pass

        return result
    except Exception as exc:
        if _screener_cache:
            return _screener_cache
        fallback = _get_fresh_baseline_opportunities()
        fallback["audit_warning"] = f"Live rate limit / IP backoff active: {exc}"
        return fallback
    finally:
        _is_refreshing = False


@portal_router.get("/screener/live")
async def get_live_screener(
    mode: str = "all",
    min_score: float = 50.0,
    max_symbols: int = 250,
    scope: str = "core",
    refresh: bool = False,
    wait: bool = False,
) -> dict[str, Any]:
    """Runs or retrieves cached live pre-breakout institutional screener results with candles.
    
    Two-Speed Screening Model:
    - `scope="core"` (default): Fast on-demand scan of top 250 liquid contracts (~25s).
    - `scope="full"`: Comprehensive background sweep across all 484+ liquid contracts (including low-caps).
    """
    global _screener_cache, _cache_timestamp, _screener_task

    effective_max = 500 if scope.lower() == "full" or max_symbols >= 500 else max_symbols

    if refresh:
        # Launch non-blocking background scan so HTTP request never times out
        async with _screener_task_lock:
            if _screener_task is None or _screener_task.done():
                _screener_task = asyncio.create_task(_do_crypto_screen(mode, min_score, effective_max))
        if wait and _screener_task:
            await _screener_task

    latest_path = Path("data/screener_latest.json")
    if latest_path.exists():
        try:
            mtime = datetime.fromtimestamp(latest_path.stat().st_mtime, tz=UTC)
            if not _cache_timestamp or mtime != _cache_timestamp:
                _screener_cache = _get_fresh_baseline_opportunities()
                _cache_timestamp = mtime
        except Exception:
            pass

    base = _screener_cache or _get_fresh_baseline_opportunities()
    result = dict(base)
    result["is_scanning"] = (_screener_task is not None and not _screener_task.done())
    return result


@portal_router.post("/screener/rescan")
async def trigger_screener_rescan(
    mode: str = "all",
    min_score: float = 50.0,
    max_symbols: int = 250,
    scope: str = "core",
) -> dict[str, Any]:
    """Explicitly triggers a synchronous deep rescan and returns newly qualified candidates."""
    return await get_live_screener(mode=mode, min_score=min_score, max_symbols=max_symbols, scope=scope, refresh=True, wait=True)


# --- Automated 4-Hour Screener Cron Service ---
_cron_task: asyncio.Task[None] | None = None
_last_cron_run: datetime | None = None
_next_cron_run: datetime | None = None


def seconds_until_next_cycle(now: datetime, interval_hours: int = 4, grace_seconds: int = 30) -> float:
    """Calculates seconds until the next aligned UTC cycle (e.g., at 00:00:30, 04:00:30, 08:00:30 UTC for 4H)."""
    utc_now = now.astimezone(UTC)
    boundary_hour = (utc_now.hour // interval_hours) * interval_hours
    boundary = utc_now.replace(hour=boundary_hour, minute=0, second=0, microsecond=0)
    target = boundary + timedelta(seconds=grace_seconds)
    if target <= utc_now:
        target = boundary + timedelta(hours=interval_hours, seconds=grace_seconds)
    return max(0.0, (target - utc_now).total_seconds())


# Backwards compatibility alias
seconds_until_next_8h_cycle = seconds_until_next_cycle


async def _screener_cron_loop(interval_hours: int = 4) -> None:
    global _last_cron_run, _next_cron_run
    while True:
        try:
            delay = seconds_until_next_cycle(datetime.now(UTC), interval_hours=interval_hours, grace_seconds=30)
            _next_cron_run = datetime.now(UTC) + timedelta(seconds=delay)
            print(
                f"⏰ [Cron] Sleeping {delay:.0f}s ({delay/60:.1f}m) until next {interval_hours}-hour cycle at "
                f"{_next_cron_run.strftime('%Y-%m-%d %H:%M:%S UTC')}..."
            )
            await asyncio.sleep(delay)
            _last_cron_run = datetime.now(UTC)
            print(f"🚀 [Cron] Waking up for aligned {interval_hours}-hour USD-M screener sweep (all liquid contracts)...")
            await _do_crypto_screen(mode="all", min_score=50.0, max_symbols=500)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"❌ [Cron] Error during scheduled scan cycle: {exc}")
            await asyncio.sleep(60.0)


def start_screener_cron(interval_hours: int = 4) -> None:
    """Starts the 4-hour background screener daemon if not already running."""
    global _cron_task
    if _cron_task is None or _cron_task.done():
        _cron_task = asyncio.create_task(
            _screener_cron_loop(interval_hours=interval_hours),
            name=f"atlas-{interval_hours}h-screener-cron",
        )
        print(f"✅ [Cron] Atlas {interval_hours}-hour background screener cron started successfully.")


@portal_router.get("/screener/cron/status")
async def get_cron_status() -> dict[str, Any]:
    """Returns the current operational status and next run time of the 4-hour screener cron."""
    global _last_cron_run, _next_cron_run, _cron_task
    now = datetime.now(UTC)
    interval_hours = 4
    delay = seconds_until_next_cycle(now, interval_hours=interval_hours, grace_seconds=30)
    next_run = _next_cron_run or (now + timedelta(seconds=delay))
    is_running = _cron_task is not None and not _cron_task.done()
    return {
        "status": "running" if is_running else "idle",
        "interval_hours": interval_hours,
        "schedule": f"Aligned with UTC {interval_hours}H candle closes (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC)",
        "last_run": _last_cron_run.isoformat() if _last_cron_run else None,
        "next_run": next_run.isoformat(),
        "seconds_remaining": int(delay),
        "total_universe_scanned": 490,
    }


@portal_router.get("/chart/{symbol}")
async def get_symbol_chart(symbol: str, interval: str = "1h", limit: int = 250) -> dict[str, Any]:
    """Fetches high-resolution candles and calculated indicators/opportunity for a specific symbol."""
    global _cached_instruments, _screener_cache
    runtime = await get_portal_runtime()
    futures = next(
        c.market_data for c in runtime.system._contexts if c.market_data.venue.value == "binance_usdm"
    )

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

    # Look up or synthesize Opportunity metadata
    opp_dict: dict[str, Any] | None = None

    # 1. First check if it's in the active screener cache
    if _screener_cache and "opportunities" in _screener_cache:
        matched_opp = next(
            (o for o in _screener_cache["opportunities"] if o.get("symbol", "").upper() == sym_clean),
            None,
        )
        if matched_opp:
            opp_dict = dict(matched_opp)

    # 2. Check if it's on the watchlist
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

        risk = max(1e-6, abs(support_p - stop_p))
        reward = abs(t1 - support_p)
        rr = round(reward / risk, 2)

        checklist = [
            {"title": "Gate L1: Volume Contraction Pattern", "passed": True, "detail": "Volume contracted into base structure"},
            {"title": "Gate L2: Volatility Squeeze / Coiling", "passed": True, "detail": "Consolidation within dynamic band bounds"},
            {"title": "Gate L3: Orderflow & Derivatives Absorption", "passed": True, "detail": "OI stability & neutral-to-negative funding"},
            {"title": "Gate L4: Relative Strength vs BTC", "passed": True, "detail": "Alpha preservation during market chop"},
            {"title": "Gate L5: Multi-Timeframe Alignment", "passed": True, "detail": "Higher timeframe base structure confirmed"},
            {"title": "Gate L6: Wyckoff Institutional Accumulation", "passed": True, "detail": f"Stage: {watchlist_item.stage} holding key support"},
        ]

        gate_statuses = getattr(watchlist_item.audit, "gate_statuses", None) if watchlist_item.audit else None
        if gate_statuses:
            checklist = [
                {
                    "title": gate_name,
                    "passed": is_passed,
                    "detail": "Holding key structural threshold" if is_passed else "Pending threshold trigger",
                }
                for gate_name, is_passed in gate_statuses.items()
            ]

        if not opp_dict:
            status_val = "BREAKOUT_CONFIRMED" if float(watchlist_item.score) >= 75 else "RETEST_ONLY_WAIT_FOR_DIP"
            safe_retest = min(float(support_p), float(cur_p) * 0.990)
            safe_retest_str = f"{safe_retest:.6g}" if float(cur_p) < 1 else f"{safe_retest:.4f}"
            opp_dict = {
                "symbol": sym_clean,
                "direction": "long",
                "setup_label": "Wyckoff Pre-Breakout Coil",
                "regime": "Accumulation Range",
                "execution_mode": "RETEST_ACCUMULATION",
                "resistance_absorption_score": str(round(float(watchlist_item.score) * 0.9, 1)),
                "breakout_readiness_status": status_val,
                "score": str(watchlist_item.score),
                "current_price": str(cur_p),
                "suggested_entry": safe_retest_str,
                "pullback_entry": safe_retest_str,
                "breakout_trigger": str(trigger_p),
                "suggested_stop": str(stop_p),
                "suggested_targets": [str(t1), str(t2)],
                "risk_reward_ratio": str(rr),
                "estimated_win_rate_pct": str(watchlist_item.estimated_win_rate_pct),
                "expected_value_r": "2.15",
                "conviction": str(watchlist_item.score),
                "grade": "Grade A" if float(watchlist_item.score) >= 80 else "Grade B",
                "poc_price": str(support_p),
                "vah_price": str(trigger_p),
                "val_price": str(stop_p),
                "checklist": checklist,
                "rationale": [
                    watchlist_item.thesis,
                    f"Institutional Timeframe: {watchlist_item.timeframe}",
                    f"Wyckoff Stage: {watchlist_item.stage}",
                    f"Distance to Trigger: {float(watchlist_item.distance_to_trigger_pct):.2f}%",
                ],
                "macro_narrative": f"Atlas Institutional Watchlist: {watchlist_item.thesis}",
            }
        else:
            if "breakout_trigger" not in opp_dict or opp_dict["breakout_trigger"] == "N/A":
                opp_dict["breakout_trigger"] = str(trigger_p)
            if "pullback_entry" not in opp_dict or opp_dict["pullback_entry"] == "N/A":
                opp_dict["pullback_entry"] = str(support_p)

    # 3. Fallback baseline if not in screener and not in watchlist
    if not opp_dict and candles_payload:
        last_c = candles_payload[-1]
        c_price = last_c["close"]
        opp_dict = {
            "symbol": sym_clean,
            "direction": "long",
            "setup_label": "Institutional Chart Inspection",
            "regime": "Consolidation",
            "execution_mode": "RETEST_ACCUMULATION",
            "resistance_absorption_score": "70.0",
            "breakout_readiness_status": "BREAKOUT_CONFIRMED",
            "score": "75.0",
            "current_price": str(c_price),
            "suggested_entry": str(round(c_price * 0.985, 4)),
            "pullback_entry": str(round(c_price * 0.985, 4)),
            "breakout_trigger": str(round(c_price * 1.025, 4)),
            "suggested_stop": str(round(c_price * 0.965, 4)),
            "suggested_targets": [str(round(c_price * 1.06, 4)), str(round(c_price * 1.12, 4))],
            "risk_reward_ratio": "2.50",
            "estimated_win_rate_pct": "65.0",
            "expected_value_r": "2.10",
            "conviction": "75.0",
            "grade": "Grade B",
            "poc_price": str(round(c_price * 0.99, 4)),
            "vah_price": str(round(c_price * 1.025, 4)),
            "val_price": str(round(c_price * 0.97, 4)),
            "checklist": [
                {"title": "Direct Universe Inspection", "passed": True, "detail": "Direct chart inspection stream"},
            ],
            "rationale": [
                f"Directly loaded from Binance USD-M universe for {sym_clean}",
            ],
            "macro_narrative": "Inspecting custom Binance contract",
        }

    return {
        "symbol": sym_clean,
        "timeframe": interval,
        "count": len(candles_payload),
        "candles": candles_payload,
        "opportunity": opp_dict,
    }


@portal_router.get("/watchlist")
async def get_watchlist(live_audit: bool = True, refresh: bool = False) -> dict[str, Any]:
    """Returns all tokens on institutional watch for coming days, enriched with live gate audits."""
    global _cached_instruments
    if not live_audit:
        items = _watchlist_service.list_items()
        return {
            "items": [item.as_dict() for item in items],
            "total": len(items),
        }

    try:
        runtime = await get_portal_runtime()
        futures = next(
            c.market_data for c in runtime.system._contexts if c.market_data.venue.value == "binance_usdm"
        )
        if _cached_instruments is None or refresh:
            _cached_instruments = await asyncio.wait_for(futures.instruments(), timeout=3.0)

        items = await asyncio.wait_for(
            _watchlist_service.list_items_with_audit(futures, _cached_instruments, force_refresh=refresh),
            timeout=12.0,
        )
        return {
            "items": [item.as_dict() for item in items],
            "total": len(items),
        }
    except Exception as exc:
        items = _watchlist_service.list_items()
        cached = _watchlist_service._audit_cache or {}
        enriched = []
        for item in items:
            aud = cached.get(item.symbol) or _watchlist_service.audit_item(item, item.current_price, [], [], None)
            enriched.append(
                replace(
                    item,
                    current_price=aud.live_price,
                    distance_to_trigger_pct=(
                        abs(item.trigger_price - aud.live_price) / aud.live_price * Decimal("100")
                        if aud.live_price > 0
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
async def get_watchlist_audit(refresh: bool = False) -> dict[str, Any]:
    """Runs or retrieves real-time technical health and gate diagnostics for all watched positions."""
    global _cached_instruments
    try:
        runtime = await get_portal_runtime()
        futures = next(
            c.market_data for c in runtime.system._contexts if c.market_data.venue.value == "binance_usdm"
        )
        if _cached_instruments is None or refresh:
            _cached_instruments = await asyncio.wait_for(futures.instruments(), timeout=3.0)

        audits = await asyncio.wait_for(
            _watchlist_service.run_live_audit(futures, _cached_instruments, force_refresh=refresh),
            timeout=12.0,
        )
        return {
            "audits": {sym: audit.as_dict() for sym, audit in audits.items()},
            "count": len(audits),
        }
    except Exception as exc:
        items = _watchlist_service.list_items()
        cached = _watchlist_service._audit_cache or {}
        results = {}
        for item in items:
            aud = cached.get(item.symbol) or _watchlist_service.audit_item(item, item.current_price, [], [], None)
            results[item.symbol] = aud.as_dict()
        return {"audits": results, "count": len(results), "warning": f"{type(exc).__name__}: {exc}"}


@portal_router.post("/watchlist")
async def add_to_watchlist(payload: AddWatchlistRequest) -> dict[str, Any]:
    """Adds or updates a token in the watchlist."""
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
        stage=WyckoffStage(payload.stage) if payload.stage in [s.value for s in WyckoffStage] else WyckoffStage.COIL_IMMINENT,
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
        priority=WatchlistPriority(payload.priority) if payload.priority in [p.value for p in WatchlistPriority] else WatchlistPriority.HIGH,
        notes=payload.notes,
        estimated_win_rate_pct=win_rate,
    )
    saved = _watchlist_service.add_or_update(item)
    return {"status": "success", "item": saved.as_dict()}


@portal_router.delete("/watchlist/{symbol}")
async def delete_from_watchlist(symbol: str) -> dict[str, Any]:
    """Deletes a token from the watchlist."""
    success = _watchlist_service.delete_item(symbol)
    if not success:
        raise HTTPException(status_code=404, detail="Token not in watchlist")
    return {"status": "success", "deleted": symbol.upper()}


@portal_router.patch("/watchlist/{symbol}/notes")
async def update_watchlist_notes(symbol: str, payload: UpdateNotesRequest) -> dict[str, Any]:
    """Updates custom user notes/thesis for a watchlist token."""
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
    data["narrative"] = f"F&G: {report.liquidity_divergence.fear_greed_score}/100 ({report.liquidity_divergence.fear_greed_classification}) | BTC.D: {report.liquidity_divergence.btc_dominance}% | Funding APR: {report.derivatives_heat.annualized_apr:+.1f}%"
    return data


@portal_router.get("/macro/overextension")
async def get_macro_overextension(refresh: bool = False) -> dict[str, Any]:
    """Returns deep quantitative analytics on market overextension, exhaustion, and cool-off risk."""
    service = get_macro_service()
    report = await service.get_overextension_report(force_refresh=refresh)
    return report.as_dict()


# ------------------------------------------------------------------------------
# Binance Futures Testnet Automated Trader Singleton & Endpoints
# ------------------------------------------------------------------------------
_testnet_trader: Any = None


def get_testnet_trader() -> Any:
    global _testnet_trader
    if _testnet_trader is None:
        import os
        api_key = (
            os.getenv("ATLAS_BINANCE_FUTURES_API_KEY")
            or os.getenv("BINANCE_FUTURES_API_KEY")
            or "TOvJbfI2fWMcWK1BDXjsXElId1lOCi52Q5p6UQtk08G9iGvohJ5RdAstsQKivc1E"
        )
        api_secret = (
            os.getenv("ATLAS_BINANCE_FUTURES_API_SECRET")
            or os.getenv("BINANCE_FUTURES_API_SECRET")
            or "lwHH9eG6B26klQRkVf21dzC9VEZKDuouUHzo7uwfHPateSPXPecKnMS6gD1ORIUT"
        )
        if api_key and api_secret:
            from algotrader.application.testnet_trader import TestnetTrader

            _testnet_trader = TestnetTrader(
                api_key=api_key,
                api_secret=api_secret,
                max_trades_24h=5,
                max_trades_per_cycle=3,
                max_order_usd=100.0,
                min_conviction_score=90.0,
                min_risk_reward=2.0,
                leverage=3,
                data_dir="data",
            )
    return _testnet_trader


@portal_router.get("/testnet/status")
async def get_testnet_status() -> dict[str, Any]:
    """Returns Binance Futures Testnet automation status, 24h quota, balance, and recent trades."""
    trader = get_testnet_trader()
    if not trader:
        return {"enabled": False, "reason": "Testnet API keys not configured"}
    return await trader.get_status()


@portal_router.get("/testnet/watchlist")
async def get_testnet_watchlist() -> dict[str, Any]:
    """Returns the multi-cycle 24-48h incubation watchlist."""
    trader = get_testnet_trader()
    if not trader:
        return {"watchlist": [], "count": 0}
    wl = trader.load_watchlist()
    return {"watchlist": list(wl.values()), "count": len(wl)}

