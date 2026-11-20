"""Incremental bounded discovery and same-input core/recovery qualification."""
from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, TypeVar, cast

from algotrader.adapters.external.models import MacroSentimentContext
from algotrader.application.activity_recovery import PoolControlScreener, PoolRecoveryScreener
from algotrader.application.recovery_episodes import RecoveryEpisodeService
from algotrader.application.scan_replay import ReplayMarket, ReplayMismatch, ReplayTape
from algotrader.config import Settings
from algotrader.domain.crypto_screener_models import SetupLabel
from algotrader.domain.discovery_selection import (
    ACTIVITY_POLICY,
    liquid_quote,
    positioning_regime,
    scout_features,
    scout_selection,
)
from algotrader.domain.models import (
    Candle,
    DerivativesSnapshot,
    Direction,
    Instrument,
    Quote,
    Venue,
)
from algotrader.domain.ports import DerivativesDataPort, MarketDataPort
from algotrader.domain.recovery import RECOVERY_POLICY_VERSION
from algotrader.domain.recovery_episodes import RecoveryEpisode, require_aware
from algotrader.domain.scan_observations import RecordedCall, ScanDecisionJournal
from algotrader.infrastructure.recovery_episodes import (
    RecoveryEpisodeStore,
    episode_values,
    serialize,
)
from algotrader.infrastructure.rolling_discovery import RollingDiscoveryStore

T = TypeVar("T")

ROLLING_POLICY: dict[str, Any] = {
    "version": "rolling-recovery-v1",
    "watch_policy": ACTIVITY_POLICY,
    "candidate_policy": RECOVERY_POLICY_VERSION,
    "scouts_per_cycle": 64,
    "priority_scouts": 16,
    "qualification_per_cycle": 4,
    "active_episode_limit": 30,
    "study_episode_limit": 120,
    "deep_cache_instruments": 64,
    "scout_closed_bars": 96,
    "macro": "frozen_issuance_context_shared_by_matched_arms",
    "control": "current_core_long_only_same_pool_conservative_discovery_tier",
    "history": "closed_incremental_buffers",
    "capture_quote_age_seconds": 600,
    "scout_quote_age_seconds": 120,
    "signal_deduplication": "instrument_closed_hour",
    "accrual_window_hours": 48,
    "followup_window_hours": 120,
}


