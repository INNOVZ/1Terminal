from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from algotrader.application.scan_replay import ReplayMarket, ReplayTape
from algotrader.infrastructure.scan_archive import canonical_bytes
from scripts.research_immutable_capture import ImmutableTape, capture_control, freeze
from scripts.research_verified_replay import diagnostic_settings
from tests.conftest import trending_candles
from tests.test_capture_evidence import bundle_fixture


async def test_immutable_recorder_preserves_exact_production_capture(monkeypatch):
    from algotrader.application import scan_recording
    from scripts import research_immutable_capture as module

    bundle = await bundle_fixture()
    original = bundle["record"]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return original.started_at

    monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setattr(scan_recording, "datetime", Clock)
    ordinary_market = ReplayMarket(ReplayTape(original.inputs))
    ordinary_arguments = dict(original.request)
    ordinary_arguments.pop("derivatives_enabled")
    ordinary = await scan_recording.capture_scan(
        ordinary_market, ordinary_market, ordinary_market, diagnostic_settings(), ordinary_arguments
    )
    ordinary_market.tape.assert_consumed()
    assert ordinary.failure is None
    original = ordinary.record
    market = ReplayMarket(ReplayTape(original.inputs))
    arguments = dict(original.request)
    arguments.pop("derivatives_enabled")
    result = await capture_control(market, market, market, diagnostic_settings(), arguments)
    market.tape.assert_consumed()
    assert result.failure is None
    actual = result.record
    for key in ["inputs", "opportunities", "decisions", "counts", "cohort"]:
        assert canonical_bytes(getattr(actual, key)) == canonical_bytes(getattr(original, key))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda x: x.append(None),
        lambda x: x.clear(),
        lambda x: x.__setitem__(0, None),
        lambda x: x.extend([]),
        lambda x: x.sort(),
        lambda x: x.__iadd__([]),
    ],
)
def test_snapshot_list_cannot_mutate_archived_values(mutation):
    bars = trending_candles(count=2)
    result = freeze(bars)
    assert result[0] is bars[0]
    before = canonical_bytes(result)
    with pytest.raises(TypeError):
        mutation(result)
    assert canonical_bytes(result) == before
    with pytest.raises(FrozenInstanceError):
        result[0].close = 0
    bars.clear()
    assert len(result) == 2


def test_nested_mutable_containers_are_detached_and_frozen():
    source = {"items": [[1, 2]]}
    value = freeze(source)
    source["items"][0].append(3)
    assert value == {"items": [[1, 2]]}
    with pytest.raises(TypeError):
        value["other"] = 1
    with pytest.raises(TypeError):
        value["items"][0].append(3)
    with pytest.raises(TypeError):
        value.update({"other": 1})


async def test_cancelled_and_failed_inputs_remain_errors():
    import asyncio

    tape = ImmutableTape()

    async def failed():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await tape.capture("instruments", {}, failed)
    assert tape.calls[0].error == "CancelledError"
