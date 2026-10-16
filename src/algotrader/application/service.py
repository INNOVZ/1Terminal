from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import cast
from uuid import uuid4

from algotrader.application.execution import ExecutionCoordinator, ExecutionReport
from algotrader.application.manage import PositionManagementCoordinator
from algotrader.application.reconciliation import PositionReconciliationService
from algotrader.application.research import candidate_observation
from algotrader.application.screener import MultiFactorScreener
from algotrader.application.strategy import TrendMomentumStrategy
from algotrader.config import FOUR_HOUR_DECISION_PROFILES, Settings, StrategyProfile
from algotrader.domain.crypto_screener_models import CryptoOpportunity, ScreenerMode
from algotrader.domain.errors import SafetyInterlockError, TradeRejected, VenueError
from algotrader.domain.models import (
    AccountSnapshot,
    Candidate,
    Direction,
    ExecutionLifecycle,
    ExecutionStage,
    InstrumentId,
    MarketKind,
    MomentumState,
    OrderStatus,
    Position,
    PositionManagementState,
    TrackedSignal,
    TradeRecord,
    TradeSignal,
    Venue,
)
from algotrader.domain.ports import (
    DerivativesDataPort,
    ExecutionPort,
    MarketDataPort,
    StateRepository,
)


@dataclass(frozen=True, slots=True)
class VenueContext:
    market_data: MarketDataPort
    execution: ExecutionPort
    coordinator: ExecutionCoordinator
    management: PositionManagementCoordinator | None = None


class CycleReason(StrEnum):
    EXECUTED = "executed"
    ALREADY_RUNNING = "already_running"
    LEASE_UNAVAILABLE = "lease_unavailable"
    UNRESOLVED_ORDER = "unresolved_order"
    INCOMPLETE_EXECUTION = "incomplete_execution"
    KILL_SWITCH = "kill_switch"
    POSITION_RECONCILIATION = "position_reconciliation"
    COOLDOWN = "cooldown"
    NO_CANDIDATE = "no_candidate"
    REVALIDATION_FAILED = "revalidation_failed"


@dataclass(frozen=True, slots=True)
class CycleResult:
    reason: CycleReason
    report: ExecutionReport | None = None
    candidate_count: int = 0
    detail: str = ""
    next_eligible_at: datetime | None = None

    @property
    def executed(self) -> bool:
        return self.report is not None


