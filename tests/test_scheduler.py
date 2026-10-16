from __future__ import annotations

from datetime import UTC, datetime

from algotrader.application.scheduler import seconds_until_next_cycle


def test_scheduler_waits_for_next_aligned_eight_hour_close() -> None:
    now = datetime(2026, 8, 2, 10, 42, 15, tzinfo=UTC)
    assert seconds_until_next_cycle(now, 8, 30) == 19095


def test_scheduler_started_between_close_and_grace_runs_current_boundary() -> None:
    now = datetime(2026, 8, 2, 16, 0, 10, tzinfo=UTC)
    assert seconds_until_next_cycle(now, 8, 30) == 20


def test_scheduler_at_grace_boundary_uses_next_cycle() -> None:
    now = datetime(2026, 8, 2, 16, 0, 30, tzinfo=UTC)
    assert seconds_until_next_cycle(now, 8, 30) == 28800


def test_hourly_discovery_wakes_after_each_confirmed_close() -> None:
    now = datetime(2026, 8, 2, 10, 42, 15, tzinfo=UTC)
    assert seconds_until_next_cycle(now, 1, 30) == 1095
