"""Resumable bounded outcome passes and frozen exactly-once report linkage."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from algotrader.adapters.binance.research import OutcomePassBudgetReached, ResearchPassStopped
from algotrader.application.forward_observer import MinuteHistoryPort
from algotrader.application.research_sessions import ResearchSessions
from algotrader.application.shadow_benchmark import ShadowBenchmark
from algotrader.application.shadow_budget import ShadowBudget, ShadowBudgetExceeded
from algotrader.application.shadow_observer import ShadowObserver
from algotrader.domain.models import Candle, Instrument
from algotrader.domain.research_sessions import ResearchSession, aware_utc, due_horizon
from algotrader.domain.shadow_discovery import ShadowPolicy
from algotrader.infrastructure.rank_archive import digest, envelope
from algotrader.infrastructure.shadow_archive import LocalShadowArchive


class ResearchOutcomeMarketPort(MinuteHistoryPort, Protocol):
    async def close(self) -> None: ...


class _BoundedMinutes:
    def __init__(
        self,
        market: ResearchOutcomeMarketPort,
        budget: ShadowBudget,
        sessions: ResearchSessions,
        session_id: str,
        clock: Callable[[], datetime],
        horizon: int,
        pass_id: str,
    ) -> None:
        self.market, self.budget = market, budget
        self.sessions, self.session_id, self.clock = sessions, session_id, clock
        self.horizon, self.pass_id = horizon, pass_id

    async def minute_candles_between(
        self, instrument: Instrument, start: datetime, end: datetime
    ) -> Sequence[Candle]:
        if (
            self.budget.remaining_seconds <= 0
            or self.budget.attempts >= self.budget.policy.max_attempts
        ):
            raise OutcomePassBudgetReached("pass_budget")
        try:
            guard = self.sessions.guard(self.session_id, now=self.clock())
        except Exception as exc:
            raise OutcomePassBudgetReached(f"guard_failed:{type(exc).__name__}") from exc
        if not guard["ok"]:
            raise OutcomePassBudgetReached(guard.get("detail") or guard["reason"])
        try:
            self.sessions.store.append(
                self.session_id,
                "outcomes",
                "outcome_symbol_reserved",
                {
                    "horizon_hours": self.horizon,
                    "symbol": instrument.id.symbol,
                    "pass_id": self.pass_id,
                },
                self.clock(),
            )
        except Exception as exc:
            raise OutcomePassBudgetReached("symbol_reservation_failed") from exc
        try:
            result = await self.market.minute_candles_between(instrument, start, end)
            if self.budget.remaining_seconds <= 0:
                raise OutcomePassBudgetReached("pass_deadline")
            return result
        except (ShadowBudgetExceeded, ResearchPassStopped, TimeoutError) as exc:
            raise OutcomePassBudgetReached(type(exc).__name__) from exc


class ResearchOutcomeWorker:
    def __init__(
        self,
        sessions: ResearchSessions,
        market_factory: Callable[[ShadowBudget, dict[str, Any]], ResearchOutcomeMarketPort],
        benchmark: ShadowBenchmark,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if benchmark.root.resolve() != sessions.root:
            raise ValueError("outcome worker and benchmark must share one archive")
        self.sessions, self.factory, self.benchmark, self.monotonic = (
            sessions,
            market_factory,
            benchmark,
            monotonic,
        )

    def _stopped(self, session_id: str) -> bool:
        return any(
            event.kind == "stop_requested" for event in self.sessions.store.events(session_id)
        )

    async def _finalize(
        self,
        session: ResearchSession,
        horizon: int,
        now: datetime,
        inputs: dict[str, Any] | None = None,
    ) -> None:
        store = self.sessions.store
        reservation = store.report_reservation(session.session_id, horizon)
        if reservation is None:
            frozen = (
                inputs
                if inputs is not None
                else await asyncio.to_thread(
                    self.benchmark.freeze_inputs, session.shadow_id, horizon, as_of=now
                )
            )
            reservation = store.reserve_report(session.session_id, horizon, now, frozen)
        if not any(
            event.kind == "report_reserved" and event.details["horizon_hours"] == horizon
            for event in store.events(session.session_id)
        ):
            store.append(
                session.session_id,
                "outcomes",
                "report_reserved",
                {
                    "horizon_hours": horizon,
                    "report_id": reservation["report_id"],
                    "as_of": reservation["as_of"],
                    "inputs_checksum": digest(reservation["inputs"]),
                },
                now,
            )
        commit = asyncio.create_task(
            asyncio.to_thread(
                self.benchmark.build,
                session.shadow_id,
                horizon,
                as_of=reservation["as_of"],
                report_id=reservation["report_id"],
                inputs=reservation["inputs"],
            )
        )
        cancelled = False
        while True:
            try:
                report = await asyncio.shield(commit)
                break
            except asyncio.CancelledError:
                cancelled = True
        store.append(
            session.session_id,
            "outcomes",
            "report_completed",
            {
                "horizon_hours": horizon,
                "report_id": report.report_id,
                "checksum": digest(envelope("rank_benchmark", report)),
                "as_of": report.as_of,
            },
            now,
        )
        if cancelled:
            raise asyncio.CancelledError()

    async def _pass(
        self, session: ResearchSession, horizon: int, now: datetime, slot: int
    ) -> datetime:
        budget = ShadowBudget(
            ShadowPolicy(
                max_attempts=session.policy.outcome_max_attempts,
                deadline_seconds=session.policy.outcome_deadline_seconds,
            ),
            monotonic=self.monotonic,
            expires_at=self.monotonic()
            + max(
                0.0,
                (
                    session.anchor
                    + timedelta(hours=horizon, seconds=session.policy.retry_window_seconds)
                    - now
                ).total_seconds(),
            ),
        )
        started = self.monotonic()

        def clock() -> datetime:
            return now + timedelta(seconds=max(0.0, self.monotonic() - started))

        metadata: dict[str, Any] = {
            "on_response": lambda response: self.sessions.record_response(
                session.session_id, "outcomes", response, at=clock()
            ),
        }
        pass_id = str(uuid4())
        base = {"horizon_hours": horizon, "slot": slot, "pass_id": pass_id}
        self.sessions.store.append(
            session.session_id, "outcomes", "outcome_pass", {**base, "phase": "reserved"}, now
        )
        market = None
        acquisition: asyncio.Task[dict[str, Any]] | None = None
        phase, error, cancelled, stopping = "complete", None, False, False
        try:
            market = self.factory(budget, metadata)
            visits = {instrument.id.symbol: 0 for instrument in session.cohort}
            for event in self.sessions.store.events(session.session_id):
                if (
                    event.kind == "outcome_symbol_reserved"
                    and event.details["horizon_hours"] == horizon
                ):
                    symbol = event.details["symbol"]
                    if symbol not in visits:
                        raise ValueError("reserved symbol is outside frozen cohort")
                    visits[symbol] += 1
            order = tuple(sorted(visits, key=lambda symbol: visits[symbol]))
            port = _BoundedMinutes(
                market, budget, self.sessions, session.session_id, clock, horizon, pass_id
            )
            observer = ShadowObserver(
                LocalShadowArchive(self.sessions.root), port, pace_seconds=0.2
            )

            async def observe() -> dict[str, Any]:
                async with asyncio.timeout(budget.remaining_seconds):
                    return await observer.observe(
                        session.shadow_id, horizon, len(session.cohort), now=now, symbol_order=order
                    )

            acquisition = asyncio.create_task(observe())
            while not acquisition.done():
                done, _ = await asyncio.wait({acquisition}, timeout=0.2)
                if not done and self._stopped(session.session_id):
                    stopping = True
                    acquisition.cancel()
                    break
            await acquisition
        except OutcomePassBudgetReached as exc:
            phase, error = "bounded", str(exc)
        except asyncio.CancelledError:
            phase, error, cancelled = (
                "stopped" if stopping else "cancelled",
                "CancelledError",
                not stopping,
            )
        except TimeoutError:
            phase, error = "bounded", "pass_deadline"
        except Exception as exc:
            phase, error = "failed", type(exc).__name__
        finally:
            if acquisition is not None and not acquisition.done():
                acquisition.cancel()
                while not acquisition.done():
                    try:
                        await asyncio.shield(acquisition)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
            if market is not None:
                try:
                    async with asyncio.timeout(budget.remaining_seconds):
                        await market.close()
                except asyncio.CancelledError:
                    cancelled, phase, error = True, "cancelled", "CancelledError"
                except Exception as exc:
                    phase, error = "bounded", f"cleanup:{type(exc).__name__}"
            self.sessions.store.append(
                session.session_id,
                "outcomes",
                "outcome_pass",
                {
                    **base,
                    "phase": phase,
                    "error": error,
                    "accounting": budget.snapshot(),
                    "last_response": metadata.get("last_response"),
                },
                clock(),
            )
        if cancelled:
            raise asyncio.CancelledError()
        return clock()

    async def tick(self, session_id: str, *, now: datetime) -> dict[str, Any]:
        now = aware_utc(now)
        tick_started = self.monotonic()
        session = self.sessions.store.read(session_id)
        if self.benchmark.policy != session.rank_policy:
            raise ValueError("outcome benchmark policy differs from frozen session")
        status = self.sessions.status(session_id, now=now)
        if status["terminal"]:
            return status
        guard = self.sessions.guard(session_id, now=now)
        # Cooldown blocks acquisition, not offline finalization at a fixed expiry.
        if not guard["ok"] and guard.get("detail") not in (
            "exchange_cooldown",
            "cooldown_unrepresentable_operator_attention",
        ):
            self.sessions.record_state(session_id, "outcomes", guard["reason"], at=now)
            return self.sessions.status(session_id, now=now)
        horizon = due_horizon(session, now, frozenset(status["reports"]))
        if horizon is None:
            return status
        reservation = self.sessions.store.report_reservation(session_id, horizon)
        if reservation is not None:
            await self._finalize(session, horizon, now)
            return self.sessions.status(session_id, now=now)
        inputs = await asyncio.to_thread(
            self.benchmark.freeze_inputs, session.shadow_id, horizon, as_of=now
        )
        expired = now >= session.anchor + timedelta(
            hours=horizon, seconds=session.policy.retry_window_seconds
        )
        if expired or len(inputs["selected_outcomes"]) == len(session.cohort):
            await self._finalize(session, horizon, now, inputs)
            return self.sessions.status(session_id, now=now)
        if not guard["ok"]:
            return self.sessions.status(session_id, now=now)
        # Guard/report preparation is part of elapsed time, not a reason to move
        # the original UTC retry window forward before starting a fresh budget.
        pass_now = now + timedelta(seconds=max(0.0, self.monotonic() - tick_started))
        guard = self.sessions.guard(session_id, now=pass_now)
        if not guard["ok"]:
            return self.sessions.status(session_id, now=pass_now)
        if pass_now >= session.anchor + timedelta(
            hours=horizon, seconds=session.policy.retry_window_seconds
        ):
            await self._finalize(session, horizon, pass_now)
            return self.sessions.status(session_id, now=pass_now)
        now = pass_now
        slot = int((now - session.anchor).total_seconds() // session.policy.rank_interval_seconds)
        if any(
            event.kind == "outcome_pass" and event.details["slot"] == slot
            for event in self.sessions.store.events(session_id)
        ):
            return status
        completed_at = await self._pass(session, horizon, now, slot)
        if self._stopped(session_id):
            return self.sessions.status(session_id, now=completed_at)
        # Freeze again after the pass; this exact manifest is reserved before publication.
        inputs = await asyncio.to_thread(
            self.benchmark.freeze_inputs, session.shadow_id, horizon, as_of=completed_at
        )
        if len(inputs["selected_outcomes"]) == len(
            session.cohort
        ) or completed_at >= session.anchor + timedelta(
            hours=horizon, seconds=session.policy.retry_window_seconds
        ):
            await self._finalize(session, horizon, completed_at, inputs)
        self.sessions.record_state(
            session_id,
            "outcomes",
            "running" if len(inputs["selected_outcomes"]) == len(session.cohort) else "degraded",
            at=completed_at,
            slot=slot,
        )
        return self.sessions.status(session_id, now=completed_at)
