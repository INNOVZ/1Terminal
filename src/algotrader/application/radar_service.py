"""Bounded public-data refresh for radar lifecycle decisions.

The domain owns transitions; this service owns provider timeouts, data freshness and
unknown-data retention. It neither submits orders nor enrolls research sessions.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from algotrader.domain.indicators import bollinger_bands, ema, volume_contraction_metrics
from algotrader.domain.models import Candle
from algotrader.domain.radar_lifecycle import evaluate_radar_candidate_lifecycle


async def reconcile_radar_cohort(
    prior_opportunities: list[dict[str, Any]],
    seen_bases: set[str],
    futures: Any,
    now: datetime | None = None,
    events: list[dict[str, Any]] | None = None,
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
        updated["checklist"] = [
            {
                "title": title,
                "passed": False,
                "status": "unknown",
                "detail": "Unavailable: fresh market data required",
            }
            for title in (
                "Trend Baseline",
                "Anti-Chasing Validated",
                "Value Acceptance",
                "Volatility Compression",
                "Smart Money Absorption",
            )
        ]
        return updated

    if not prior_opportunities:
        return []
    inst_by_symbol = {}
    quotes_by_symbol = {}
    try:
        all_insts = await asyncio.wait_for(futures.instruments(), timeout=6.0)
        inst_by_symbol = {inst.id.symbol: inst for inst in all_insts}
        wanted = [
            inst_by_symbol[str(p.get("symbol"))]
            for p in prior_opportunities
            if str(p.get("symbol")) in inst_by_symbol
        ]
        if wanted:
            quotes = await asyncio.wait_for(futures.quotes(wanted), timeout=6.0)
            quotes_by_symbol = {q.instrument_id.symbol: q for q in quotes}
    except Exception:
        pass  # Retain uncertainty explicitly; a provider error is not invalidation.

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
            and quote.last.is_finite()
            and quote.last > 0
            and -5 <= (quote_now - quote.observed_at).total_seconds() <= 60
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
                or not quote.last.is_finite()
                or quote.last <= 0
                or not -5 <= (checked_at - quote.observed_at).total_seconds() <= 60
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
            updated["radar_origin"] = "retained"
            updated["radar_gate_policy"] = "retention-health-v2"
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
                if getattr(deriv_snap, "open_interest_change_1h", None) is not None:
                    metrics["oi_change_1h_pct"] = (
                        f"{float(deriv_snap.open_interest_change_1h * Decimal('100')):+.2f}%"
                    )
                if getattr(deriv_snap, "funding_rate", None) is not None:
                    metrics["funding_rate_bps"] = (
                        f"{float(deriv_snap.funding_rate * Decimal('10000')):+.2f} bps"
                    )
                if getattr(deriv_snap, "taker_buy_sell_ratio", None) is not None:
                    metrics["taker_buy_sell_ratio"] = (
                        f"{float(deriv_snap.taker_buy_sell_ratio):.4f}"
                    )
            decision = evaluate_radar_candidate_lifecycle(
                updated,
                current_price=live_price,
                recent_candles_1h=candles_payload,
                ema50_4h=ema50,
                now=checked_at,
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
            updated["current_price"] = str(live_price)
            updated["candles"] = candles_payload
            updated["radar_status"] = decision.state.value
            updated["radar_status_label"] = decision.label
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