class TradingSystem:
    def __init__(
        self,
        contexts: tuple[VenueContext, ...],
        screener: MultiFactorScreener,
        strategy: TrendMomentumStrategy,
        repository: StateRepository,
        execution_cooldown_hours: int = 8,
        signal_expiry_bars: int = 2,
        execution_lease_seconds: int = 300,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._contexts = contexts
        self._screener = screener
        self._strategy = strategy
        self._repository = repository
        self._execution_cooldown = timedelta(hours=execution_cooldown_hours)
        profile = getattr(screener, "strategy_profile", StrategyProfile.LEGACY_MOMENTUM.value)
        four_hour_profiles = frozenset(item.value for item in FOUR_HOUR_DECISION_PROFILES)
        bar_hours = 4 if profile in four_hour_profiles else 1
        self._signal_expiry = timedelta(hours=signal_expiry_bars * bar_hours)
        self._execution_lease = timedelta(seconds=execution_lease_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._position_reconciliation = PositionReconciliationService(
            repository,
            {context.market_data.venue: context.execution for context in contexts},
            self._utc_now,
        )
        self._lease_owner_id = uuid4().hex
        self._cycle_lock = asyncio.Lock()

    async def status(self) -> dict[str, object]:
        now = self._utc_now()
        open_trades = await self._repository.list_open_trades()
        active_signals = await self._repository.list_active_signals(now)
        kill_switch = await self._repository.get_state("kill_switch") or {}
        cooldown_remaining = await self._cooldown_remaining(now)
        return {
            "kill_switch": bool(kill_switch.get("active")),
            "kill_switch_reason": str(kill_switch.get("reason", "")),
            "strategy_profile": self._screener.strategy_profile,
            "venues": [context.market_data.venue.value for context in self._contexts],
            "cycle_running": self._cycle_lock.locked(),
            "tracked_open_trades": len(open_trades),
            "active_signals": len(active_signals),
            "execution_cooldown_remaining_seconds": int(cooldown_remaining.total_seconds()),
            "next_execution_eligible_at": (
                (now + cooldown_remaining).isoformat()
                if cooldown_remaining > timedelta(0)
                else now.isoformat()
            ),
        }

    async def screen(self) -> list[Candidate]:
        batches = await asyncio.gather(
            *(self._screener.screen(context.market_data) for context in self._contexts)
        )
        return self._rank([candidate for batch in batches for candidate in batch])

    async def alt_momentum_leaderboard(self, *, limit: int = 15) -> dict[str, object]:
        """Return the observation-only USD-M altcoin momentum leaderboard."""

        futures = next(
            (
                context.market_data
                for context in self._contexts
                if context.market_data.venue is Venue.BINANCE_USDM
            ),
            None,
        )
        if futures is None:
            raise VenueError("USD-M market data is unavailable")
        return await self._screener.alt_momentum_leaderboard(futures, limit=limit)

    async def crypto_screen(
        self,
        *,
        settings: Settings | None = None,
        min_score: Decimal | None = None,
        target_direction: Direction | None = None,
        top_n: int = 30,
        max_symbols: int | None = None,
        mode: ScreenerMode = ScreenerMode.ACCUMULATION,
    ) -> list[CryptoOpportunity]:
        """Screen the USD-M crypto universe using multi-factor regime, derivatives, and macro analysis."""
        from algotrader.application.crypto_screener import CryptoOpportunityScreener
        from algotrader.config import load_settings

        futures = next(
            (
                context.market_data
                for context in self._contexts
                if context.market_data.venue is Venue.BINANCE_USDM
            ),
            None,
        )
        if futures is None and self._contexts:
            futures = self._contexts[0].market_data
        if futures is None:
            raise VenueError("No market data context is available for crypto screening")

        effective_settings = (
            settings
            if settings is not None
            else getattr(self._screener, "_settings", None)
        )
        if effective_settings is None:
            effective_settings = load_settings()

        screener = CryptoOpportunityScreener(effective_settings)
        derivatives_port = (
            cast(DerivativesDataPort, futures)
            if hasattr(futures, "derivatives_snapshot")
            else None
        )
        return await screener.screen(
            futures,
            derivatives_port=derivatives_port,
            min_score=min_score,
            target_direction=target_direction,
            top_n=top_n,
            max_symbols=max_symbols,
            mode=mode,
        )

    def _rank(self, candidates: list[Candidate]) -> list[Candidate]:
        """Stamp expiry, collapse cross-venue duplicates, and order by score.

        Every path that surfaces tradable candidates goes through here, so an operator
        reading `screen` sees exactly the objects the trading cycle would act on -- including
        the two-bar expiry, without which a candidate looks valid indefinitely.
        """
        ranked = self._deduplicate_across_venues(
            [self._with_expiry(candidate) for candidate in candidates]
        )
        ranked.sort(key=lambda candidate: candidate.score, reverse=True)
        return ranked

    @staticmethod
    def _deduplicate_across_venues(candidates: list[Candidate]) -> list[Candidate]:
        """Collapse one economic opportunity to one candidate.

        Most majors are listed on both Spot and USD-M, and Spot is long-only. Merging the
        venue batches naively therefore gives every dual-listed coin *two* long candidates
        and one short, so longs get two chances at the top of a globally sorted ranking
        while shorts get one -- a selection bias with no basis in the market.

        Deduplication is keyed on (base asset, direction) so short candidates are never
        displaced by a long. Ties prefer the futures listing, which can express both
        directions and so keeps the instrument eligible if the trend later inverts.
        """
        best: dict[tuple[str, Direction], Candidate] = {}
        for candidate in candidates:
            key = (candidate.instrument.base_asset, candidate.direction)
            incumbent = best.get(key)
            if incumbent is None or TradingSystem._preferred(candidate, incumbent):
                best[key] = candidate
        return list(best.values())

    @staticmethod
    def _preferred(candidate: Candidate, incumbent: Candidate) -> bool:
        candidate_futures = candidate.instrument.market_kind is MarketKind.FUTURES
        incumbent_futures = incumbent.instrument.market_kind is MarketKind.FUTURES
        if candidate_futures is not incumbent_futures:
            return candidate_futures
        return candidate.score > incumbent.score

    async def screen_with_diagnostics(self) -> tuple[list[Candidate], dict[str, object]]:
        """Eligible candidates and the funnel that produced them, from a single pass.

        Callers that want both must not screen twice: a full pass is ~160 venue requests and
        there is no rate-limit backoff, so a second one doubles the exposure to a 429.
        """
        evaluated = await self._evaluate_universe()
        eligible = self._rank(
            [item for item in evaluated if item.score >= self._screener_minimum()]
        )
        return eligible, self._summarize(evaluated)

    async def screen_diagnostics(self) -> dict[str, object]:
        """Why the universe produced the directions it did, per direction.

        Answers "why are there no shorts?" with counts instead of intuition. Spot is
        long-only by construction, so the venue split is reported separately: a long/short
        imbalance sourced from venue coverage is a very different problem from one sourced
        from market regime or a specific gate.
        """
        return self._summarize(await self._evaluate_universe())

    async def _evaluate_universe(self) -> list[Candidate]:
        batches = await asyncio.gather(
            *(self._screen_all(context.market_data) for context in self._contexts)
        )
        return [candidate for batch in batches for candidate in batch]

    async def _screen_all(self, market_data: MarketDataPort) -> list[Candidate]:
        """Compatibility boundary for simple test/plugin screeners without diagnostics."""
        if "include_rejected" in inspect.signature(self._screener.screen).parameters:
            return await self._screener.screen(market_data, include_rejected=True)
        return await self._screener.screen(market_data)

    def _screener_minimum(self) -> Decimal:
        return getattr(self._screener, "minimum_score", Decimal("0"))

    def _summarize(self, evaluated: list[Candidate]) -> dict[str, object]:
        summary: dict[str, object] = {}
        for direction in (Direction.LONG, Direction.SHORT):
            subset = [item for item in evaluated if item.direction is direction]
            eligible = [item for item in subset if item.score > 0]
            blocked: dict[str, int] = {}
            for candidate in subset:
                for reason in candidate.reasons:
                    if not reason.startswith("entry blocked: "):
                        continue
                    message = reason.removeprefix("entry blocked: ")
                    blocked[message] = blocked.get(message, 0) + 1
            summary[direction.value] = {
                "evaluated": len(subset),
                "eligible": len(eligible),
                "blocking_reasons": dict(
                    sorted(blocked.items(), key=lambda item: item[1], reverse=True)
                ),
            }
        summary["evaluations_by_venue"] = {
            venue: {
                "evaluated": sum(
                    1 for item in evaluated if item.instrument.id.venue.value == venue
                ),
                "short_capable": any(
                    item.instrument.supports_short
                    for item in evaluated
                    if item.instrument.id.venue.value == venue
                ),
            }
            for venue in sorted({item.instrument.id.venue.value for item in evaluated})
        }
        return summary

    async def discover_signals(self) -> list[Candidate]:
        """Discover on every configured close and persist observations without trading."""
        now = self._utc_now()
        expired = await self._repository.expire_signals(now)
        if expired:
            await self._repository.append_event(
                "signals_expired", {"count": expired, "expired_at": now.isoformat()}
            )
        evaluated = await self._evaluate_universe()
        recorder = getattr(self._repository, "record_opportunities", None)
        if recorder is not None:
            observations = tuple(
                candidate_observation(candidate, self._screener_minimum())
                for candidate in evaluated
            )
            changed = await recorder(observations)
            if observations:
                await self._repository.append_event(
                    "research_opportunities_recorded",
                    {
                        "evaluated": len(observations),
                        "inserted_or_updated": changed,
                        "eligible": sum(item.eligible for item in observations),
                        "signal_bar_closed_at": max(
                            item.signal_bar_closed_at for item in observations
                        ).isoformat(),
                    },
                )
        candidates = self._rank(
            [item for item in evaluated if item.score >= self._screener_minimum()]
        )
        for candidate in candidates:
            if candidate.signal_bar_closed_at is None or candidate.expires_at is None:
                continue
            tracked = await self._repository.record_signal_observation(
                TrackedSignal(
                    signal_id=uuid4(),
                    instrument_id=candidate.instrument.id,
                    direction=candidate.direction,
                    first_seen_at=now,
                    last_seen_at=now,
                    signal_bar_closed_at=candidate.signal_bar_closed_at,
                    expires_at=candidate.expires_at,
                    observations=1,
                    score=candidate.score,
                    entry_price=candidate.entry_price,
                )
            )
            if tracked.observations == 1:
                await self._repository.append_event(
                    "signal_discovered",
                    {
                        "tracked_signal_id": str(tracked.signal_id),
                        "instrument": str(tracked.instrument_id),
                        "direction": tracked.direction.value,
                        "signal_bar_closed_at": tracked.signal_bar_closed_at.isoformat(),
                        "expires_at": tracked.expires_at.isoformat(),
                        "score": str(tracked.score),
                    },
                )
        return candidates

    async def run_cycle(self) -> CycleResult:
        if self._cycle_lock.locked():
            return CycleResult(CycleReason.ALREADY_RUNNING)
        async with self._cycle_lock:
            # Discovery is read-only with respect to the exchange and can safely run on
            # every instance. Acquire the durable lease only for the short order-authority
            # section so a long universe scan cannot outlive the lease.
            candidates = await self.discover_signals()
            now = self._utc_now()
            acquired = await self._repository.try_acquire_lease(
                "trading-cycle",
                self._lease_owner_id,
                now,
                now + self._execution_lease,
            )
            if not acquired:
                await self._repository.append_event(
                    "cycle_skipped_execution_lease", {"observed_at": now.isoformat()}
                )
                return CycleResult(
                    CycleReason.LEASE_UNAVAILABLE,
                    candidate_count=len(candidates),
                )
            try:
                return await self._run_cycle_with_lease(candidates)
            finally:
                await self._repository.release_lease("trading-cycle", self._lease_owner_id)

    async def _run_cycle_with_lease(self, candidates: list[Candidate]) -> CycleResult:
        if not await self._unresolved_order_intents_are_safe():
            return CycleResult(
                CycleReason.UNRESOLVED_ORDER,
                candidate_count=len(candidates),
                detail=await self._unresolved_intent_detail(),
            )
        if not await self._incomplete_execution_lifecycles_are_safe():
            return CycleResult(
                CycleReason.INCOMPLETE_EXECUTION,
                candidate_count=len(candidates),
                # A bare reason code sends the operator hunting through other commands for
                # something the cycle already knows. Name what is stuck, at the point it
                # blocks.
                detail=await self._incomplete_lifecycle_detail(),
            )
        if await self._repository.is_kill_switch_active():
            kill_switch = await self._repository.get_state("kill_switch") or {}
            return CycleResult(
                CycleReason.KILL_SWITCH,
                candidate_count=len(candidates),
                detail=str(kill_switch.get("reason", "")),
            )
        if not await self._tracked_positions_are_consistent():
            return CycleResult(
                CycleReason.POSITION_RECONCILIATION,
                candidate_count=len(candidates),
                detail=await self._position_mismatch_detail(),
            )
        # Manage what is already open before considering anything new. Protecting an
        # existing position outranks adding exposure, and a scale-out may free the capacity
        # a new entry needs.
        await self.manage_open_positions()
        now = self._utc_now()
        cooldown_remaining = await self._cooldown_remaining(now)
        if cooldown_remaining > timedelta(0):
            await self._repository.append_event(
                "execution_throttled",
                {
                    "remaining_seconds": int(cooldown_remaining.total_seconds()),
                    "active_candidates": len(candidates),
                },
            )
            cooldown_seconds = int(cooldown_remaining.total_seconds())
            return CycleResult(
                CycleReason.COOLDOWN,
                candidate_count=len(candidates),
                detail=f"execution cooldown has {cooldown_seconds} seconds remaining",
                next_eligible_at=now + cooldown_remaining,
            )
        if not candidates:
            await self._repository.append_event("cycle_no_candidate", {})
            return CycleResult(CycleReason.NO_CANDIDATE)
        # One new position per bar-close cycle limits correlated burst exposure.
        for candidate in candidates:
            try:
                revalidated = await self._revalidate(candidate)
                if revalidated is None:
                    await self._repository.append_event(
                        "candidate_failed_revalidation",
                        {
                            "instrument": str(candidate.instrument.id),
                            "direction": candidate.direction.value,
                        },
                    )
                    continue
                signal = self._strategy.signal(revalidated)
            except ValueError as exc:
                await self._repository.append_event(
                    "candidate_rejected_by_strategy",
                    {
                        "instrument": str(candidate.instrument.id),
                        "direction": candidate.direction.value,
                        "score": str(candidate.score),
                        "reason": str(exc),
                    },
                )
                continue
            context = self._context_for(revalidated.instrument.id.venue)
            await self._repository.append_event(
                "signal_selected",
                {
                    "signal_id": str(signal.signal_id),
                    "instrument": str(signal.instrument.id),
                    "direction": signal.direction.value,
                    "momentum": signal.momentum_state.value,
                    "score": str(signal.score),
                    "signal_bar_closed_at": (
                        signal.signal_bar_closed_at.isoformat()
                        if signal.signal_bar_closed_at
                        else None
                    ),
                    "expires_at": signal.expires_at.isoformat() if signal.expires_at else None,
                    "stop_price": str(signal.stop_price),
                    "target_price": str(signal.target_price),
                    "risk_per_unit": str(signal.risk_per_unit),
                    "reward_risk_ratio": str(signal.reward_risk_ratio),
                    "support": str(signal.support_price) if signal.support_price else None,
                    "resistance": (
                        str(signal.resistance_price) if signal.resistance_price else None
                    ),
                    "rationale": list(signal.rationale),
                },
            )
            try:
                report = await context.coordinator.execute(signal)
            except TradeRejected as exc:
                # Expected outcome, no venue state created: evaluate the next ranked
                # candidate instead of failing the cycle. Letting this propagate is what
                # allowed three unfilled IOC entries to trip the durable kill switch.
                await self._repository.append_event(
                    "candidate_not_traded",
                    {
                        "instrument": str(signal.instrument.id),
                        "direction": signal.direction.value,
                        "signal_id": str(signal.signal_id),
                        "reason": str(exc),
                    },
                )
                continue
            await self._repository.mark_signal_executed(
                signal.instrument.id, signal.direction, self._utc_now()
            )
            return CycleResult(
                CycleReason.EXECUTED,
                report=report,
                candidate_count=len(candidates),
            )
        await self._repository.append_event(
            "cycle_no_candidate",
            {"reason": "all ranked candidates failed fresh revalidation"},
        )
        return CycleResult(
            CycleReason.REVALIDATION_FAILED,
            candidate_count=len(candidates),
            detail="all ranked candidates failed fresh revalidation",
        )

    async def manage_open_positions(self) -> int:
        """Advance the exit ratchet on every open trade. Returns the number changed.

        Runs on every cycle regardless of cooldown or candidate availability: an open
        position needs managing whether or not the system wants a new one.
        """
        open_trades = tuple(await self._repository.list_open_trades())
        if not open_trades:
            return 0
        changed = 0
        for context in self._contexts:
            if context.management is None:
                continue
            venue = context.market_data.venue
            trades = [trade for trade in open_trades if trade.instrument_id.venue is venue]
            if not trades:
                continue
            instruments = {
                str(instrument.id): instrument
                for instrument in await context.market_data.instruments()
                if any(trade.instrument_id == instrument.id for trade in trades)
            }
            outcomes = await context.management.manage(trades, instruments)
            changed += len(outcomes)
        return changed

    async def active_signals(self) -> tuple[TrackedSignal, ...]:
        return tuple(await self._repository.list_active_signals(self._utc_now()))

    def _with_expiry(self, candidate: Candidate) -> Candidate:
        signal_bar_closed_at = candidate.signal_bar_closed_at or candidate.evaluated_at
        if signal_bar_closed_at.tzinfo is None:
            raise ValueError("signal bar close timestamp must be timezone-aware")
        expires_at = candidate.expires_at or signal_bar_closed_at + self._signal_expiry
        return replace(
            candidate,
            signal_bar_closed_at=signal_bar_closed_at.astimezone(UTC),
            expires_at=expires_at.astimezone(UTC),
        )

    async def _revalidate(self, candidate: Candidate) -> Candidate | None:
        context = self._context_for(candidate.instrument.id.venue)
        refreshed = await self._screener.screen_instrument(
            context.market_data, candidate.instrument
        )
        match = next(
            (item for item in refreshed if item.direction is candidate.direction),
            None,
        )
        if match is None:
            return None
        fresh = self._with_expiry(match)
        if fresh.expires_at is None or self._utc_now() >= fresh.expires_at:
            return None
        return fresh

    async def _cooldown_remaining(self, now: datetime) -> timedelta:
        last_opened_at = await self._repository.latest_trade_opened_at()
        if last_opened_at is None:
            return timedelta(0)
        eligible_at = last_opened_at.astimezone(UTC) + self._execution_cooldown
        return max(timedelta(0), eligible_at - now)

    def _utc_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("system clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    async def set_kill_switch(self, active: bool, reason: str) -> None:
        if not active:
            unresolved_safe = await self._unresolved_order_intents_are_safe()
            lifecycle_safe = await self._incomplete_execution_lifecycles_are_safe()
            positions_safe = await self._tracked_positions_are_consistent()
            if not (unresolved_safe and lifecycle_safe and positions_safe):
                raise SafetyInterlockError(
                    "kill switch cannot be cleared while reconciliation issues remain"
                )
        await self._repository.set_kill_switch(active, reason)

    async def reconcile_trade_close(
        self,
        signal_id: str,
        *,
        exit_price: Decimal,
        fees: Decimal,
        exit_reason: str,
    ) -> tuple[TradeRecord, Decimal]:
        return await self._position_reconciliation.reconcile_trade_close(
            signal_id,
            exit_price=exit_price,
            fees=fees,
            exit_reason=exit_reason,
        )

    async def _tracked_positions_are_consistent(self) -> bool:
        return await self._position_reconciliation.positions_are_consistent()

    async def _unresolved_order_intents_are_safe(self) -> bool:
        unresolved = await self._repository.list_unresolved_order_intents()
        for intent in unresolved:
            context = self._context_for(intent.instrument_id.venue)
            try:
                result = await context.execution.query_order(
                    intent.instrument_id, intent.client_order_id
                )
            except VenueError:
                result = None
            safely_unfilled = (
                result is not None
                and result.status
                in {OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
                and result.executed_quantity == 0
            )
            if safely_unfilled and result is not None:
                await self._repository.record_order_result(result)
                for lifecycle in await self._repository.list_incomplete_execution_lifecycles():
                    if lifecycle.client_order_id == intent.client_order_id:
                        await self._repository.record_execution_lifecycle(
                            replace(
                                lifecycle,
                                stage=ExecutionStage.ABORTED,
                                updated_at=self._utc_now(),
                            )
                        )
                await self._repository.append_event(
                    "unresolved_order_reconciled_unfilled",
                    {
                        "instrument": str(intent.instrument_id),
                        "client_order_id": intent.client_order_id,
                        "status": result.status.value,
                    },
                )
                continue

            reason = (
                "durable order intent has no confirmed zero-fill terminal result; "
                "exchange order and position reconciliation is required"
            )
            await self._repository.set_kill_switch(True, reason)
            await self._repository.append_event(
                "unresolved_order_intent",
                {
                    "instrument": str(intent.instrument_id),
                    "client_order_id": intent.client_order_id,
                    "created_at": intent.created_at.isoformat(),
                    "observed_status": result.status.value if result is not None else "unknown",
                    "observed_quantity": (
                        str(result.executed_quantity) if result is not None else "unknown"
                    ),
                },
            )
            return False
        return True

    async def adopt_orphaned_position(self, signal_id: str, *, reason: str) -> dict[str, object]:
        """Protect and start tracking a filled position the system lost track of.

        The correct remedy for an orphan is not to close it by hand. The position is real,
        it was opened on a signal whose protective prices were persisted *before* the order
        was sent, and those prices are still the right ones. Closing at market instead
        realises an arbitrary outcome and discards the trade thesis; worse, it makes manual
        intervention the recovery path, which does not scale past one operator watching.

        This places the intended bracket against the quantity that actually filled, writes
        the trade record that should have existed, and advances the lifecycle to TRACKED so
        the exit manager picks it up from the next cycle.
        """
        lifecycles = await self._repository.list_incomplete_execution_lifecycles()
        lifecycle = next((item for item in lifecycles if str(item.signal_id) == signal_id), None)
        if lifecycle is None:
            raise SafetyInterlockError(
                f"incomplete execution lifecycle {signal_id!r} was not found"
            )

        payload = await self._repository.get_order_intent_payload(lifecycle.client_order_id)
        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        if not isinstance(metadata, dict):
            raise SafetyInterlockError(
                "the persisted order intent carries no protective prices; this position "
                "must be reconciled by hand"
            )
        try:
            stop_price = Decimal(str(metadata["stop_price"]))
            target_price = Decimal(str(metadata["target_price"]))
        except (KeyError, ArithmeticError, ValueError) as exc:
            raise SafetyInterlockError(
                f"the persisted order intent has unusable protective prices: {exc}"
            ) from exc

        context = self._context_for(lifecycle.instrument_id.venue)
        account = await context.execution.account_snapshot()
        position = next(
            (
                item
                for item in account.positions
                if item.instrument_id == lifecycle.instrument_id
                and item.direction is lifecycle.direction
                and item.quantity > 0
            ),
            None,
        )
        if position is None:
            raise SafetyInterlockError(
                f"no open {lifecycle.direction.value} position in "
                f"{lifecycle.instrument_id} to adopt; use abandon-lifecycle instead"
            )
        instrument = next(
            (
                item
                for item in await context.market_data.instruments()
                if item.id == lifecycle.instrument_id
            ),
            None,
        )
        if instrument is None:
            raise SafetyInterlockError(
                f"{lifecycle.instrument_id} is not currently tradable at the venue"
            )

        # A venue can report a position with no usable entry price -- testnet especially.
        # The fill price is recoverable from the order itself, so exhaust that before
        # giving up: the bracket only needs *an* anchor to validate geometry against.
        entry_price = position.entry_price
        if entry_price <= 0:
            view = await self._venue_order_view(lifecycle.instrument_id, lifecycle.client_order_id)
            with suppress(ArithmeticError, ValueError):
                entry_price = Decimal(view.get("average_price") or "0")
        if entry_price <= 0 and lifecycle.fill_price is not None:
            entry_price = lifecycle.fill_price

        # Validate every input by name. `normalize_bracket_prices` reports only that
        # something was non-positive, which is useless when four values feed it.
        unusable = [
            name
            for name, value in (
                ("position entry price", entry_price),
                ("stop price", stop_price),
                ("target price", target_price),
                ("instrument price tick", instrument.price_tick),
            )
            if value <= 0
        ]
        if unusable:
            raise SafetyInterlockError(
                f"cannot build a protective bracket: {', '.join(unusable)} is not positive "
                f"(entry={entry_price}, stop={stop_price}, target={target_price}, "
                f"tick={instrument.price_tick})"
            )
        valid_geometry = (
            stop_price < entry_price < target_price
            if lifecycle.direction is Direction.LONG
            else target_price < entry_price < stop_price
        )
        if not valid_geometry:
            raise SafetyInterlockError(
                f"the persisted bracket no longer brackets the fill: entry={entry_price}, "
                f"stop={stop_price}, target={target_price}; close this position manually"
            )

        signal = TradeSignal(
            instrument=instrument,
            direction=lifecycle.direction,
            score=Decimal("0"),
            entry_price=entry_price,
            signal_bar_close=entry_price,
            atr=Decimal("0"),
            stop_price=stop_price,
            target_price=target_price,
            strategy="adopted",
            timeframe="1h/4h",
            momentum_state=MomentumState.GAINING,
            rationale=("adopted orphaned position", reason),
            signal_id=lifecycle.signal_id,
        )
        protection = await context.execution.place_protection(signal, position.quantity)
        if protection.stop is None:
            raise SafetyInterlockError(
                "protective stop was not accepted; the position remains unprotected"
            )
        for order in protection:
            await self._repository.record_order_result(order)

        now = self._utc_now()
        await self._repository.record_trade_open(
            TradeRecord(
                signal_id=lifecycle.signal_id,
                instrument_id=lifecycle.instrument_id,
                direction=lifecycle.direction,
                strategy="adopted",
                quantity=position.quantity,
                entry_price=entry_price,
                stop_price=stop_price,
                target_price=target_price,
                entry_order_id=lifecycle.client_order_id,
                protection_order_ids=tuple(order.exchange_order_id for order in protection),
                opened_at=now,
            )
        )
        await self._repository.record_execution_lifecycle(
            replace(
                lifecycle,
                stage=ExecutionStage.TRACKED,
                updated_at=now,
                filled_quantity=position.quantity,
                fill_price=entry_price,
            )
        )
        await self._repository.append_event(
            "orphaned_position_adopted",
            {
                "signal_id": signal_id,
                "instrument": str(lifecycle.instrument_id),
                "quantity": str(position.quantity),
                "entry_price": str(entry_price),
                "stop_price": str(stop_price),
                "target_price": str(target_price),
                "reason": reason,
            },
        )
        return {
            "adopted": True,
            "signal_id": signal_id,
            "instrument": str(lifecycle.instrument_id),
            "quantity": str(position.quantity),
            "entry_price": str(entry_price),
            "stop_price": str(stop_price),
            "target_price": str(target_price),
            "note": "position is now protected and tracked by the exit manager",
        }

    async def abandon_execution_lifecycle(
        self, signal_id: str, *, reason: str
    ) -> dict[str, object]:
        """Retire a lifecycle the operator has confirmed carries no live position.

        Needed for the one case nothing else can resolve: an entry that filled at the venue
        while the process died before recording it. No ``TradeRecord`` exists, so
        ``reconcile_trade_close`` cannot see it, and the fill is real, so it can never be
        auto-abandoned. Without this the system stays halted permanently.

        The guard is deliberately narrow: this refuses while any position in that instrument
        is open at the venue, and refuses if account state cannot be read at all -- an
        unverifiable account is exactly when a human must not be taken at their word.

        This does **not** book profit or loss. The fill predates any trade record, so its
        PnL is not in the ledger and the daily-loss breaker never sees it. The exchange view
        is written to the event log so the gap is auditable.
        """
        lifecycles = await self._repository.list_incomplete_execution_lifecycles()
        lifecycle = next((item for item in lifecycles if str(item.signal_id) == signal_id), None)
        if lifecycle is None:
            raise SafetyInterlockError(
                f"incomplete execution lifecycle {signal_id!r} was not found"
            )

        # Scope the check to the venue that actually holds the instrument. Requiring *every*
        # venue to be readable would let an unrelated problem -- a stale Spot balance
        # baseline, say -- block recovery of a USD-M lifecycle, which is a deadlock rather
        # than a safeguard: neither side can be cleared until the other is.
        venue = lifecycle.instrument_id.venue
        try:
            account = await self._context_for(venue).execution.account_snapshot()
        except (VenueError, SafetyInterlockError) as exc:
            raise SafetyInterlockError(
                f"{venue.value} account state cannot be read, so an absent position cannot "
                f"be confirmed: {exc}"
            ) from exc
        still_open = any(
            position.instrument_id == lifecycle.instrument_id and position.quantity > 0
            for position in account.positions
        )
        if still_open:
            raise SafetyInterlockError(
                f"{lifecycle.instrument_id} still has an open position at the venue; "
                "close or track it before abandoning the lifecycle"
            )

        exchange_view = await self._venue_order_view(
            lifecycle.instrument_id, lifecycle.client_order_id
        )
        await self._repository.record_execution_lifecycle(
            replace(lifecycle, stage=ExecutionStage.ABORTED, updated_at=self._utc_now())
        )
        await self._repository.append_event(
            "execution_lifecycle_abandoned_by_operator",
            {
                "signal_id": signal_id,
                "instrument": str(lifecycle.instrument_id),
                "stage_when_abandoned": lifecycle.stage.value,
                "client_order_id": lifecycle.client_order_id,
                "exchange_view": exchange_view,
                "reason": reason,
                "pnl_booked": False,
            },
        )
        return {
            "abandoned": True,
            "signal_id": signal_id,
            "instrument": str(lifecycle.instrument_id),
            "exchange_view": exchange_view,
            "pnl_booked": False,
            "note": "realised PnL was not booked; this fill predates any trade record",
        }

    async def _unresolved_intent_detail(self) -> str:
        unresolved = await self._repository.list_unresolved_order_intents()
        if not unresolved:
            return "an order intent has no confirmed terminal result"
        parts = [f"{intent.instrument_id} ({intent.client_order_id})" for intent in unresolved[:3]]
        suffix = f" and {len(unresolved) - 3} more" if len(unresolved) > 3 else ""
        return f"unresolved order intent: {', '.join(parts)}{suffix}; run `atlas reconcile`"

    async def _incomplete_lifecycle_detail(self) -> str:
        incomplete = await self._repository.list_incomplete_execution_lifecycles()
        if not incomplete:
            return "an execution lifecycle stopped before the position was durably tracked"
        parts = [f"{item.instrument_id} stopped at {item.stage.value}" for item in incomplete[:3]]
        suffix = f" and {len(incomplete) - 3} more" if len(incomplete) > 3 else ""
        return f"{', '.join(parts)}{suffix}; run `atlas reconcile` for the exchange view"

    async def _position_mismatch_detail(self) -> str:
        return await self._position_reconciliation.mismatch_detail()

    async def _abandon_dead_entry_intents(
        self, incomplete: tuple[ExecutionLifecycle, ...]
    ) -> tuple[ExecutionLifecycle, ...]:
        """Retire lifecycles whose entry the venue confirms never filled.

        This is the same evidence standard already applied to unresolved order intents: a
        terminal status with zero executed quantity means no inventory was created, so
        nothing can be orphaned. Anything past INTENT_PERSISTED, any fill, and any
        unreadable status are all left to halt the cycle.

        Without this a single IOC entry that expired between persisting its intent and
        recording its result wedges the system permanently, which is a poor trade for a
        condition the venue can settle definitively.
        """
        remaining: list[ExecutionLifecycle] = []
        for lifecycle in incomplete:
            venue_view = await self._venue_order_view(
                lifecycle.instrument_id, lifecycle.client_order_id
            )
            if not self._lifecycle_is_safely_dead(lifecycle, venue_view):
                remaining.append(lifecycle)
                continue
            await self._repository.record_execution_lifecycle(
                replace(lifecycle, stage=ExecutionStage.ABORTED, updated_at=self._utc_now())
            )
            await self._repository.append_event(
                "execution_lifecycle_abandoned",
                {
                    "signal_id": str(lifecycle.signal_id),
                    "instrument": str(lifecycle.instrument_id),
                    "client_order_id": lifecycle.client_order_id,
                    "exchange_status": venue_view.get("status", "unknown"),
                },
            )
        return tuple(remaining)

    async def reconciliation_report(self) -> dict[str, object]:
        """Read-only view of everything currently preventing a cycle from trading.

        Failing closed is only half an operational control: an operator also needs to see
        *what* is unresolved and what the venue believes about it. Without this the system
        halts with "reconciliation is required" and offers nothing to reconcile against.

        This performs no writes and never clears the kill switch.
        """
        kill_switch = await self._repository.get_state("kill_switch") or {}
        unresolved = await self._repository.list_unresolved_order_intents()
        incomplete = await self._repository.list_incomplete_execution_lifecycles()
        open_trades = tuple(await self._repository.list_open_trades())
        management = tuple(await self._repository.list_position_management())
        # A venue whose account state cannot be read is itself a finding, and the most
        # likely one when the system is already halted. Reporting it beats propagating it:
        # a diagnostic that dies on the failure it exists to explain is worse than none,
        # because it also hides every other unrelated problem in the same report.
        accounts, venue_errors = await self._readable_accounts()

        blocking: list[dict[str, object]] = []
        for lifecycle in incomplete:
            venue_view = await self._venue_order_view(
                lifecycle.instrument_id, lifecycle.client_order_id
            )
            blocking.append(
                {
                    "signal_id": str(lifecycle.signal_id),
                    "instrument": str(lifecycle.instrument_id),
                    "stage": lifecycle.stage.value,
                    "client_order_id": lifecycle.client_order_id,
                    "filled_quantity": str(lifecycle.filled_quantity),
                    "exchange_view": venue_view,
                    "auto_resolvable": self._lifecycle_is_safely_dead(lifecycle, venue_view),
                }
            )
        return {
            "kill_switch": bool(kill_switch.get("active")),
            "kill_switch_reason": str(kill_switch.get("reason", "")),
            "venue_errors": venue_errors,
            "unresolved_order_intents": [
                {
                    "instrument": str(intent.instrument_id),
                    "client_order_id": intent.client_order_id,
                    "created_at": intent.created_at.isoformat(),
                    "exchange_view": await self._venue_order_view(
                        intent.instrument_id, intent.client_order_id
                    ),
                }
                for intent in unresolved
            ],
            "incomplete_execution_lifecycles": blocking,
            "tracked_open_trades": [
                {
                    "signal_id": str(trade.signal_id),
                    "instrument": str(trade.instrument_id),
                    "direction": trade.direction.value,
                    "initial_quantity": str(trade.quantity),
                    "remaining_quantity": str(self._expected_quantity(trade, management)),
                }
                for trade in open_trades
            ],
            "exchange_positions": [
                {
                    "instrument": str(position.instrument_id),
                    "direction": position.direction.value,
                    "quantity": str(position.quantity),
                }
                for account in accounts
                for position in account.positions
            ],
        }

    async def _readable_accounts(
        self,
    ) -> tuple[tuple[AccountSnapshot, ...], list[dict[str, str]]]:
        return await self._position_reconciliation.readable_accounts()

    async def _venue_order_view(
        self, instrument_id: InstrumentId, client_order_id: str
    ) -> dict[str, str]:
        try:
            context = self._context_for(instrument_id.venue)
        except RuntimeError:
            return {"status": "no_adapter_registered"}
        try:
            result = await context.execution.query_order(instrument_id, client_order_id)
        except VenueError as exc:
            return {"status": "query_failed", "detail": str(exc)[:200]}
        if result is None:
            return {"status": "not_found_at_exchange"}
        return {
            "status": result.status.value,
            "executed_quantity": str(result.executed_quantity),
            "average_price": str(result.average_price) if result.average_price else "",
        }

    @staticmethod
    def _lifecycle_is_safely_dead(
        lifecycle: ExecutionLifecycle, venue_view: dict[str, str]
    ) -> bool:
        """Only an unfilled entry intent can be abandoned without human judgement.

        Past ENTRY_FILLED there is inventory at the venue, so an automatic abort could
        silently orphan a live position. That case always needs an operator.
        """
        if lifecycle.stage is not ExecutionStage.INTENT_PERSISTED:
            return False
        return venue_view.get("status") in {
            OrderStatus.CANCELED.value,
            OrderStatus.REJECTED.value,
            OrderStatus.EXPIRED.value,
        } and venue_view.get("executed_quantity") in {"0", "0E-8", "0.0"}

    async def _incomplete_execution_lifecycles_are_safe(self) -> bool:
        incomplete = tuple(await self._repository.list_incomplete_execution_lifecycles())
        incomplete = await self._abandon_dead_entry_intents(incomplete)
        if not incomplete:
            return True
        reason = (
            "an execution lifecycle stopped before the position was durably tracked; "
            "exchange order and protection reconciliation is required"
        )
        await self._repository.set_kill_switch(True, reason)
        await self._repository.append_event(
            "incomplete_execution_lifecycle",
            {
                "executions": [
                    {
                        "signal_id": str(item.signal_id),
                        "instrument": str(item.instrument_id),
                        "stage": item.stage.value,
                        "client_order_id": item.client_order_id,
                    }
                    for item in incomplete
                ]
            },
        )
        return False

    @staticmethod
    def _missing_tracked_trades(
        trades: tuple[TradeRecord, ...],
        accounts: tuple[AccountSnapshot, ...],
        management: tuple[PositionManagementState, ...] = (),
    ) -> tuple[TradeRecord, ...]:
        return PositionReconciliationService.missing_tracked_trades(trades, accounts, management)

    @staticmethod
    def _untracked_exchange_positions(
        open_trades: tuple[TradeRecord, ...],
        accounts: tuple[AccountSnapshot, ...],
        management: tuple[PositionManagementState, ...] = (),
    ) -> tuple[Position, ...]:
        return PositionReconciliationService.untracked_exchange_positions(
            open_trades, accounts, management
        )

    @staticmethod
    def _position_matches_trade(
        position: Position,
        trade: TradeRecord,
        management: tuple[PositionManagementState, ...] = (),
    ) -> bool:
        return PositionReconciliationService.position_matches_trade(position, trade, management)

    @staticmethod
    def _expected_quantity(
        trade: TradeRecord, management: tuple[PositionManagementState, ...]
    ) -> Decimal:
        return PositionReconciliationService.expected_quantity(trade, management)

    def _context_for(self, venue: Venue) -> VenueContext:
        try:
            return next(context for context in self._contexts if context.market_data.venue is venue)
        except StopIteration as exc:
            raise RuntimeError(f"no context registered for {venue.value}") from exc
