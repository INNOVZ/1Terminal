import hashlib
from dataclasses import replace
from datetime import timedelta

import pytest

from algotrader.application.shadow_observer import ShadowObserver
from algotrader.infrastructure.scan_archive import LocalScanArchive, canonical_bytes
from algotrader.infrastructure.shadow_archive import LocalShadowArchive
from tests.test_forward_outcomes import ANCHOR, minute_path
from tests.test_shadow_archive import record_fixture
from tests.test_shadow_discovery import parent_fixture


def observation_fixture(root):
    parent = replace(
        parent_fixture(2),
        completed_at=ANCHOR - timedelta(minutes=2),
        started_at=ANCHOR - timedelta(minutes=3),
        opportunities=({"symbol": "T1USDT", "direction": "short"},),
    )
    child = replace(
        record_fixture(),
        cohort=parent.cohort,
        parent_checksum=hashlib.sha256(canonical_bytes(parent)).hexdigest(),
        started_at=ANCHOR - timedelta(minutes=1),
        completed_at=ANCHOR - timedelta(seconds=15),
        results=(
            {
                "symbol": "T0USDT",
                "qualified": True,
                "direction": "long",
                "suggested_entry": "100",
                "suggested_stop": "95",
                "suggested_targets": ["105", "110"],
            },
        ),
    )
    LocalScanArchive(root).write_scan(parent)
    archive = LocalShadowArchive(root)
    archive.write_run(child)
    return archive, parent, child


class History:
    def __init__(self):
        self.calls = []
        self.missing = True

    async def minute_candles_between(self, instrument, start, end):
        assert start == ANCHOR
        self.calls.append(instrument.id.symbol)
        if self.missing and instrument.id.symbol == "T1USDT":
            raise RuntimeError("delisted")
        return minute_path(hours=int((end - start).total_seconds() / 3600))


@pytest.mark.parametrize("order", [("T0USDT",), ("T0USDT", "T0USDT"), ("T0USDT", "OTHERUSDT")])
async def test_traversal_order_cannot_reduce_or_replace_frozen_cohort(tmp_path, order):
    archive, _, child = observation_fixture(tmp_path)
    history = History()
    with pytest.raises(ValueError, match="whole-cohort"):
        await ShadowObserver(archive, history, pace_seconds=0).observe(
            child.shadow_id, 24, 2, now=ANCHOR + timedelta(hours=25), symbol_order=order
        )
    assert history.calls == []
    assert archive.outcome_attempts(child.shadow_id, 24, "T0USDT") == ()


async def test_reordered_traversal_preserves_complete_measurements_and_denominator(tmp_path):
    archive, parent, child = observation_fixture(tmp_path)
    history = History()
    history.missing = False
    summary = await ShadowObserver(archive, history, pace_seconds=0).observe(
        child.shadow_id, 24, 2, now=ANCHOR + timedelta(hours=25), symbol_order=("T1USDT", "T0USDT")
    )
    assert history.calls == ["T1USDT", "T0USDT"]
    assert summary["total"] == summary["observed"] == 2
    assert summary["full_coverage"] is True
    assert LocalScanArchive(tmp_path).read_scan(parent.scan_id) == parent
    for symbol in ("T0USDT", "T1USDT"):
        path = archive.outcome_attempts(child.shadow_id, 24, symbol)[0]["observation"]
        assert str(path["raw_return_pct"]) == "10.0"


async def test_child_outcomes_preserve_cohort_parent_and_separate_anchor(tmp_path):
    archive, parent, child = observation_fixture(tmp_path)
    history = History()
    observer = ShadowObserver(archive, history, pace_seconds=0)
    first = await observer.observe(child.shadow_id, 24, 2, now=ANCHOR + timedelta(hours=25))
    assert first["anchor"] == ANCHOR
    assert first["parent_anchor"] == ANCHOR - timedelta(minutes=2)
    assert first["total"] == 2 and first["observed"] == 1
    assert first["missing"] == {"T1USDT": "unavailable"}
    assert first["qualified_symbols"] == ("T0USDT",)
    assert first["baseline_symbols"] == ("T1USDT",)
    assert first["comparison"] == "staggered_shadow"
    history.missing = False
    second = await observer.observe(child.shadow_id, 24, 2, now=ANCHOR + timedelta(hours=26))
    assert second["full_coverage"]
    assert history.calls == ["T0USDT", "T1USDT", "T1USDT"]
    assert second["attempts"]["T0USDT"] == first["attempts"]["T0USDT"]
    assert LocalScanArchive(tmp_path).read_scan(parent.scan_id) == parent
    assert not (tmp_path / "outcomes").exists()
    attempt = archive.outcome_attempts(child.shadow_id, 24, "T0USDT")[0]
    assert attempt["touches"]["ambiguous_order"]


async def test_child_limit_and_not_due_never_shrink_cohort_or_request_history(tmp_path):
    archive, _, child = observation_fixture(tmp_path)
    history = History()
    observer = ShadowObserver(archive, history, pace_seconds=0)
    with pytest.raises(ValueError, match="cohort"):
        await observer.observe(child.shadow_id, 24, 1, now=ANCHOR + timedelta(hours=25))
    summary = await observer.observe(child.shadow_id, 48, 2, now=ANCHOR + timedelta(hours=1))
    assert summary["missing"] == {"T0USDT": "not_due", "T1USDT": "not_due"}
    assert not history.calls


async def test_48h_gap_and_incompatible_cache_are_not_successes(tmp_path):
    archive, _, child = observation_fixture(tmp_path)

    class Gapped(History):
        async def minute_candles_between(self, instrument, start, end):
            return (await super().minute_candles_between(instrument, start, end))[:-1]

    history = Gapped()
    history.missing = False
    observer = ShadowObserver(archive, history, pace_seconds=0)
    result = await observer.observe(child.shadow_id, 48, 2, now=ANCHOR + timedelta(hours=49))
    assert result["observed"] == 0
    assert result["missing"] == {"T0USDT": "missing", "T1USDT": "missing"}
    successful_history = History()
    successful_history.missing = False
    observer = ShadowObserver(archive, successful_history, pace_seconds=0)
    complete = await observer.observe(child.shadow_id, 48, 2, now=ANCHOR + timedelta(hours=50))
    assert complete["full_coverage"]
    assert len(successful_history.calls) == 2
