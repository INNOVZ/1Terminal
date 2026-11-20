"""Bounded Top-Gainer Short Watch screener and loop (strictly isolated from long/buy pipeline)."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
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
        super().__init__(
            settings,
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
        now = (evaluated_at or datetime.now(UTC)).astimezone(UTC)
        instruments = list(await market.instruments())
        quotes_list = list(await market.quotes(instruments))
        quotes_by_symbol = {q.instrument_id.symbol: q for q in quotes_list}

        active = self.store.active_episodes()
        active_by_symbol = {ep.symbol: ep for ep in active}
        pinned_symbols = [ep.symbol for ep in active]

        selected = select_top_gainer_instruments(
            instruments,
            quotes_by_symbol,
            self.policy,
            pinned_symbols=pinned_symbols,
        )

        # Also preserve BTCUSDT if available so benchmark beta/residual can compute cleanly
        btc_inst = next(
            (i for i in instruments if i.base_asset == "BTC" and i.quote_asset == "USDT" and i.trading),
            None,
        )
        subset_instruments = list(selected)
        if btc_inst is not None and all(i.id != btc_inst.id for i in subset_instruments):
            subset_instruments.append(btc_inst)

        journal = ScanDecisionJournal()
        screener = TopGainerDistributionScreener(
            self.settings,
            macro_provider=self.macro_provider,
            evaluated_at=now,
            journal=journal,
            watch_policy=self.policy,
            pinned_symbols=pinned_symbols,
        )
        subset_market = _SubsetMarket(market, subset_instruments, quotes_by_symbol)
        if macro_context is not None and self.macro_provider is None:
            fixed_macro = macro_context

            class _FixedMacro:
                async def get_context(self) -> MacroSentimentContext:
                    return fixed_macro

            screener._macro_provider = _FixedMacro()

        opportunities = await screener.screen(
            subset_market,
            derivatives_port=derivatives_port,
            min_score=Decimal("0"),
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

        cycle_summary = {
            "cycle_id": str(uuid4()),
            "evaluated_at": now.isoformat(),
            "policy_version": self.policy.version,
            "watched_input_count": len(selected),
            "watching_count": sum(1 for e in updated_episodes if e.state == TopGainerWatchState.WATCHING),
            "confirmed_short_count": sum(1 for e in updated_episodes if e.state == TopGainerWatchState.CONFIRMED_SHORT),
            "invalidated_count": sum(1 for e in updated_episodes if e.state == TopGainerWatchState.INVALIDATED),
            "expired_count": sum(1 for e in updated_episodes if e.state == TopGainerWatchState.EXPIRED),
            "symbols": [e.symbol for e in updated_episodes],
        }
        self.store.save_cycle(cycle_summary, updated_episodes)
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        episodes = self.store.list_episodes(limit=100)
        watching = [e.as_dict() for e in episodes if e.state == TopGainerWatchState.WATCHING]
        confirmed = [e.as_dict() for e in episodes if e.state == TopGainerWatchState.CONFIRMED_SHORT]
        invalidated = [e.as_dict() for e in episodes if e.state == TopGainerWatchState.INVALIDATED][:25]
        expired = [e.as_dict() for e in episodes if e.state == TopGainerWatchState.EXPIRED][:25]
        return {
            "policy_version": self.policy.version,
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
