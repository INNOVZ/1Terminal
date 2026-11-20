"""Bounded public-data refresh for opted-in research recovery episodes."""
from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from algotrader.application.recovery_screener import RecoveryScreener
from algotrader.domain.crypto_screener_models import CryptoOpportunity, SetupLabel
from algotrader.domain.models import Candle, DerivativesSnapshot, Quote
from algotrader.domain.ports import DerivativesDataPort, MarketDataPort
from algotrader.domain.recovery_episodes import RecoveryEpisode, advance_episode, require_aware
from algotrader.infrastructure.recovery_episodes import RecoveryEpisodeStore


class RecoveryEpisodeService:
    def __init__(self, store: RecoveryEpisodeStore) -> None:
        self.store = store
        self._lock = asyncio.Lock()

    async def scan_and_enroll(
        self, engine: RecoveryScreener, market: MarketDataPort,
        derivatives: DerivativesDataPort, *, at: datetime | None = None, **scan_options: Any,
    ) -> tuple[list[CryptoOpportunity], dict[str, str]]:
        """Explicit research entry point. Existing core results are also returned."""
        candidates = await engine.screen(market, derivatives, **scan_options)
        recovery = [c for c in candidates if c.setup_label is SetupLabel.RECOVERY_RECLAIM_LONG]
        return candidates, self.store.enroll(recovery, at or datetime.now(UTC))

    async def refresh(
        self, market: MarketDataPort, derivatives: DerivativesDataPort, *, at: datetime | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> tuple[RecoveryEpisode, ...]:
        if at is not None:
            require_aware(at)
        async with self._lock:
            result = []
            episodes = self.store.episodes(active_only=True)
            if at is not None and any(at < episode.checked_at for episode in episodes):
                raise ValueError("out-of-order recovery refresh")
            pending = [e for e in episodes if at != e.checked_at]
            quotes: Sequence[Quote] = ()
            quote_error: str | None = None
            if pending:
                try:
                    fetched_quotes = await asyncio.wait_for(market.quotes([e.instrument for e in pending]), 5)
                    for item in fetched_quotes:
                        require_aware(item.observed_at)
                    quotes = fetched_quotes
                except Exception as exc:
                    quote_error = type(exc).__name__
            for episode in episodes:
                started_at = at or clock()
                if started_at < episode.checked_at:
                    raise ValueError("out-of-order recovery refresh")
                if started_at == episode.checked_at:
                    result.append(episode)
                    continue
                # Four baseline bars plus every unseen interval, bounded to one day.
                unseen = math.ceil((started_at - episode.last_bar_open).total_seconds() / 900)
                limit = min(100, max(5, unseen + 4))
                errors: dict[str, str] = {}
                if quote_error:
                    errors["quote"] = quote_error
                bars: Sequence[Candle] = ()
                matching = [q for q in quotes if q.instrument_id == episode.instrument.id]
                quote = matching[0] if len(matching) == 1 else None
                flow: DerivativesSnapshot | None = None
                # Serial symbol requests keep memory and concurrent provider load bounded.
                try:
                    fetched = await asyncio.wait_for(market.candles(episode.instrument, "15m", limit), 5)
                    if len(fetched) > limit:
                        raise ValueError("provider exceeded recovery candle limit")
                    for candle in fetched:
                        require_aware(candle.opened_at)
                        require_aware(candle.closed_at)
                    bars = fetched
                except Exception as exc:
                    errors["candles"] = type(exc).__name__
                if episode.triggered_at is None:
                    try:
                        fetched_flow = await asyncio.wait_for(derivatives.derivatives_snapshot(episode.instrument), 5)
                        if fetched_flow is not None:
                            require_aware(fetched_flow.observed_at)
                        flow = fetched_flow
                    except Exception as exc:
                        errors["derivatives"] = type(exc).__name__
                observed_at = at or clock()
                evidence = {"kind": "refresh", "at": observed_at, "bars": tuple(bars),
                            "quote": quote, "derivatives": flow, "errors": errors}
                try:
                    state = advance_episode(episode, at=observed_at, bars=bars, quote=quote, derivatives=flow)
                except ValueError as exc:
                    # Keep the watermark so a later complete fetch can repair coverage.
                    state = replace(episode, checked_at=observed_at, revision=episode.revision + 1,
                                    reason="invalid_observation", coverage_complete=False)
                    evidence["validation_error"] = str(exc)
                self.store.commit(episode, state, evidence)
                result.append(state)
            return tuple(result)
