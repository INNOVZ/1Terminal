"""Rate-bounded :16/:31/:46 15-minute Kinetic Scout Service for ALFA v3.3.

Design & Binance Rate-Limit Guarantees:
1. Never overlaps the top-of-hour (:00..:05 and :58..:59) full 320-symbol sweep and never runs
   concurrently with `_scan_execution_lock`.
2. Scouts up to `48` symbols (`15m` klines `limit=96`, weight `1` per symbol) prioritizing
   staged `WATCHLIST` pre-ignition coils plus `scout_selection` `$500k+` activity movers.
3. Caps full Stage-1 + Stage-2 derivatives enrichment at `MAX_IGNITION_ENRICHMENTS = 6` symbols
   per quarter-hour tick (`<= 254 / 2,400` `/fapi/v1` weight and `<= 36 / 1,000` `/futures/data/*`
   requests per 5m).
4. Fail-closed: degraded or timed-out derivatives leave `WATCHLIST` coils untouched (neither
   promoted to `ON_RADAR` nor falsely invalidated).
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from algotrader.application.auto_watchlist_service import AutoWatchlistService
from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.application.scan_recording import MacroContextSource
from algotrader.config import Settings
from algotrader.domain.auto_watchlist import AutoWatchStatus
from algotrader.domain.crypto_screener_models import ScreenerMode
from algotrader.domain.discovery_selection import scout_features, scout_selection
from algotrader.domain.models import Candle, Direction, Instrument, Quote, Venue
from algotrader.domain.ports import DerivativesDataPort, MarketDataPort
from algotrader.domain.radar_insights import record_radar_publication

SCOUT_MAX_SYMBOLS = 48
MAX_IGNITION_ENRICHMENTS = 6


class _SubsetScoutMarket:
    """MarketDataPort wrapper restricting Stage-1/Stage-2 evaluation to `<= 6` ignited symbols + BTC."""

    def __init__(
        self,
        inner: MarketDataPort,
        selected: Sequence[Instrument],
        quotes_by_symbol: dict[str, Quote],
    ) -> None:
        self._inner = inner
        self._selected = tuple(selected)
        self._quotes_by_symbol = quotes_by_symbol
        self.venue: Venue = getattr(inner, "venue", Venue.BINANCE_USDM)

    async def instruments(self) -> Sequence[Instrument]:
        return self._selected

    async def quotes(self, instruments: Sequence[Instrument]) -> Sequence[Quote]:
        return [
            self._quotes_by_symbol[inst.id.symbol]
            for inst in instruments
            if inst.id.symbol in self._quotes_by_symbol
        ]

    async def candles(self, instrument: Instrument, interval: str, limit: int) -> Sequence[Candle]:
        return await self._inner.candles(instrument, interval, limit)


class KineticScoutService:
    """Quarter-hour (`:16`, `:31`, `:46`) kinetic breakout promoter for Watchlist coils & emerging movers."""

    def __init__(
        self,
        *,
        settings: Settings,
        market: MarketDataPort,
        derivatives: DerivativesDataPort | None,
        macro_provider: MacroContextSource | None,
        auto_watchlist: AutoWatchlistService,
        scan_lock: asyncio.Lock,
        on_promoted: Callable[[list[dict[str, Any]], datetime, str], Awaitable[None]] | None = None,
    ) -> None:
        self._settings = settings
        self._market = market
        self._derivatives = derivatives
        self._macro_provider = macro_provider
        self._auto_watchlist = auto_watchlist
        self._scan_lock = scan_lock
        self._on_promoted = on_promoted
        self._seen_at: dict[str, datetime] = {}

    async def run_quarter_hour_tick(self, *, now: datetime | None = None) -> dict[str, Any]:
        ref_now = (now or datetime.now(UTC)).astimezone(UTC)
        if ref_now.minute < 6 or ref_now.minute >= 58:
            return {
                "status": "skipped_hourly_window",
                "at": ref_now.isoformat(),
                "estimated_fapi_weight": 0,
                "futures_data_calls": 0,
                "promoted_count": 0,
            }
        if self._scan_lock.locked():
            return {
                "status": "skipped_scan_lock_busy",
                "at": ref_now.isoformat(),
                "estimated_fapi_weight": 0,
                "futures_data_calls": 0,
                "promoted_count": 0,
            }

        async with self._scan_lock:
            all_insts = await asyncio.wait_for(self._market.instruments(), timeout=6.0)
            trading_insts = [i for i in all_insts if i.trading]
            if not trading_insts:
                return {
                    "status": "no_instruments",
                    "at": ref_now.isoformat(),
                    "estimated_fapi_weight": 1,
                    "futures_data_calls": 0,
                    "promoted_count": 0,
                }

            inst_by_sym = {i.id.symbol: i for i in trading_insts}
            btc_inst = inst_by_sym.get("BTCUSDT") or next(
                (i for i in trading_insts if i.base_asset == "BTC"),
                None,
            )

            quotes_raw = await asyncio.wait_for(self._market.quotes(trading_insts), timeout=6.0)
            quotes_by_sym = {q.instrument_id.symbol: q for q in quotes_raw}
            est_weight = 56  # exchangeInfo (1) + bulk quotes (55)
            futures_data_calls = 0

            if btc_inst is None:
                return {
                    "status": "missing_btc_benchmark",
                    "at": ref_now.isoformat(),
                    "estimated_fapi_weight": est_weight,
                    "futures_data_calls": 0,
                    "promoted_count": 0,
                }

            btc_raw = await asyncio.wait_for(self._market.candles(btc_inst, "15m", 97), timeout=6.0)
            btc_15m = [c for c in btc_raw if c.closed and c.closed_at <= ref_now][-96:]
            est_weight += 1

            # 1. Prioritize eligible WATCHLIST coils (`graduation_count == 0`: fresh pre-ignition coils
            #    plus demoted coils actively challenging/crossing their breakout `trigger_price`), then scout movers
            blocked = self._auto_watchlist.blocked_from_radar_symbols(now=ref_now, cooldown_hours=12.0)
            episodes = self._auto_watchlist.list_episodes(days=3, now=ref_now)
            coil_symbols: list[str] = []
            for ep in episodes:
                if (
                    ep.status is not AutoWatchStatus.WATCHLIST
                    or ep.graduation_count >= 1
                    or ep.symbol not in inst_by_sym
                ):
                    continue
                q_sym = quotes_by_sym.get(ep.symbol)
                near_or_above_trigger = (
                    ep.trigger_price is not None
                    and ep.trigger_price > Decimal("0")
                    and q_sym is not None
                    and q_sym.last >= ep.trigger_price * Decimal("0.990")
                )
                if not ep.was_promoted_to_watchlist:
                    if (ep.symbol, ep.direction) not in blocked:
                        coil_symbols.append(ep.symbol)
                elif near_or_above_trigger:
                    coil_symbols.append(ep.symbol)
            chosen_insts: list[Instrument] = []
            seen_ids: set[str] = {"BTCUSDT"}
            for sym in coil_symbols[:SCOUT_MAX_SYMBOLS]:
                if sym not in seen_ids:
                    seen_ids.add(sym)
                    chosen_insts.append(inst_by_sym[sym])

            rem_scouts = SCOUT_MAX_SYMBOLS - len(chosen_insts)
            if rem_scouts > 0:
                pool = [
                    i
                    for i in trading_insts
                    if i.id.symbol not in seen_ids
                    and (i.id.symbol, "long") not in blocked
                ]
                extra = scout_selection(pool, quotes_by_sym, self._seen_at, ref_now, limit=rem_scouts)
                for inst in extra:
                    seen_ids.add(inst.id.symbol)
                    chosen_insts.append(inst)

            # 2. Fetch 97 x 15m candles (weight = 1 per symbol since limit < 100) and slice 96 closed bars
            sem_15m = asyncio.Semaphore(10)
            ignited_candidates: list[tuple[Decimal, Decimal, Instrument]] = []

            async def _check_15m(inst: Instrument) -> None:
                nonlocal est_weight
                async with sem_15m:
                    with contextlib.suppress(Exception):
                        raw_bars = await asyncio.wait_for(
                            self._market.candles(inst, "15m", 97), timeout=5.0
                        )
                        bars = [c for c in raw_bars if c.closed and c.closed_at <= ref_now][-96:]
                        est_weight += 1
                        self._seen_at[inst.id.symbol] = ref_now
                        feats = scout_features(bars, btc_15m, ref_now)
                        if not feats.get("available"):
                            return
                        rel_6h = Decimal(str(feats["relative_6h_pp"]))
                        accel = Decimal(str(feats["turnover_acceleration"]))
                        ret24 = Decimal(str(feats["return_24h_pct"]))
                        is_staged_coil = inst.id.symbol in coil_symbols
                        min_rel = Decimal("1.0") if is_staged_coil else Decimal("1.8")
                        min_acc = Decimal("1.35") if is_staged_coil else Decimal("1.55")
                        if rel_6h >= min_rel and accel >= min_acc and Decimal("-2.0") <= ret24 <= Decimal("18.0"):
                            ignited_candidates.append((rel_6h, accel, inst))

            await asyncio.gather(*(_check_15m(i) for i in chosen_insts))
            ignited_candidates.sort(key=lambda item: (-item[0], -item[1], item[2].id.symbol))
            top_ignited = [inst for _, _, inst in ignited_candidates[:MAX_IGNITION_ENRICHMENTS]]

            if not top_ignited or self._derivatives is None:
                return {
                    "status": "ok",
                    "at": ref_now.isoformat(),
                    "scouted_count": len(chosen_insts),
                    "ignited_count": len(top_ignited),
                    "promoted_count": 0,
                    "estimated_fapi_weight": est_weight,
                    "futures_data_calls": 0,
                }

            # 3. Evaluate ONLY the <= 6 ignited symbols + BTC through full 5/5 Stage-1 + Stage-2 Screener
            subset_insts = [btc_inst, *top_ignited]
            subset_market = _SubsetScoutMarket(self._market, subset_insts, quotes_by_sym)
            v33_settings = self._settings.model_copy(update={"kinetic_ignition_v33_enabled": True})
            screener = CryptoOpportunityScreener(
                v33_settings,
                self._macro_provider,
                evaluated_at=ref_now,
                feature_contract="discovery_v2",
            )
            ranked_24h = sorted(
                (
                    (q.price_change_pct_24h, s)
                    for s, q in quotes_by_sym.items()
                    if q.price_change_pct_24h is not None and q.quote_volume_24h >= Decimal("500000")
                ),
                reverse=True,
            )
            live_top10 = frozenset(s for chg, s in ranked_24h[:10] if chg is not None and chg >= Decimal("10.0"))

            opps = await screener.screen(
                subset_market,
                derivatives_port=self._derivatives,
                mode=ScreenerMode.ALL,
                target_direction=Direction.LONG,
                max_symbols=len(subset_insts),
                enforce_quality=True,
                top_gainer_symbols=live_top10,
            )
            s2_cnt = int(screener.scan_counts.get("stage2_derivatives_attempted", len(top_ignited)))
            est_weight += len(top_ignited) * 5 + 2 + s2_cnt * 4
            futures_data_calls += s2_cnt * 2

            promoted_dicts: list[dict[str, Any]] = []
            scan_id = f"scout-{uuid4()}"
            for opp in opps:
                if opp.publication_lane != "ON_RADAR":
                    continue
                if len(opp.checklist) != 5 or not all(item[1] is True for item in opp.checklist):
                    continue
                d = dict(opp.as_dict())
                d["radar_origin"] = "fresh"
                d["origin_scan_id"] = scan_id
                d["first_seen_at"] = ref_now.isoformat()
                d["discovery_price"] = d.get("current_price")
                d["radar_status"] = "FRESH_COIL"
                d["radar_status_label"] = "KINETIC BREAKOUT"
                d["gates_passed"] = 5
                d["gates_total"] = 5
                d["failed_gates"] = []
                # ALFA v3.7 (P1-4): Record publication metadata + pre-fetch 1H chart candles so
                # scout promotions never show legacy-episode warnings or empty charts in the UI.
                record_radar_publication(
                    d,
                    quotes_by_sym.get(opp.instrument.id.symbol),
                    ref_now,
                    scan_id,
                )
                if not d.get("candles"):
                    with contextlib.suppress(Exception):
                        raw_1h = await asyncio.wait_for(
                            self._market.candles(opp.instrument, "1h", 250),
                            timeout=3.0,
                        )
                        d["candles"] = [
                            {
                                "time": int(c.closed_at.timestamp()),
                                "open": float(c.open),
                                "high": float(c.high),
                                "low": float(c.low),
                                "close": float(c.close),
                                "volume": float(c.volume),
                            }
                            for c in raw_1h
                            if c.closed
                        ]
                promoted_dicts.append(d)

            if promoted_dicts:
                await asyncio.to_thread(
                    self._auto_watchlist.sync_from_scan,
                    published_opportunities=promoted_dicts,
                    radar_events=[],
                    now=ref_now,
                    scan_id=scan_id,
                    incremental=True,
                )
                if self._on_promoted is not None:
                    await self._on_promoted(promoted_dicts, ref_now, scan_id)

            return {
                "status": "ok",
                "at": ref_now.isoformat(),
                "scouted_count": len(chosen_insts),
                "ignited_count": len(top_ignited),
                "promoted_count": len(promoted_dicts),
                "promoted_symbols": [str(p["symbol"]) for p in promoted_dicts],
                "estimated_fapi_weight": est_weight,
                "futures_data_calls": futures_data_calls,
            }
