from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from algotrader.application.service import TradingSystem
from algotrader.domain.ports import SchedulerRepository


def seconds_until_next_cycle(now: datetime, interval_hours: int, grace_seconds: int) -> float:
    if interval_hours <= 0 or 24 % interval_hours != 0:
        raise ValueError("interval_hours must be a positive divisor of 24")
    utc_now = now.astimezone(UTC)
    boundary_hour = (utc_now.hour // interval_hours) * interval_hours
    boundary = utc_now.replace(hour=boundary_hour, minute=0, second=0, microsecond=0)
    target = boundary + timedelta(seconds=grace_seconds)
    if target <= utc_now:
        target = boundary + timedelta(hours=interval_hours, seconds=grace_seconds)
    return max(0.0, (target - utc_now).total_seconds())


class CycleScheduler:
    """Discovers on aligned UTC closes; TradingSystem independently throttles entries."""

    def __init__(
        self,
        system: TradingSystem,
        repository: SchedulerRepository,
        *,
        interval_hours: int,
        grace_seconds: int,
        failure_limit: int,
    ) -> None:
        self._system = system
        self._repository = repository
        self._interval_hours = interval_hours
        self._grace_seconds = grace_seconds
        self._failure_limit = failure_limit
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if not self.running:
            self._task = asyncio.create_task(self.run_forever(), name="atlas-cycle-scheduler")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def run_forever(self) -> None:
        consecutive_failures = 0
        while True:
            delay = seconds_until_next_cycle(
                datetime.now(UTC), self._interval_hours, self._grace_seconds
            )
            await asyncio.sleep(delay)
            try:
                await self._system.run_cycle()
                consecutive_failures = 0
            except Exception as exc:
                consecutive_failures += 1
                await self._repository.append_event(
                    "scheduler_cycle_failed",
                    {
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:300],
                        "consecutive_failures": consecutive_failures,
                    },
                )
                if consecutive_failures >= self._failure_limit:
                    await self._repository.set_kill_switch(
                        True,
                        f"scheduler failed {consecutive_failures} consecutive cycles",
                    )


def seconds_until_next_fast_cycle(
    now: datetime, interval_minutes: int = 15, grace_seconds: int = 3
) -> float:
    """Calculates seconds until the next 15-minute kline close + 3 seconds (e.g. :00:03, :15:03, :30:03, :45:03 UTC)."""
    if interval_minutes <= 0 or 60 % interval_minutes != 0:
        raise ValueError("interval_minutes must be a positive divisor of 60")
    utc_now = now.astimezone(UTC)
    boundary_minute = (utc_now.minute // interval_minutes) * interval_minutes
    boundary = utc_now.replace(minute=boundary_minute, second=0, microsecond=0)
    target = boundary + timedelta(seconds=grace_seconds)
    if target <= utc_now:
        target = boundary + timedelta(minutes=interval_minutes, seconds=grace_seconds)
    return max(0.0, (target - utc_now).total_seconds())


def seconds_until_next_settlement_cycle(
    now: datetime, interval_hours: int = 8, grace_seconds: int = 30
) -> float:
    """Calculates seconds until the next 8-hour settlement boundary + 30 seconds (00:00:30, 08:00:30, 16:00:30 UTC)."""
    return seconds_until_next_cycle(now, interval_hours=interval_hours, grace_seconds=grace_seconds)


class DualLoopScheduler:
    """Dual-Loop Async Scheduler (Section 10 of AGENT.md V2.5).

    1. Fast Evaluation Loop (Every 15 Minutes on Kline Close + 3s):
       - Polls /fapi/v1/premiumIndex and /fapi/v1/ticker/24hr
       - Evaluates incremental 1H and 4H klines for the 160 active universe symbols
       - Writes atomic snapshot to data/screener_latest.json
    2. Settlement & Macro Loop (Every 8 Hours at 00:00:30, 08:00:30, 16:00:30 UTC):
       - Re-computes the top-160 ILI universe, refreshes 7d DeFiLlama stablecoin flows, recalculates coi, snapshots post-settlement funding rates
    """

    def __init__(
        self,
        *,
        fast_cycle_fn: Any = None,
        settlement_cycle_fn: Any = None,
        fast_interval_minutes: int = 15,
        fast_grace_seconds: int = 3,
        settlement_interval_hours: int = 8,
        settlement_grace_seconds: int = 30,
        repository: SchedulerRepository | None = None,
    ) -> None:
        self._fast_cycle_fn = fast_cycle_fn
        self._settlement_cycle_fn = settlement_cycle_fn
        self._fast_interval_minutes = fast_interval_minutes
        self._fast_grace_seconds = fast_grace_seconds
        self._settlement_interval_hours = settlement_interval_hours
        self._settlement_grace_seconds = settlement_grace_seconds
        self._repository = repository
        self._fast_task: asyncio.Task[None] | None = None
        self._settlement_task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return (
            (self._fast_task is not None and not self._fast_task.done())
            or (self._settlement_task is not None and not self._settlement_task.done())
        )

    def start(self) -> None:
        if not self.running:
            if self._fast_cycle_fn:
                self._fast_task = asyncio.create_task(
                    self.run_fast_forever(), name="atlas-fast-eval-scheduler"
                )
            if self._settlement_cycle_fn:
                self._settlement_task = asyncio.create_task(
                    self.run_settlement_forever(), name="atlas-settlement-macro-scheduler"
                )

    async def stop(self) -> None:
        for task in (self._fast_task, self._settlement_task):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        self._fast_task = None
        self._settlement_task = None

    async def run_fast_forever(self) -> None:
        while True:
            delay = seconds_until_next_fast_cycle(
                datetime.now(UTC),
                self._fast_interval_minutes,
                self._fast_grace_seconds,
            )
            await asyncio.sleep(delay)
            if self._fast_cycle_fn:
                try:
                    await self._fast_cycle_fn()
                except Exception as exc:
                    if self._repository:
                        await self._repository.append_event(
                            "fast_loop_cycle_failed",
                            {"error_type": type(exc).__name__, "message": str(exc)[:300]},
                        )

    async def run_settlement_forever(self) -> None:
        while True:
            delay = seconds_until_next_settlement_cycle(
                datetime.now(UTC),
                self._settlement_interval_hours,
                self._settlement_grace_seconds,
            )
            await asyncio.sleep(delay)
            if self._settlement_cycle_fn:
                try:
                    await self._settlement_cycle_fn()
                except Exception as exc:
                    if self._repository:
                        await self._repository.append_event(
                            "settlement_loop_cycle_failed",
                            {"error_type": type(exc).__name__, "message": str(exc)[:300]},
                        )
