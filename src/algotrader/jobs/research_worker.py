"""Explicit opt-in role supervision; never started by API, runtime or core cron."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from algotrader.adapters.binance.research import ResearchMarketData
from algotrader.application.rank_collector import RankCollector
from algotrader.application.research_outcome_worker import ResearchOutcomeWorker
from algotrader.application.research_rank_worker import ResearchRankWorker
from algotrader.application.research_sessions import ResearchSessions
from algotrader.application.shadow_benchmark import ShadowBenchmark
from algotrader.domain.research_sessions import ResearchSession, aware_utc, canonical_id

WorkerRole = Literal["ranks", "outcomes"]


def utc_now() -> datetime:
    return datetime.now(UTC)


async def interruptible_wait(seconds: float, stop: asyncio.Event) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)


def _slot(session: ResearchSession, now: datetime) -> int:
    return min(
        720,
        max(0, int((now - session.anchor).total_seconds() // session.policy.rank_interval_seconds)),
    )


def _delay(
    session: ResearchSession, status: dict[str, Any], role: WorkerRole, now: datetime
) -> float:
    if status["terminal"] or not status.get("guard", {}).get("ok", True):
        return 30.0
    if role == "ranks":
        target = status["rank_decision"]["next_at"]
    else:
        horizon = next((value for value in (24, 48) if value not in status["reports"]), None)
        if horizon is None:
            return 30.0
        due_at = status["horizons"][horizon]["due_at"]
        target = (
            due_at
            if now < due_at
            else session.anchor
            + timedelta(seconds=(_slot(session, now) + 1) * session.policy.rank_interval_seconds)
        )
        if status["horizons"][horizon]["expired"]:
            target = now + timedelta(seconds=1)
    return 30.0 if target is None else max(0.05, min(30.0, (target - now).total_seconds()))


async def _tick_until_stop(tick: Coroutine[Any, Any, dict[str, Any]], stop: asyncio.Event) -> None:
    acquisition: asyncio.Task[dict[str, Any]] = asyncio.create_task(tick)
    stopping = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait({acquisition, stopping}, return_when=asyncio.FIRST_COMPLETED)
        if stopping in done and not acquisition.done():
            acquisition.cancel()
        try:
            await acquisition
        except asyncio.CancelledError:
            if not stop.is_set():
                raise
    finally:
        stopping.cancel()
        if not acquisition.done():
            acquisition.cancel()
            while not acquisition.done():
                try:
                    await asyncio.shield(acquisition)
                except asyncio.CancelledError:
                    continue
        await asyncio.gather(stopping, return_exceptions=True)


async def run_research_worker(
    root: Path,
    session_id: str,
    role: WorkerRole,
    *,
    clock: Callable[[], datetime] = utc_now,
    monotonic: Callable[[], float] = time.monotonic,
    wait: Callable[[float, asyncio.Event], Awaitable[None]] = interruptible_wait,
    stop: asyncio.Event | None = None,
) -> int:
    canonical_id(session_id)
    if role not in ("ranks", "outcomes"):
        raise ValueError("worker role must be ranks or outcomes")
    last_valid = aware_utc(clock())
    sessions = ResearchSessions(root)
    session = sessions.store.read(session_id)
    owns_signals = stop is None
    stop = stop if stop is not None else asyncio.Event()
    loop = asyncio.get_running_loop()
    handlers: dict[signal.Signals, Any] = {}
    with sessions.store.role_lock(role):
        try:
            if owns_signals:
                for signum in (signal.SIGTERM, signal.SIGINT):
                    handlers[signum] = signal.getsignal(signum)
                    loop.add_signal_handler(signum, stop.set)
            sessions.record_state(session_id, role, "starting", at=last_valid)
            if role == "ranks":
                collector = RankCollector(
                    sessions.root,
                    session.rank_policy,
                    clock=clock,
                    monotonic=monotonic,
                    market_factory=lambda budget: ResearchMarketData(
                        budget,
                        {
                            "on_response": lambda response: sessions.record_response(
                                session_id, "ranks", response, at=clock()
                            )
                        },
                    ),
                )
                worker: ResearchRankWorker | ResearchOutcomeWorker = ResearchRankWorker(
                    sessions, collector
                )
            else:
                worker = ResearchOutcomeWorker(
                    sessions,
                    ResearchMarketData,
                    ShadowBenchmark(sessions.root, session.rank_policy),
                    monotonic=monotonic,
                )
            events = sessions.store.events(session_id)
            heartbeat_slots = {
                event.details.get("slot")
                for event in events
                if event.role == role and event.kind == "heartbeat"
            }
            jump_slots = {
                event.details.get("slot")
                for event in events
                if event.role == role and event.kind == "clock_discontinuity"
            }
            previous_wall, previous_mono = None, None
            while not stop.is_set():
                now, mono = aware_utc(clock()), monotonic()
                last_valid = now
                slot = _slot(session, now)
                if previous_wall is not None and previous_mono is not None:
                    expected = previous_wall + timedelta(seconds=max(0.0, mono - previous_mono))
                    if abs((now - expected).total_seconds()) > 5 and slot not in jump_slots:
                        sessions.store.append(
                            session_id,
                            role,
                            "clock_discontinuity",
                            {
                                "slot": slot,
                                "previous_at": previous_wall,
                                "expected_at": expected,
                                "actual_at": now,
                            },
                            now,
                        )
                        jump_slots.add(slot)
                previous_wall, previous_mono = now, mono
                if slot not in heartbeat_slots:
                    sessions.store.append(
                        session_id, role, "heartbeat", {"slot": slot, "pid": os.getpid()}, now
                    )
                    heartbeat_slots.add(slot)
                status = sessions.status(session_id, now=now)
                if not status["terminal"]:
                    if status.get("guard", {}).get("ok", False) and status["role_states"].get(
                        role
                    ) in (None, "starting", "disabled", "source_mismatch", "storage_blocked"):
                        sessions.record_state(session_id, role, "running", at=now, slot=slot)
                    await _tick_until_stop(worker.tick(session_id, now=now), stop)
                if stop.is_set():
                    break
                now = aware_utc(clock())
                last_valid = now
                status = sessions.status(session_id, now=now)
                await wait(_delay(session, status, role, now), stop)
        finally:
            try:
                sessions.store.append(
                    session_id,
                    role,
                    "state",
                    {"state": "disabled", "reason": "process_shutdown"},
                    last_valid,
                )
            finally:
                for signum, previous in handlers.items():
                    loop.remove_signal_handler(signum)
                    signal.signal(signum, previous)
    return 0


def worker_health(
    root: Path, session_id: str, role: WorkerRole, *, now: datetime | None = None
) -> dict[str, Any]:
    canonical_id(session_id)
    if role not in ("ranks", "outcomes"):
        raise ValueError("invalid worker health role")
    now = aware_utc(now if now is not None else utc_now())
    sessions = ResearchSessions(root)
    status = sessions.status(session_id, now=now)
    heartbeat = status["heartbeats"].get(role)
    owned = False
    try:
        with sessions.store.role_lock(role):
            pass
    except RuntimeError:
        owned = True
    guard = status.get("guard", {"ok": True})
    fresh = heartbeat is not None and 0 <= (now - heartbeat).total_seconds() <= 360
    healthy = owned and (status["terminal"] or fresh) and guard["ok"]
    reason = "terminal_idle" if status["terminal"] else "idle_waiting"
    if not owned:
        reason = "role_not_owned"
    elif not status["terminal"] and not fresh:
        reason = "missing_or_stale_heartbeat"
    elif not guard["ok"]:
        reason = guard.get("detail") or guard["reason"]
    events = sessions.store.events(session_id)
    role_events = [event for event in events if event.role == role]
    if role == "ranks":
        active = any(slot["kind"] == "rank_reserved" for slot in status["rank_slots"].values())
    else:
        passes = [event for event in role_events if event.kind == "outcome_pass"]
        active = bool(passes and passes[-1].details.get("phase") == "reserved")
    collecting = bool(healthy and active and not status["terminal"])
    return {
        "session_id": session_id,
        "role": role,
        "state": status["state"],
        "healthy": bool(healthy),
        "collecting": collecting,
        "reason": "acquiring" if collecting else reason,
        "heartbeat_at": heartbeat,
        "last_successful_arrival": status["last_successful_arrival"],
        "next_allowed_at": guard.get("next_allowed_at"),
    }
