"""Bounded Top-Gainer Short Watch screener and loop (strictly isolated from long/buy pipeline)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from algotrader.adapters.external.models import MacroSentimentContext
from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.application.scan_recording import MacroContextSource
from algotrader.config import Settings
from algotrader.domain.crypto_screener_models import CryptoOpportunity, ScreenerMode
from algotrader.domain.models import Direction, Instrument, Quote
from algotrader.domain.ports import DerivativesDataPort, MarketDataPort
from algotrader.domain.scan_observations import ScanDecisionJournal
from algotrader.domain.top_gainer_short_watch import (
    TopGainerWatchEpisode,
    TopGainerWatchPolicy,
    TopGainerWatchState,
    advance_top_gainer_watch_episode,
    extract_short_diagnostic,
    select_top_gainer_instruments,
)
from algotrader.infrastructure.top_gainer_short_watch import TopGainerShortWatchStore


class TopGainerDistributionScreener(CryptoOpportunityScreener):
    """Short-only distribution screener that pins rolling top-N 24h gainers in Stage 0."""

    def __init__(
        self,
        settings: Settings,
        macro_provider: MacroContextSource | None = None,
        *,
        evaluated_at: datetime | None = None,
        journal: ScanDecisionJournal | None = None,
        watch_policy: TopGainerWatchPolicy | None = None,
        pinned_symbols: Sequence[str] = (),
    ) -> None:
        # Keep this separate top-gainer reversal feed outside continuation composition.
        isolated = settings.model_copy(
            update={"bearish_continuation_enabled": False, "early_recovery_enabled": False}
        )
        super().__init__(
            isolated,
            macro_provider=macro_provider,
            evaluated_at=evaluated_at,
            journal=journal,
            feature_contract="discovery_v2",
        )
        self._watch_policy = watch_policy or TopGainerWatchPolicy()
        self._pinned_symbols = tuple(pinned_symbols)

    async def screen(self, *args: Any, **kwargs: Any) -> list[CryptoOpportunity]:
        if kwargs.get("target_direction") not in (None, Direction.SHORT):
            raise ValueError("TopGainerDistributionScreener is short-only")
        kwargs["target_direction"] = Direction.SHORT
        kwargs["mode"] = ScreenerMode.DISTRIBUTION
        return await super().screen(*args, **kwargs)

    def _extend_selection(
        self,
        selected: list[Instrument],
        liquid: list[Instrument],
        quotes: dict[str, Quote],
        max_symbols: int | None,
    ) -> list[Instrument]:
        """Stage-0 hook: pin active watched gainers and rolling top-N 24h gainers."""
        pinned = select_top_gainer_instruments(
            liquid,
            quotes,
            self._watch_policy,
            pinned_symbols=self._pinned_symbols,
        )
        seen = {inst.id for inst in selected}
        merged = list(selected)
        for inst in pinned:
            if inst.id not in seen:
                merged.append(inst)
                seen.add(inst.id)
        return merged


class _SubsetMarket:
    """MarketDataPort wrapper restricting Stage-0 evaluation to pinned top gainers."""

    def __init__(
        self,
        inner: MarketDataPort,
        selected: Sequence[Instrument],
        quotes_by_symbol: dict[str, Quote],
    ) -> None:
        self._inner = inner
        self._selected = tuple(selected)
        self._quotes_by_symbol = quotes_by_symbol
        self.venue = getattr(inner, "venue", None)

    async def instruments(self) -> Sequence[Instrument]:
        return self._selected

    async def quotes(self, instruments: Sequence[Instrument]) -> Sequence[Quote]:
        out: list[Quote] = []
        missing: list[Instrument] = []
        for inst in instruments:
            q = self._quotes_by_symbol.get(inst.id.symbol)
            if q is not None:
                out.append(q)
            else:
                missing.append(inst)
        if missing:
            out.extend(await self._inner.quotes(missing))
        return out

    async def candles(self, instrument: Instrument, interval: str, limit: int) -> Any:
        return await self._inner.candles(instrument, interval, limit)


class TopGainerShortWatchService:
    """Bounded watch loop tracking rolling top-24h gainers into confirmed shorts."""

    def __init__(
        self,
        store: TopGainerShortWatchStore,
        settings: Settings,
        *,
        policy: TopGainerWatchPolicy | None = None,
        macro_provider: MacroContextSource | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.policy = policy or TopGainerWatchPolicy()
        self.macro_provider = macro_provider

    async def run_cycle(
        self,
        market: MarketDataPort,
        *,
        derivatives_port: DerivativesDataPort | None = None,
        evaluated_at: datetime | None = None,
        macro_context: MacroSentimentContext | None = None,
    ) -> dict[str, Any]:
        timestamp = evaluated_at or datetime.now(UTC)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        now = timestamp.astimezone(UTC)
        try:
            return await self._run_cycle(
                market,
                derivatives_port=derivatives_port,
                evaluated_at=now,
                macro_context=macro_context,
            )
        except Exception as exc:
            episodes = [
                replace(
                    ep,
                    state=(
                        TopGainerWatchState.EXPIRED
                        if now - ep.enrolled_at >= timedelta(hours=self.policy.max_watch_hours)
                        else TopGainerWatchState.WATCHING
                    ),
                    gates_passed=False,
                    watch_tier="data_unavailable",
                    latest_blocker="cycle_failed",
                    invalidation_reason=(
                        "watch_horizon_exceeded"
                        if now - ep.enrolled_at >= timedelta(hours=self.policy.max_watch_hours)
                        else None
                    ),
                    policy_version=self.policy.version,
                )
                for ep in self.store.active_episodes()
            ]
            self.store.save_cycle(
                {
                    "cycle_id": str(uuid4()),
                    "evaluated_at": now.isoformat(),
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "policy_version": self.policy.version,
                },
                episodes,
            )
            raise

    async def _run_cycle(
        self,
        market: MarketDataPort,
        *,
        derivatives_port: DerivativesDataPort | None = None,
        evaluated_at: datetime | None = None,
        macro_context: MacroSentimentContext | None = None,
    ) -> dict[str, Any]:
        timestamp = evaluated_at or datetime.now(UTC)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        now = timestamp.astimezone(UTC)
        instruments = list(await market.instruments())
        quotes_list = list(await market.quotes(instruments))
        quotes_by_symbol = {
            q.instrument_id.symbol: q
            for q in quotes_list
            if q.observed_at.tzinfo is not None
            and q.is_valid_book
            and q.is_fresh_at(now, max_age_seconds=300.0, max_future_seconds=60.0)
        }

        active = self.store.active_episodes()
        active_by_symbol = {ep.symbol: ep for ep in active}
        pinned_symbols = [ep.symbol for ep in active]

        selected = select_top_gainer_instruments(
            instruments,
            quotes_by_symbol,
            self.policy,
            pinned_symbols=pinned_symbols,
        )

        # Unavailable active watches still own capacity until terminal. Otherwise each
        # outage could admit another full batch and defeat the bounded state contract.
        new_capacity = max(0, self.policy.max_active_watches - len(active))
        admitted: list[Instrument] = []
        for inst in selected:
            if inst.id.symbol in active_by_symbol:
                admitted.append(inst)
            elif new_capacity > 0:
                admitted.append(inst)
                new_capacity -= 1
        selected = tuple(admitted)

        # Also preserve BTCUSDT if available so benchmark beta/residual can compute cleanly
        btc_inst = next(
            (
                i
                for i in instruments
                if i.base_asset == "BTC" and i.quote_asset == "USDT" and i.trading
            ),
            None,
        )
        subset_instruments = list(selected)
        if btc_inst is not None and all(i.id != btc_inst.id for i in subset_instruments):
            subset_instruments.append(btc_inst)

        journal = ScanDecisionJournal()
        macro_provider = self.macro_provider
        if macro_context is not None:
            fixed_macro = macro_context

            class _FixedMacro:
                async def get_context(self) -> MacroSentimentContext:
                    return fixed_macro

            macro_provider = _FixedMacro()
        if macro_provider is None:
            raise ValueError(
                "short watch requires a shared macro provider or explicit macro context"
            )
        screener = TopGainerDistributionScreener(
            self.settings,
            macro_provider=macro_provider,
            evaluated_at=now,
            journal=journal,
            watch_policy=self.policy,
            pinned_symbols=pinned_symbols,
        )
        subset_market = _SubsetMarket(market, subset_instruments, quotes_by_symbol)

        opportunities = await screener.screen(
            subset_market,
            derivatives_port=derivatives_port,
            min_score=Decimal("0"),
            enforce_quality=True,
            max_symbols=max(len(subset_instruments), 1),
            mode=ScreenerMode.DISTRIBUTION,
            target_direction=Direction.SHORT,
        )
        opps_by_symbol = {o.instrument.id.symbol: o for o in opportunities}
        events = journal.snapshot()

        updated_episodes: list[TopGainerWatchEpisode] = []
        for inst in selected:
            sym = inst.id.symbol
            q = quotes_by_symbol.get(sym)
            if q is None:
                continue
            opp = opps_by_symbol.get(sym)
            diag = extract_short_diagnostic(sym, events, opp)
            next_ep = advance_top_gainer_watch_episode(
                active_by_symbol.get(sym),
                symbol=sym,
                base_asset=inst.base_asset,
                quote=q,
                diagnostic=diag,
                opportunity=opp,
                evaluated_at=now,
                policy=self.policy,
            )
            updated_episodes.append(next_ep)

        evaluated_symbols = {e.symbol for e in updated_episodes}
        for previous in active:
            if previous.symbol in evaluated_symbols:
                continue
            expired = now - previous.enrolled_at >= timedelta(hours=self.policy.max_watch_hours)
            updated_episodes.append(
                replace(
                    previous,
                    updated_at=now,
                    gates_passed=False,
                    checklist_passed=0,
                    state=TopGainerWatchState.EXPIRED if expired else TopGainerWatchState.WATCHING,
                    watch_tier="expired" if expired else "data_unavailable",
                    latest_blocker="market_data_or_eligibility_unavailable",
                    invalidation_reason="watch_horizon_exceeded" if expired else None,
                    policy_version=self.policy.version,
                )
            )

        cycle_summary = {
            "cycle_id": str(uuid4()),
            "evaluated_at": now.isoformat(),
            "policy_version": self.policy.version,
            "status": "degraded"
            if any(e.symbol not in evaluated_symbols for e in active)
            else "completed",
            "unavailable_active_count": sum(e.symbol not in evaluated_symbols for e in active),
            "invalid_quote_count": len(quotes_list) - len(quotes_by_symbol),
            "watched_input_count": len(selected),
            "watching_count": sum(
                1 for e in updated_episodes if e.state == TopGainerWatchState.WATCHING
            ),
            "confirmed_short_count": sum(
                1 for e in updated_episodes if e.state == TopGainerWatchState.CONFIRMED_SHORT
            ),
            "invalidated_count": sum(
                1 for e in updated_episodes if e.state == TopGainerWatchState.INVALIDATED
            ),
            "expired_count": sum(
                1 for e in updated_episodes if e.state == TopGainerWatchState.EXPIRED
            ),
            "symbols": [e.symbol for e in updated_episodes],
        }
        self.store.save_cycle(cycle_summary, updated_episodes)
        return self.snapshot(evaluated_at=now)

    def snapshot(self, *, evaluated_at: datetime | None = None) -> dict[str, Any]:
        now = evaluated_at or datetime.now(UTC)
        # Active episodes must not disappear behind a page of terminal history.
        episodes = list(
            {
                e.episode_id: e
                for e in (*self.store.list_episodes(limit=100), *self.store.active_episodes())
            }.values()
        )
        freshness = timedelta(hours=self.settings.screener_interval_hours, minutes=60)

        def current(e: TopGainerWatchEpisode) -> bool:
            return (
                e.policy_version == self.policy.version
                and e.gates_passed
                and e.checklist_passed >= self.policy.confirmed_checklist_required
                and e.confirmed_opportunity is not None
                and e.latest_blocker is None
                and timedelta(0) <= now - e.updated_at <= freshness
            )

        watching = [
            e.as_dict()
            for e in episodes
            if e.state == TopGainerWatchState.WATCHING
            or (e.state == TopGainerWatchState.CONFIRMED_SHORT and not current(e))
        ]
        for item in watching:
            item["current_quality_verified"] = False
            if item["state"] == TopGainerWatchState.CONFIRMED_SHORT.value:
                item.update(
                    state="watching",
                    watch_tier="reverification_required",
                    latest_blocker="stale_or_legacy_confirmation",
                )
        confirmed = [
            e.as_dict()
            for e in episodes
            if e.state == TopGainerWatchState.CONFIRMED_SHORT and current(e)
        ]
        for item in confirmed:
            item["current_quality_verified"] = True
        invalidated = [e.as_dict() for e in episodes if e.state == TopGainerWatchState.INVALIDATED][
            :25
        ]
        expired = [e.as_dict() for e in episodes if e.state == TopGainerWatchState.EXPIRED][:25]
        return {
            "policy_version": self.policy.version,
            "snapshot_at": now.isoformat(),
            "confirmation_freshness_limit_seconds": int(freshness.total_seconds()),
            "latest_cycle": self.store.latest_cycle(),
            "counts": {
                "watching": len(watching),
                "confirmed_short": len(confirmed),
                "invalidated": len(invalidated),
                "expired": len(expired),
            },
            "confirmed_shorts": confirmed,
            "watching": watching,
            "invalidated": invalidated,
            "expired": expired,
        }