def rank_ready_candidates(
    ready: Sequence[tuple[Decimal, Decimal, str]],
    *,
    prior_summaries: Sequence[dict[str, Any]] = (),
    latest_scouts: dict[str, dict[str, Any]] | None = None,
    now: datetime | None = None,
    backoff_minutes: int = 45,
    slots: int = 4,
    reserved_exploration_slots: int = 1,
) -> list[tuple[Decimal, Decimal, str]]:
    """Order ready pool candidates with reserved exploration capacity, waiting-age boost, and reason-specific backoff.

    - Distinguishes published evaluations, data-unavailable evaluations, and genuine strategy rejections.
      Evaluations with unavailable/degraded data are not placed in rejection backoff.
    - Genuine strategy rejections within `backoff_minutes` are placed in the backoff tier (`0`)
      unless their current features show a material positive change compared against their last
      evaluated features (`relative_6h_pp >= 4.0` with `delta >= +1.0`, or
      `turnover_acceleration >= 1.75` with `delta >= +0.25`).
    - Applies a waiting-age boost per 15-minute cycle waited so deferred candidates cannot starve.
    - Reserves `reserved_exploration_slots` within the top `slots` for never-evaluated candidates
      whenever fresh candidates exist.
    """
    if not ready:
        return []
    if not prior_summaries or now is None:
        return sorted(ready, key=lambda item: (-item[0], -item[1], item[2]))

    last_eval_at: dict[str, datetime] = {}
    last_status: dict[str, str] = {}
    last_eval_features: dict[str, tuple[Decimal, Decimal]] = {}
    first_waiting_at: dict[str, datetime] = {}
    deferred_cycles: dict[str, int] = {}

    for summary in prior_summaries:
        eval_at = summary.get("evaluation_at") or summary.get("issued_at")
        summary_features: dict[str, tuple[Decimal, Decimal]] = {}
        eval_feats = summary.get("evaluated_features")
        if isinstance(eval_feats, dict):
            for s, feat in eval_feats.items():
                if (
                    isinstance(feat, dict)
                    and feat.get("relative_6h_pp") is not None
                    and feat.get("turnover_acceleration") is not None
                ):
                    summary_features[str(s)] = (
                        Decimal(str(feat["relative_6h_pp"])),
                        Decimal(str(feat["turnover_acceleration"])),
                    )
        for obs in summary.get("scout_observations", ()):
            if (
                isinstance(obs, dict)
                and obs.get("symbol")
                and obs.get("relative_6h_pp") is not None
                and obs.get("turnover_acceleration") is not None
            ):
                summary_features.setdefault(
                    str(obs["symbol"]),
                    (Decimal(str(obs["relative_6h_pp"])), Decimal(str(obs["turnover_acceleration"]))),
                )

        published_syms = set(summary.get("control_symbols", ())) | set(summary.get("challenger_symbols", ()))
        unavailable_syms: set[str] = set(summary.get("unavailable_symbols", ()))
        unavailable_syms |= set(summary.get("feature_unavailable", ())) | set(summary.get("fetch_failures", ()))
        for arm in ("control", "challenger"):
            arm_data = summary.get(arm)
            if isinstance(arm_data, dict):
                unavailable_syms |= set(arm_data.get("feature_unavailable", ())) | set(
                    arm_data.get("fetch_failures", ())
                )
        for dec in summary.get("decisions", ()):
            if isinstance(dec, dict) and dec.get("symbol"):
                reason_str = str(dec.get("reason", "")).lower()
                if any(tok in reason_str for tok in ("unavailable", "degraded", "fetch_failure")):
                    unavailable_syms.add(str(dec["symbol"]))

        global_unavail = bool(summary.get("global_input_unavailable")) or (
            not unavailable_syms and bool(summary.get("scout_counts", {}).get("deep_input_unavailable"))
        )

        pool_syms = set(summary.get("pool", ()))
        for sym in summary.get("pool", ()):
            if isinstance(eval_at, datetime):
                last_eval_at[sym] = eval_at
            if sym in published_syms:
                last_status[sym] = "published"
            elif global_unavail or sym in unavailable_syms:
                last_status[sym] = "unavailable"
            else:
                last_status[sym] = "rejected"
            if sym in summary_features:
                last_eval_features[sym] = summary_features[sym]
            first_waiting_at.pop(sym, None)
            deferred_cycles[sym] = 0

        for entry in summary.get("qualification_admission", ()):
            if isinstance(entry, dict) and entry.get("reason") == "qualification_capacity":
                s = str(entry.get("symbol", ""))
                if s and s not in pool_syms:
                    deferred_cycles[s] = deferred_cycles.get(s, 0) + 1
                    if isinstance(eval_at, datetime) and s not in first_waiting_at:
                        first_waiting_at[s] = eval_at

        for s in summary_features:
            if s not in pool_syms:
                deferred_cycles[s] = deferred_cycles.get(s, 0) + 1
                if isinstance(eval_at, datetime) and s not in first_waiting_at:
                    first_waiting_at[s] = eval_at

    if latest_scouts:
        for s, row in latest_scouts.items():
            if s not in first_waiting_at and s not in last_eval_at and isinstance(row, dict):
                obs_at = row.get("observed_at")
                if isinstance(obs_at, datetime) and obs_at < now:
                    first_waiting_at[s] = obs_at

    scored: list[tuple[tuple[int, Decimal, Decimal, int], tuple[Decimal, Decimal, str]]] = []
    for rel_6h, accel, sym in ready:
        prev_at = last_eval_at.get(sym)
        status = last_status.get(sym)
        never_evaluated = 1 if prev_at is None else 0
        in_recent_rejection_backoff = False
        if prev_at is not None and status == "rejected":
            age_min = (now - prev_at).total_seconds() / 60.0
            if 0.0 <= age_min < backoff_minutes:
                in_recent_rejection_backoff = True

        material_new_evidence = False
        prev_feat = last_eval_features.get(sym)
        if prev_feat is not None:
            prev_rel_6h, prev_accel = prev_feat
            rel_jump = rel_6h >= Decimal("4.0") and (rel_6h - prev_rel_6h) >= Decimal("1.0")
            accel_jump = accel >= Decimal("1.75") and (accel - prev_accel) >= Decimal("0.25")
            material_new_evidence = rel_jump or accel_jump

        fresh_tier = 0 if (in_recent_rejection_backoff and not material_new_evidence) else 1

        if sym in first_waiting_at:
            wait_min = max(0.0, (now - first_waiting_at[sym]).total_seconds() / 60.0)
        elif prev_at is not None and not in_recent_rejection_backoff:
            wait_min = max(0.0, (now - prev_at).total_seconds() / 60.0 - backoff_minutes)
        else:
            wait_min = 0.0
        cycles_waited = max(
            Decimal(deferred_cycles.get(sym, 0)),
            Decimal(str(round(wait_min / 15.0, 4))),
        )
        aging_boost = min(Decimal("12.0"), cycles_waited * Decimal("0.75")) if fresh_tier == 1 else Decimal("0")
        effective_rel_6h = rel_6h + aging_boost

        scored.append(((fresh_tier, effective_rel_6h, accel, never_evaluated), (rel_6h, accel, sym)))

    scored.sort(key=lambda item: (-item[0][0], -item[0][1], -item[0][2], -item[0][3], item[1][2]))
    ordered = [item[1] for item in scored]

    if slots >= 2 and reserved_exploration_slots > 0 and len(ordered) > slots:
        target_exploration = min(reserved_exploration_slots, slots // 2)
        top_window = list(ordered[:slots])
        rest_window = list(ordered[slots:])
        fresh_in_top = [item for item in top_window if item[2] not in last_eval_at]
        fresh_in_rest = [item for item in rest_window if item[2] not in last_eval_at]
        if len(fresh_in_top) < target_exploration and fresh_in_rest:
            needed = min(target_exploration - len(fresh_in_top), len(fresh_in_rest))
            promoted = fresh_in_rest[:needed]
            promoted_syms = {item[2] for item in promoted}
            kept_top: list[tuple[Decimal, Decimal, str]] = []
            displaced: list[tuple[Decimal, Decimal, str]] = []
            # Displace the lowest-ranked non-fresh items from top_window
            non_fresh_indices = [idx for idx, item in enumerate(top_window) if item[2] in last_eval_at]
            displace_indices = set(non_fresh_indices[-needed:])
            for idx, item in enumerate(top_window):
                if idx in displace_indices:
                    displaced.append(item)
                else:
                    kept_top.append(item)
            remaining_rest = [item for item in rest_window if item[2] not in promoted_syms]
            ordered = kept_top + promoted + displaced + remaining_rest

    return ordered


def latest_open(at: datetime, interval: str) -> datetime:
    at = at.astimezone(UTC)
    if interval == "1w":
        monday = at.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=at.weekday())
        return monday - timedelta(days=7)
    seconds = {"15m": 900, "1h": 3600, "4h": 14400}[interval]
    return datetime.fromtimestamp(int(at.timestamp()) // seconds * seconds - seconds, UTC)


class CachedDiscoveryMarket:
    venue = Venue.BINANCE_USDM

    def __init__(self, market: MarketDataPort, derivatives: DerivativesDataPort,
                 store: RollingDiscoveryStore, cohort: Sequence[Instrument], benchmark: Instrument,
                 cutoff: datetime) -> None:
        self.market, self.derivatives, self.store = market, derivatives, store
        self.cohort, self.benchmark, self.cutoff = tuple(cohort), benchmark, cutoff
        self.current_quotes: dict[str, Quote] = {}
        self.flows: dict[str, DerivativesSnapshot] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def instruments(self) -> Sequence[Instrument]:
        return self.cohort

    async def update_quotes(self) -> None:
        instruments = {i.id: i for i in (*self.cohort, self.benchmark)}
        quotes = await asyncio.wait_for(self.market.quotes(tuple(instruments.values())), 5)
        grouped: dict[str, list[Quote]] = {}
        for quote in quotes:
            if quote.instrument_id not in instruments:
                continue
            require_aware(quote.observed_at)
            grouped.setdefault(quote.instrument_id.symbol, []).append(quote)
        self.current_quotes = {s: matches[0] for s, matches in grouped.items() if len(matches) == 1}

    async def quotes(self, instruments: Sequence[Instrument]) -> Sequence[Quote]:
        return [self.current_quotes[i.id.symbol] for i in instruments if i.id.symbol in self.current_quotes]

    async def derivatives_snapshot(self, instrument: Instrument) -> DerivativesSnapshot:
        symbol = instrument.id.symbol
        if symbol not in self.flows:
            self.flows[symbol] = await asyncio.wait_for(self.derivatives.derivatives_snapshot(instrument), 5)
        return self.flows[symbol]

    async def candles(self, instrument: Instrument, interval: str, limit: int) -> Sequence[Candle]:
        if interval not in ("15m", "1h", "4h", "1w") or not 1 <= limit <= 480:
            raise ValueError("unsupported discovery history request")
        key = (instrument.id.symbol, interval)
        async with self._locks.setdefault(key, asyncio.Lock()):
            history, previous_limit = self.store.cache(*key)
            expected = latest_open(self.cutoff, interval)
            if history and history[-1].opened_at > expected:
                raise ValueError("future cached discovery history")
            if history and history[-1].opened_at == expected and previous_limit >= limit:
                return history[-limit:]
            duration = {"15m": 900, "1h": 3600, "4h": 14400, "1w": 604800}[interval]
            fetch_limit = limit + 1
            if history and previous_limit >= limit:
                fetch_limit = min(limit + 1, max(2, math.ceil((self.cutoff - history[-1].opened_at).total_seconds() / duration) + 1))
            fetched = await asyncio.wait_for(self.market.candles(instrument, interval, fetch_limit), 5)
            if len(fetched) > fetch_limit:
                raise ValueError("provider exceeded discovery request bound")
            for candle in fetched:
                require_aware(candle.opened_at)
                require_aware(candle.closed_at)
            closed = tuple(c for c in fetched if c.closed is True and c.closed_at <= self.cutoff)
            if (not closed or any(not 0 <= (c.closed_at - c.opened_at).total_seconds() - (duration - 1) <= 1.001 for c in closed)
                    or any(not v.is_finite() or v <= 0 for c in closed for v in (c.open, c.high, c.low, c.close))
                    or any(not c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high for c in closed)
                    or any(not v.is_finite() or v < 0 for c in closed for v in (c.volume, c.quote_volume))
                    or len({c.opened_at for c in closed}) != len(closed)):
                raise ValueError("invalid discovery candle response")
            merged = {c.opened_at: c for c in history}
            for candle in closed:
                old = merged.get(candle.opened_at)
                if old is not None and old != candle:
                    raise ValueError("closed discovery candle revision")
                merged[candle.opened_at] = candle
            keep = max(limit, previous_limit)
            result = tuple(sorted(merged.values(), key=lambda c: c.opened_at)[-keep:])
            if (result[-1].opened_at != expected
                    or any((b.opened_at - a.opened_at).total_seconds() != duration
                           for a, b in zip(result, result[1:], strict=False))):
                raise ValueError("missing discovery history interval")
            self.store.write_cache(*key, result, keep, self.cutoff)
            return result[-limit:]


class PoolSnapshotMarket:
    venue = Venue.BINANCE_USDM

    def __init__(self, source: CachedDiscoveryMarket, pool: Sequence[Instrument], macro: MacroSentimentContext) -> None:
        self.source, self.pool, self.macro = source, tuple(pool), macro
        self.calls: dict[str, RecordedCall] = {}

    async def _memo(self, method: str, arguments: dict[str, Any], operation: Callable[[], Awaitable[T]]) -> T:
        key = serialize((method, arguments))
        if key not in self.calls:
            try:
                value = await operation()
                self.calls[key] = RecordedCall(method, 0, arguments, value)
            except Exception as exc:
                self.calls[key] = RecordedCall(method, 0, arguments, error=type(exc).__name__)
                raise
        call = self.calls[key]
        if call.error:
            raise ValueError("discovery input unavailable: " + call.error)
        return cast(T, call.result)

    async def get_context(self) -> MacroSentimentContext:
        async def value() -> MacroSentimentContext:
            return self.macro
        return await self._memo("macro", {}, value)

    async def instruments(self) -> Sequence[Instrument]:
        async def value() -> tuple[Instrument, ...]:
            return tuple({i.id: i for i in (*self.pool, self.source.benchmark)}.values())
        return await self._memo("instruments", {}, value)

    async def quotes(self, instruments: Sequence[Instrument]) -> Sequence[Quote]:
        return await self._memo("quotes", {"instruments": tuple(instruments)}, lambda: self.source.quotes(instruments))

    async def candles(self, instrument: Instrument, interval: str, limit: int) -> Sequence[Candle]:
        return await self._memo("candles", {"instrument": instrument, "interval": interval, "limit": limit},
                                lambda: self.source.candles(instrument, interval, limit))

    async def derivatives_snapshot(self, instrument: Instrument) -> DerivativesSnapshot:
        return await self._memo("derivatives_snapshot", {"instrument": instrument},
                                lambda: self.source.derivatives_snapshot(instrument))


class SnapshotTape(ReplayTape):
    def __init__(self, calls: Sequence[RecordedCall]) -> None:
        super().__init__(tuple(calls))
        self.snapshot_calls = {serialize((c.method, c.arguments)): c for c in calls}
        self.used: set[str] = set()
        if len(self.snapshot_calls) != len(calls):
            raise ValueError("duplicate rolling snapshot call")

    def take(self, method: str, arguments: dict[str, Any]) -> Any:
        call = self.snapshot_calls.get(serialize((method, arguments)))
        if call is None:
            self.missing = True
            raise ReplayMismatch("rolling snapshot input missing")
        self.used.add(serialize((method, arguments)))
        if call.error:
            raise ReplayMismatch("rolling snapshot input unavailable")
        return call.result

    def assert_consumed(self) -> None:
        if self.missing or self.used != set(self.snapshot_calls):
            raise ReplayMismatch("rolling snapshot requests were not completely reproduced")


def engine(settings: Settings, market: Any, cutoff: datetime, recovery: bool, *, decision_at: datetime | None,
           journal: ScanDecisionJournal) -> Any:
    if recovery:
        return PoolRecoveryScreener(settings, market, evaluated_at=cutoff, decision_at=decision_at, journal=journal)
    return PoolControlScreener(settings, market, evaluated_at=cutoff, feature_contract="discovery_v2", journal=journal)


def recovery_seed(opportunity: Any, issued: datetime, calls: Sequence[RecordedCall]) -> dict[str, Any]:
    history = next((call.result for call in calls if call.method == "candles" and not call.error
                    and call.arguments["instrument"].id == opportunity.instrument.id
                    and call.arguments["interval"] == "1h" and call.arguments["limit"] == 360), None)
    if not history:
        raise ValueError("recovery signal history unavailable")
    last_closed = max(c.closed_at for c in history if c.closed)
    signal = serialize((opportunity.instrument.id, last_closed, opportunity.suggested_entry, opportunity.suggested_stop))
    return {"state": episode_values(RecoveryEpisode.enroll(opportunity, issued)),
            "evidence": {"kind": "enrollment", "candidate": opportunity.as_dict(),
                         "signal_key": hashlib.sha256(signal.encode()).hexdigest()}}


async def paired_qualification(
    source: CachedDiscoveryMarket, pool: Sequence[Instrument], macro: MacroSentimentContext,
    settings: Settings, *, clock: Callable[[], datetime],
    top_gainer_symbols: frozenset[str] | None = None,
) -> dict[str, Any]:
    capture = PoolSnapshotMarket(source, pool, macro)
    request: dict[str, Any] = {"max_symbols": 320, "target_direction": Direction.LONG, "enforce_quality": True}
    if top_gainer_symbols is not None:
        request["top_gainer_symbols"] = top_gainer_symbols
    for recovery in (False, True):
        planner = engine(settings, capture, source.cutoff, recovery, decision_at=clock(), journal=ScanDecisionJournal())
        await planner.screen(capture, capture, **request)
    issued = clock()
    require_aware(issued)
    if not source.cutoff <= issued <= source.cutoff + timedelta(seconds=360):
        raise ValueError("rolling decision chronology exceeded acquisition bound")
    calls = tuple(capture.calls.values())
    global_missing = any(call.error and call.method == "candles"
                         and call.arguments["instrument"].id == source.benchmark.id
                         and call.arguments["interval"] == "1h" and call.arguments["limit"] == 240
                         for call in calls)
    output: dict[str, Any] = {"issued_at": issued, "evaluation_at": source.cutoff, "inputs": calls,
                              "global_input_unavailable": global_missing}
    pool_ids = {i.id for i in pool}
    for recovery in (False, True):
        market = ReplayMarket(SnapshotTape(calls))
        journal = ScanDecisionJournal()
        screener = engine(settings, market, source.cutoff, recovery, decision_at=issued, journal=journal)
        opportunities = await screener.screen(market, market, **request)
        unavailable = screener._fetch_failures | screener._feature_unavailable
        opportunities = [o for o in opportunities if o.instrument.id in pool_ids
                         and o.instrument.id.symbol not in unavailable and not global_missing]
        output["challenger" if recovery else "control"] = {
            "opportunities": tuple(o.as_dict() for o in opportunities), "counts": dict(screener.scan_counts),
            "decisions": journal.snapshot(), "feature_unavailable": tuple(sorted(screener._feature_unavailable)),
            "fetch_failures": tuple(sorted(screener._fetch_failures))}
        if recovery:
            seeds = []
            positioning = {}
            for opportunity in opportunities:
                if opportunity.setup_label is SetupLabel.RECOVERY_RECLAIM_LONG:
                    seed = recovery_seed(opportunity, issued, calls)
                    seeds.append(seed)
                    positioning[seed["state"]["episode_id"]] = positioning_regime(source.flows.get(opportunity.instrument.id.symbol), issued)
            output.update(seeds=tuple(seeds), positioning=positioning)
    return output


async def rolling_cycle(
    market: MarketDataPort, derivatives: DerivativesDataPort, episodes: RecoveryEpisodeStore,
    definition: dict[str, Any], decision_id: str, *, clock: Callable[[], datetime],
    accrual_until: datetime | None = None,
) -> dict[str, Any]:
    context = definition["recovery"]["rolling"]
    if context["policy"] != ROLLING_POLICY:
        raise ValueError("rolling recovery policy mismatch")
    state = RollingDiscoveryStore(episodes)
    previous = state.frame(decision_id)
    if previous is not None:
        return next(s for s in state.summaries() if s["decision_id"] == decision_id)
    now = clock()
    require_aware(now)
    source = CachedDiscoveryMarket(market, derivatives, state, definition["cohort"], context["benchmark"], now)
    await source.update_quotes()
    # Existing risk episodes get priority over acquisition of additional candidates.
    await RecoveryEpisodeService(episodes).refresh(source, source, clock=clock)
    if not source.current_quotes:
        raise ValueError("rolling cohort quotes unavailable")
    accrual_cutoff = accrual_until if accrual_until is not None else (
        definition["issued_at"] + timedelta(hours=ROLLING_POLICY["accrual_window_hours"])
    )
    accrual_open = now < accrual_cutoff
    observations: list[dict[str, Any]] = []
    scout_inputs: dict[str, tuple[Candle, ...]] = {}
    ready: list[tuple[Decimal, Decimal, str]] = []
    qualification: list[dict[str, str]] = []
    selected: tuple[Instrument, ...] = ()
    btc: Sequence[Candle] = ()
    pool: tuple[Instrument, ...] = ()
    slots = 0
    if accrual_open:
        cached = state.scouts()
        selected = scout_selection(tuple(i for i in definition["cohort"] if i.id != context["benchmark"].id), source.current_quotes,
                                   {s: row["observed_at"] for s, row in cached.items()}, now)
        btc = await source.candles(context["benchmark"], "15m", 96)
        for instrument in selected:
            try:
                bars = await source.candles(instrument, "15m", 96)
                scout_inputs[instrument.id.symbol] = tuple(bars)
                features = scout_features(bars, btc, now)
            except Exception as exc:
                features = {"available": False, "reason": type(exc).__name__}
            state.write_scout(instrument.id.symbol, now, features)
            observations.append({"symbol": instrument.id.symbol, **features})
        active = {e.instrument.id for e in episodes.episodes(active_only=True)}
        slots = min(ROLLING_POLICY["qualification_per_cycle"], max(0, episodes.capacity - len(active)),
                    max(0, ROLLING_POLICY["study_episode_limit"] - len(episodes.episodes())))
        fresh = clock()
        if any((fresh - q.observed_at).total_seconds() > 60 for q in source.current_quotes.values()):
            await source.update_quotes()
        latest = state.scouts()
        instruments = {i.id.symbol: i for i in definition["cohort"]}
        for symbol, row in latest.items():
            values = row["features"]
            if (symbol in instruments and instruments[symbol].id not in active
                    and values.get("emerging") is True and 0 <= (now - row["observed_at"]).total_seconds() <= 901):
                quote = source.current_quotes.get(symbol)
                if (quote is not None and liquid_quote(quote, fresh)
                        and quote.funding_rate is not None and quote.funding_rate.is_finite()
                        and quote.funding_rate <= Decimal("0.00025")):
                    ready.append((values["relative_6h_pp"], values["turnover_acceleration"], symbol))
                else:
                    qualification.append({"symbol": symbol, "reason": "quote_or_funding_unavailable"
                                          if quote is None or quote.funding_rate is None else "quoted_funding_veto"})
        ready = rank_ready_candidates(
            ready,
            prior_summaries=state.summaries(),
            latest_scouts=latest,
            now=now,
            slots=max(1, slots),
        )
        pool = tuple(instruments[s] for _, _, s in ready[:slots])
        qualification.extend({"symbol": symbol, "reason": "deep_evaluation" if index < len(pool) else "qualification_capacity"}
                             for index, (_, _, symbol) in enumerate(ready))
    if pool:
        liquid_source_quotes = [
            (q.price_change_pct_24h, sym)
            for sym, q in source.current_quotes.items()
            if q.price_change_pct_24h is not None and q.quote_volume_24h >= Decimal("500000")
        ]
        if len(liquid_source_quotes) >= 20:
            ranked_source_24h = sorted(liquid_source_quotes, reverse=True)
            live_top10 = frozenset(sym for chg, sym in ranked_source_24h[:10] if chg is not None and chg >= Decimal("10.0"))
        else:
            live_top10 = frozenset()
        frame = await paired_qualification(source, pool, context["macro"],
                                           Settings.model_construct(**context["configuration"]), clock=clock,
                                           top_gainer_symbols=live_top10)
    else:
        empty: dict[str, Any] = {"opportunities": (), "decisions": (), "counts": {}, "feature_unavailable": (), "fetch_failures": ()}
        frame = {"issued_at": clock(), "evaluation_at": now, "inputs": (), "control": empty,
                 "challenger": empty, "seeds": (), "positioning": {}}
    evaluated_features = {
        i.id.symbol: {
            "relative_6h_pp": latest[i.id.symbol]["features"]["relative_6h_pp"],
            "turnover_acceleration": latest[i.id.symbol]["features"]["turnover_acceleration"],
        }
        for i in pool
        if accrual_open
        and i.id.symbol in latest
        and "relative_6h_pp" in latest[i.id.symbol].get("features", {})
        and "turnover_acceleration" in latest[i.id.symbol].get("features", {})
    } if accrual_open else {}
    unavailable_syms: set[str] = set()
    for arm in ("control", "challenger"):
        unavailable_syms |= set(frame[arm].get("feature_unavailable", ())) | set(frame[arm].get("fetch_failures", ()))
    if frame.get("global_input_unavailable"):
        unavailable_syms |= {i.id.symbol for i in pool}
    for call in frame["inputs"]:
        inst_arg = call.arguments.get("instrument") if isinstance(call.arguments, dict) else None
        inst_sym = getattr(getattr(inst_arg, "id", None), "symbol", None)
        if inst_sym and (
            call.error
            or (
                call.method == "derivatives_snapshot"
                and (call.result is None or getattr(call.result, "degraded_telemetry", False))
            )
        ):
            unavailable_syms.add(str(inst_sym))
    frame.update(decision_id=decision_id, study_id=definition["study_id"], policy=dict(ROLLING_POLICY),
                 pool=tuple(i.id.symbol for i in pool), scout_observations=tuple(observations),
                 scout_inputs=scout_inputs, benchmark_scout=tuple(btc),
                 source_quotes=tuple(source.current_quotes.values()) if accrual_open else (),
                 qualification_admission=tuple(qualification),
                 evaluated_features=evaluated_features,
                 unavailable_symbols=tuple(sorted(unavailable_syms)),
                 scout_counts={"selected": len(selected), "available": sum(o.get("available") is True for o in observations),
                               "ready": len(ready), "qualified_capacity": slots, "deferred": max(0, len(ready) - len(pool)),
                               "unknown": sum(o.get("available") is not True for o in observations),
                               "deep_input_unavailable": any(call.error or (call.method == "derivatives_snapshot" and
                                   (call.result is None or call.result.degraded_telemetry)) for call in frame["inputs"])})
    return state.admit(frame["seeds"], frame, total_capacity=ROLLING_POLICY["study_episode_limit"])


async def verify_discovery_frame(frame: dict[str, Any], configuration: dict[str, Any]) -> None:
    """Reproduce paired outputs from the decision's memoized point-in-time inputs."""
    if frame["policy"] != ROLLING_POLICY or frame["issued_at"] < frame["evaluation_at"]:
        raise ValueError("rolling replay policy/chronology mismatch")
    if not frame["pool"]:
        if frame["seeds"]:
            raise ValueError("rolling admission without evaluated pool")
        return
    for call in frame["inputs"]:
        if call.error:
            continue
        if call.method == "candles" and any(not c.closed or c.closed_at > frame["evaluation_at"] for c in call.result):
            raise ValueError("rolling replay future candle")
        if call.method in ("quotes", "derivatives_snapshot"):
            values = call.result if call.method == "quotes" else (call.result,)
            if any(value is not None and value.observed_at > frame["issued_at"] + timedelta(seconds=5) for value in values):
                raise ValueError("rolling replay future telemetry")
    pool = set(frame["pool"])
    source_quotes = frame.get("source_quotes", ())
    liquid_sq = [
        (q.price_change_pct_24h, q.instrument_id.symbol)
        for q in source_quotes
        if q.price_change_pct_24h is not None and q.quote_volume_24h >= Decimal("500000")
    ]
    if len(liquid_sq) >= 20:
        ranked_sq = sorted(liquid_sq, reverse=True)
        live_top10 = frozenset(sym for chg, sym in ranked_sq[:10] if chg is not None and chg >= Decimal("10.0"))
    else:
        live_top10 = frozenset()
    for recovery in (False, True):
        market = ReplayMarket(SnapshotTape(frame["inputs"]))
        journal = ScanDecisionJournal()
        screener = engine(Settings.model_construct(**configuration), market, frame["evaluation_at"], recovery,
                          decision_at=frame["issued_at"], journal=journal)
        result = await screener.screen(market, market, max_symbols=320, target_direction=Direction.LONG,
                                       enforce_quality=True, top_gainer_symbols=live_top10)
        missing = screener._fetch_failures | screener._feature_unavailable
        opportunities = tuple(o.as_dict() for o in result if o.instrument.id.symbol in pool
                              and o.instrument.id.symbol not in missing and not frame.get("global_input_unavailable"))
        expected = frame["challenger" if recovery else "control"]
        if (serialize(opportunities) != serialize(expected["opportunities"])
                or serialize(journal.snapshot()) != serialize(expected["decisions"])
                or screener.scan_counts != expected["counts"]):
            raise ValueError("rolling paired replay mismatch")
        if recovery:
            seeds = tuple(recovery_seed(o, frame["issued_at"], frame["inputs"]) for o in result if o.instrument.id.symbol in pool and o.instrument.id.symbol not in missing
                          and not frame.get("global_input_unavailable")
                          and o.setup_label is SetupLabel.RECOVERY_RECLAIM_LONG)
            if serialize(seeds) != serialize(frame["seeds"]):
                raise ValueError("rolling admission replay mismatch")
