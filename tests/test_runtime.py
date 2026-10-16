from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from algotrader.infrastructure.runtime import Runtime


class ClosingComponent:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.closed = False
        self._error = error

    async def close(self) -> None:
        self.closed = True
        if self._error is not None:
            raise self._error


class StoppingScheduler:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.stopped = False
        self._error = error

    async def stop(self) -> None:
        self.stopped = True
        if self._error is not None:
            raise self._error


async def test_runtime_close_attempts_every_component_after_failures() -> None:
    scheduler = StoppingScheduler(error=RuntimeError("scheduler"))
    first = ClosingComponent(error=RuntimeError("first client"))
    second = ClosingComponent()
    runtime = cast(
        Runtime,
        SimpleNamespace(scheduler=scheduler, clients=(first, second)),
    )

    with pytest.raises(RuntimeError, match="2 component"):
        await Runtime.close(runtime)

    assert scheduler.stopped
    assert first.closed
    assert second.closed


async def test_runtime_close_succeeds_when_every_component_closes() -> None:
    scheduler = StoppingScheduler()
    client = ClosingComponent()
    runtime = cast(Runtime, SimpleNamespace(scheduler=scheduler, clients=(client,)))

    await Runtime.close(runtime)

    assert scheduler.stopped
    assert client.closed
