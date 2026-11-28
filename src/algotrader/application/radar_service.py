"""Bounded public-data refresh for radar lifecycle decisions.

The domain owns transitions; this service owns provider timeouts, data freshness and
unknown-data retention. It neither submits orders nor enrolls research sessions.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from algotrader.domain.bearish_continuation import (
    DEFAULT_POLICY,
    POLICY_VERSION,
    ContinuationPlan,
    evaluate_continuation,
)
from algotrader.domain.bearish_continuation import (
    TITLES as CONTINUATION_TITLES,
)
from algotrader.domain.early_recovery import (
    DEFAULT_EARLY_RECOVERY_POLICY,
    EarlyRecoveryPlan,
    evaluate_early_recovery,
)
from algotrader.domain.early_recovery import (
    POLICY_VERSION as EARLY_RECOVERY_POLICY_VERSION,
)
from algotrader.domain.early_recovery import (
    TITLES as RECOVERY_TITLES,
)
from algotrader.domain.indicators import bollinger_bands, ema, volume_contraction_metrics
from algotrader.domain.models import Candle, Quote
from algotrader.domain.radar_insights import radar_quote_snapshot
from algotrader.domain.radar_lifecycle import evaluate_radar_candidate_lifecycle


async def reconcile_radar_cohort(
    prior_opportunities: list[dict[str, Any]],
    seen_bases: set[str],
    futures: Any,
    now: datetime | None = None,
    events: list[dict[str, Any]] | None = None,
    bearish_continuation_enabled: bool = False,
    early_recovery_enabled: bool = False,
    additional_instruments: Sequence[Any] = (),
    quote_sink: dict[str, Quote] | None = None,
    preloaded_quotes: dict[str, Quote] | None = None,
) -> list[dict[str, Any]]:
    """Re-audits prior radar setups against live 1H candles and retains active trades until stop or target."""
    ref_now = now or datetime.now(UTC)

    def unavailable(prev: dict[str, Any], reason: str) -> dict[str, Any]:
        if events is not None:
            events.append(
                {
                    "symbol": prev.get("symbol"),
                    "at": ref_now.isoformat(),
                    "state": prev.get("radar_status"),
                    "retained": True,
                    "reason": reason,
                    "data_status": "unavailable",
                    "first_seen_at": prev.get("first_seen_at") or prev.get("evaluated_at"),
                }
            )
        updated = dict(prev)
        updated.setdefault("discovery_checklist", prev.get("checklist", []))
        updated.update(
            radar_data_status="unavailable",
            radar_data_reason=reason,
            gates_passed=0,
            gates_total=5,
            failed_gates=[],
        )
        updated["radar_origin"] = "retained"
        updated["radar_status_label"] = "DATA UNAVAILABLE"
        setup_lbl = prev.get("setup_label")
        gate_titles: tuple[str, ...] = (
            RECOVERY_TITLES
            if setup_lbl == "RECOVERY_RECLAIM_LONG"
            else CONTINUATION_TITLES
            if setup_lbl == "BREAKDOWN_RETEST_SHORT"
            else (
                "Trend Baseline",
                "Anti-Chasing Validated",
                "Value Acceptance",
                "Volatility Compression",
                "Smart Money Absorption",
            )
        )
        updated["checklist"] = [
            {
                "title": title,
                "passed": False,
                "status": "unknown",
                "detail": "Unavailable: fresh market data required",
            }
            for title in gate_titles
        ]
        return updated

    if not prior_opportunities and not additional_instruments:
        return []
    inst_by_symbol = {}
    quotes_by_symbol = {}
    try:
        all_insts = (await asyncio.wait_for(futures.instruments(), timeout=6.0)
                     if prior_opportunities else additional_instruments)
        inst_by_symbol = {inst.id.symbol: inst for inst in all_insts}
        wanted = [
            inst_by_symbol[str(p.get("symbol"))]
            for p in prior_opportunities
            if str(p.get("symbol")) in inst_by_symbol
        ]
        wanted = list({inst.id.symbol: inst for inst in [*wanted, *additional_instruments]}.values())
        if wanted:
            fresh_preloaded: dict[str, Quote] = {}
            if preloaded_quotes:
                for inst_w in wanted:
                    pq = preloaded_quotes.get(inst_w.id.symbol)
                    if (
                        pq is not None
                        and pq.is_valid_book
                        and pq.is_fresh_at(ref_now, max_age_seconds=55.0, max_future_seconds=5.0)
                    ):
                        fresh_preloaded[inst_w.id.symbol] = pq
            missing_wanted = [i for i in wanted if i.id.symbol not in fresh_preloaded]
            quotes_by_symbol.update(fresh_preloaded)
            if missing_wanted:
                quotes = await asyncio.wait_for(futures.quotes(missing_wanted), timeout=6.0)
                quotes_by_symbol.update({q.instrument_id.symbol: q for q in quotes})
            if quote_sink is not None:
                quote_sink.update(quotes_by_symbol)
    except Exception:
        pass  # Retain uncertainty explicitly; a provider error is not invalidation.

    btc_candles_1h: Sequence[Candle] = ()
    if early_recovery_enabled and any(
        p.get("setup_label") == "RECOVERY_RECLAIM_LONG" for p in prior_opportunities
    ):
        btc_inst = inst_by_symbol.get("BTCUSDT") or next(
            (i for i in inst_by_symbol.values() if getattr(i, "base_asset", "") == "BTC"),
            None,
        )
        if btc_inst is not None:
            with contextlib.suppress(Exception):
                btc_candles_1h = await asyncio.wait_for(
                    futures.candles(btc_inst, "1h", 240), timeout=6.0
                )

    sem = asyncio.Semaphore(8)
    retained: list[dict[str, Any]] = []

    async def _check_single(prev: dict[str, Any]) -> dict[str, Any] | None:
        sym = str(prev.get("symbol", ""))
        base = prev.get("base_asset") or sym.removesuffix("USDT").removesuffix("USDC")
        if not sym or not base or base in seen_bases:
            return None
        inst = inst_by_symbol.get(sym)
        if inst is None:
            return unavailable(prev, "instrument_lookup_unavailable")
        # A fresh quote can establish a hard stop even if candle retrieval fails.
        # Missing historical coverage must not conceal a currently breached stop.
        quote = quotes_by_symbol.get(sym)
        quote_now = now or datetime.now(UTC)
        if (
            quote is not None
            and quote.is_valid_book
            and quote.is_fresh_at(quote_now, max_age_seconds=60.0, max_future_seconds=5.0)
        ):
            immediate = evaluate_radar_candidate_lifecycle(
                prev, current_price=quote.last, now=quote_now
            )
            if immediate.reason in ("long_stop_loss_breached", "short_stop_loss_breached"):
                if events is not None:
                    events.append(
                        {
                            "symbol": sym,
                            "at": quote_now.isoformat(),
                            "state": immediate.state.value,
                            "retained": False,
                            "reason": immediate.reason,
                            "observed_price": str(quote.last),
                            "candidate": {k: v for k, v in prev.items() if k != "candles"},
                        }
                    )
                return None
        try:
            async with sem:
                raw_1h = await asyncio.wait_for(futures.candles(inst, "1h", 338), timeout=6.0)
                raw_4h: Sequence[Candle] = ()
                with contextlib.suppress(Exception):
                    raw_4h = await asyncio.wait_for(futures.candles(inst, "4h", 200), timeout=6.0)
                deriv_snap = None
                if hasattr(futures, "derivatives_snapshot"):
                    with contextlib.suppress(Exception):
                        deriv_snap = await asyncio.wait_for(
                            futures.derivatives_snapshot(inst), timeout=6.0
                        )
            checked_at = now or datetime.now(UTC)
            closed_1h = sorted(
                [c for c in raw_1h if c.closed and c.closed_at <= checked_at],
                key=lambda c: c.closed_at,
            )
            quote = quotes_by_symbol.get(sym)
            if (
                not closed_1h
                or quote is None
                or not quote.is_valid_book
                or not quote.is_fresh_at(checked_at, max_age_seconds=60.0, max_future_seconds=5.0)
                or not 0 <= (checked_at - closed_1h[-1].closed_at).total_seconds() <= 3900
            ):
                return unavailable(prev, "fresh_quote_or_candles_unavailable")
            live_price = quote.last
            candles_payload = [
                {
                    "time": int(c.closed_at.timestamp()),
                    "opened_time": int(c.opened_at.timestamp()),
                    "open": float(c.open),
                    "high": float(c.high),
                    "low": float(c.low),
                    "close": float(c.close),
                    "volume": float(c.volume),
                }
                for c in closed_1h
            ]
            updated = dict(prev)
            updated.setdefault("discovery_checklist", prev.get("checklist", []))
            updated.setdefault("discovery_price", prev.get("discovery_price") or prev.get("current_price"))
            updated["radar_origin"] = "retained"
            updated["radar_gate_policy"] = "retention-health-v3"
            updated["first_seen_at"] = prev.get("first_seen_at") or prev.get("evaluated_at")
            updated["radar_metrics"] = {}
            metrics = updated["radar_metrics"]

            # Require complete windows rather than manufacturing a long-window value.
            def continuous(candles: Any, seconds: int) -> bool:
                return all(
                    abs((b.opened_at - a.opened_at).total_seconds() - seconds) < 2
                    for a, b in zip(candles, candles[1:], strict=False)
                )

            coverage_start_raw = (
                prev.get("radar_checked_at")
                or prev.get("first_seen_at")
                or prev.get("evaluated_at")
            )
            if coverage_start_raw:
                coverage_start = datetime.fromisoformat(coverage_start_raw)
                if closed_1h[0].opened_at > coverage_start:
                    return unavailable(prev, "post_discovery_history_truncated")
            if not continuous(closed_1h, 3600):
                return unavailable(prev, "candle_history_gap")
            if len(closed_1h) >= 336:
                metrics["volume_contraction_ratio"] = str(volume_contraction_metrics(closed_1h)[2])
            closed_4h = sorted(
                [c for c in raw_4h if c.closed and c.closed_at <= checked_at],
                key=lambda c: c.closed_at,
            )
            ema50 = None
            if (
                len(closed_4h) >= 50
                and continuous(closed_4h, 14400)
                and (checked_at - closed_4h[-1].closed_at).total_seconds() <= 14700
            ):
                closes = [c.close for c in closed_4h]
                ema50 = ema(closes, 50)[-1]
                metrics["bb_bandwidth_4h"] = str(bollinger_bands(closes)[3][-1])
            deriv_time = getattr(deriv_snap, "observed_at", None)
            if (
                getattr(deriv_snap, "degraded_telemetry", False)
                or deriv_time is None
                or not -5 <= (checked_at - deriv_time).total_seconds() <= 300
            ):
                deriv_snap = None
            if deriv_snap is not None:
                if getattr(deriv_snap, "open_interest_change_7d", None) is not None:
                    metrics["oi_change_7d_pct"] = (
                        f"{float(deriv_snap.open_interest_change_7d * Decimal('100')):+.2f}%"
                    )
                if getattr(deriv_snap, "open_interest_change_4h", None) is not None:
                    metrics["oi_change_4h_pct"] = (
                        f"{float(deriv_snap.open_interest_change_4h * Decimal('100')):+.2f}%"
                    )
                if getattr(deriv_snap, "open_interest_change_1h", None) is not None:
                    metrics["oi_change_1h_pct"] = (
                        f"{float(deriv_snap.open_interest_change_1h * Decimal('100')):+.2f}%"
                    )
                if getattr(deriv_snap, "funding_rate", None) is not None:
                    metrics["funding_rate_bps"] = (
                        f"{float(deriv_snap.funding_rate * Decimal('10000')):+.2f} bps"
                    )
                taker_1h = getattr(deriv_snap, "taker_buy_sell_ratio", None)
                taker_4h = getattr(deriv_snap, "taker_buy_sell_ratio_4h", None)
                is_short_dir = str(prev.get("direction", "long")).lower() == "short"
                if taker_1h is not None and taker_4h is not None:
                    eff_taker = min(taker_1h, taker_4h) if is_short_dir else max(taker_1h, taker_4h)
                    metrics["taker_buy_sell_ratio"] = f"{float(eff_taker):.4f}"
                elif taker_1h is not None:
                    metrics["taker_buy_sell_ratio"] = f"{float(taker_1h):.4f}"
                elif taker_4h is not None:
                    metrics["taker_buy_sell_ratio"] = f"{float(taker_4h):.4f}"
            continuation_gates = None
            is_continuation = prev.get("setup_label") == "BREAKDOWN_RETEST_SHORT"
            continuation_horizon = 4.0
            if is_continuation and bearish_continuation_enabled:
                evidence = prev.get("continuation_evidence") or {}
                if evidence.get("policy_version") == POLICY_VERSION:
                    try:
                        plan = ContinuationPlan.from_dict(evidence["plan"])
                        discovered = datetime.fromisoformat(str(prev.get("first_seen_at") or prev["evaluated_at"]))
                        wait_deadline = plan.rejection_at + timedelta(hours=DEFAULT_POLICY.trigger_wait_hours)
                        continuation_horizon = max(0.0, (wait_deadline - discovered).total_seconds() / 3600)
                        # Reuse the lifecycle's post-discovery candle ordering and locked
                        # trigger basis, including a trigger first observed in this scan.
                        observed = evaluate_radar_candidate_lifecycle(
                            updated, current_price=live_price, recent_candles_1h=candles_payload,
                            ema50_4h=ema50, now=checked_at, max_age_hours=continuation_horizon,
                        )
                        trigger_at = datetime.fromisoformat(observed.triggered_at) if observed.triggered_at else None
                        is_triggered = (
                            observed.retained and trigger_at is not None
                            and plan.rejection_at <= trigger_at <= wait_deadline
                            and observed.trigger_basis in ("breakout", "retest")
                            and observed.trigger_entry_price is not None
                            and observed.trigger_stop_price is not None
                        )
                        current = evaluate_continuation(
                            closed_1h, closed_4h, now=checked_at, price=live_price,
                            tick=inst.price_tick, derivatives=deriv_snap,
                            spread_fraction=(quote.ask - quote.bid) / quote.last,
                            existing_plan=plan, is_triggered=is_triggered,
                        )
                        continuation_gates = current.gates
                        if (not quote.quote_volume_24h.is_finite()
                                or quote.quote_volume_24h < Decimal("500000")):
                            continuation_gates = (*current.gates[:2], False, *current.gates[3:])
                    except (KeyError, TypeError, ValueError, ArithmeticError):
                        continuation_gates = None
            recovery_gates = None
            recovery_details = None
            is_recovery = prev.get("setup_label") == "RECOVERY_RECLAIM_LONG"
            recovery_horizon = 12.0
            if is_recovery and early_recovery_enabled:
                rec_ev = prev.get("recovery_evidence") or {}
                if rec_ev.get("policy_version") == EARLY_RECOVERY_POLICY_VERSION:
                    try:
                        rec_plan = EarlyRecoveryPlan.from_dict(rec_ev["plan"])
                        rec_discovered = datetime.fromisoformat(
                            str(prev.get("first_seen_at") or prev["evaluated_at"])
                        )
                        rec_wait_deadline = rec_plan.break_bar_at + timedelta(
                            hours=DEFAULT_EARLY_RECOVERY_POLICY.trigger_wait_hours
                        )
                        recovery_horizon = max(
                            0.0, (rec_wait_deadline - rec_discovered).total_seconds() / 3600.0
                        )
                        rec_observed = evaluate_radar_candidate_lifecycle(
                            updated,
                            current_price=live_price,
                            recent_candles_1h=candles_payload,
                            ema50_4h=ema50,
                            now=checked_at,
                            max_age_hours=recovery_horizon,
                        )
                        rec_trigger_at = (
                            datetime.fromisoformat(rec_observed.triggered_at)
                            if rec_observed.triggered_at
                            else None
                        )
                        rec_is_triggered = (
                            rec_observed.retained
                            and rec_trigger_at is not None
                            and rec_plan.break_bar_at <= rec_trigger_at <= rec_wait_deadline
                            and rec_observed.trigger_basis in ("breakout", "retest")
                            and rec_observed.trigger_entry_price is not None
                            and rec_observed.trigger_stop_price is not None
                        )
                        current_rec = evaluate_early_recovery(
                            closed_1h,
                            closed_4h,
                            btc_candles_1h,
                            now=checked_at,
                            quote=quote,
                            tick=inst.price_tick,
                            derivatives=deriv_snap,
                            existing_plan=rec_plan,
                            is_triggered=rec_is_triggered,
                            trigger_entry_price=rec_observed.trigger_entry_price,
                        )
                        recovery_gates = current_rec.gates
                        recovery_details = current_rec.details
                        metrics.update(current_rec.metrics)
                    except (KeyError, TypeError, ValueError, ArithmeticError):
                        recovery_gates = None
                        recovery_details = None
            decision = evaluate_radar_candidate_lifecycle(
                updated,
                current_price=live_price,
                recent_candles_1h=candles_payload,
                ema50_4h=ema50,
                now=checked_at,
                continuation_gates=continuation_gates,
                recovery_gates=recovery_gates,
                recovery_details=recovery_details,
                max_age_hours=(
                    continuation_horizon
                    if is_continuation
                    else recovery_horizon
                    if is_recovery
                    else 72.0
                ),
            )
            if events is not None:
                events.append(
                    {
                        "symbol": sym,
                        "first_seen_at": updated.get("first_seen_at"),
                        "at": checked_at.isoformat(),
                        "previous_state": prev.get("radar_status"),
                        "state": decision.state.value,
                        "retained": decision.retained,
                        "reason": decision.reason,
                        "triggered_at": decision.triggered_at,
                        "trigger_basis": decision.trigger_basis,
                        "trigger_entry": str(decision.trigger_entry_price) if decision.trigger_entry_price is not None else None,
                        "trigger_stop": str(decision.trigger_stop_price) if decision.trigger_stop_price is not None else None,
                        "candidate": {k: v for k, v in updated.items() if k != "candles"},
                        "observed_price": str(live_price),
                        "checklist": list(decision.updated_checklist),
                    }
                )
            if not decision.retained:
                return None
            updated["radar_data_status"] = (
                "unavailable"
                if any(c.get("status") == "unknown" for c in decision.updated_checklist)
                or decision.reason == "invalid_discovery_timestamp"
                else "current"
            )
            updated.pop("radar_data_reason", None)
            updated["radar_checked_at"] = checked_at.isoformat()
            if decision.triggered_at:
                updated["radar_triggered_at"] = decision.triggered_at
                updated["radar_trigger_basis"] = decision.trigger_basis
                for key, value in (("radar_trigger_entry", decision.trigger_entry_price),
                                   ("radar_trigger_stop", decision.trigger_stop_price)):
                    if value is None:
                        updated.pop(key, None)
                    else:
                        updated[key] = str(value)
            updated["radar_quote"] = radar_quote_snapshot(quote, sym, checked_at)
            updated["current_price"] = str(live_price)
            updated["candles"] = candles_payload
            updated["radar_status"] = decision.state.value
            updated["radar_status_label"] = decision.label
            updated["pending_retention_healthy"] = bool(
                decision.triggered_at is None and decision.gates_passed == decision.gates_total
            )
            updated["gates_passed"] = decision.gates_passed
            updated["gates_total"] = decision.gates_total
            updated["failed_gates"] = list(decision.failed_gates)
            if decision.updated_checklist:
                updated["checklist"] = [dict(item) for item in decision.updated_checklist]
            if decision.pnl_from_entry_pct is not None:
                updated["trade_pnl_pct"] = str(decision.pnl_from_entry_pct)
            else:
                updated.pop("trade_pnl_pct", None)
            return updated
        except Exception:
            return unavailable(prev, "market_refresh_failed")

    checked = await asyncio.gather(*(_check_single(p) for p in prior_opportunities))
    for item in checked:
        if item is not None:
            sym = str(item.get("symbol", ""))
            base = item.get("base_asset") or sym.removesuffix("USDT").removesuffix("USDC")
            if base and base not in seen_bases:
                seen_bases.add(base)
                retained.append(item)
    return retained
