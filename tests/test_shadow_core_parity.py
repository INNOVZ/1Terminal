from dataclasses import replace
from datetime import timedelta

import pytest

from algotrader.application.scan_replay import replay_scan
from algotrader.application.shadow_budget import ShadowBudgetExceeded
from algotrader.application.shadow_observer import ShadowObserver
from algotrader.application.shadow_replay import replay_shadow
from algotrader.application.shadow_runner import ShadowRunner
from algotrader.domain.forward_outcomes import outcome_anchor
from algotrader.domain.shadow_discovery import ShadowPolicy
from algotrader.infrastructure.scan_archive import LocalScanArchive
from algotrader.infrastructure.shadow_archive import LocalShadowArchive
from tests.test_forward_outcomes import ANCHOR, minute_path
from tests.test_scan_replay import captured_fixture
from tests.test_shadow_discovery import NOW
from tests.test_shadow_runner import Market, runner_parent


@pytest.mark.parametrize("failure", [False, True])
async def test_frozen_nonempty_core_replays_identically_around_child_run(
    settings, monkeypatch, failure
):
    baseline = (await captured_fixture(settings)).record
    assert baseline.opportunities
    assert (await replay_scan(baseline)).matches
    policy = ShadowPolicy(max_tokens=1)

    class MaybeLimited(Market):
        async def candles(self, *args):
            if failure:
                raise ShadowBudgetExceeded("run limit")
            return await super().candles(*args)

    await ShadowRunner(MaybeLimited(policy), clock=lambda: NOW).run(runner_parent(settings), policy)

    def forbidden(*args, **kwargs):
        raise AssertionError("offline parity tried HTTP")

    monkeypatch.setattr("httpx.AsyncClient", forbidden)
    after = await replay_scan(baseline)
    assert after.matches
    assert after.counts == baseline.counts


async def test_full_child_archive_replay_and_outcome_flow_never_writes_latest(settings, tmp_path):
    parent = runner_parent(settings)
    core = LocalScanArchive(tmp_path)
    core.write_scan(parent)
    parent_path = tmp_path / "scans" / parent.scan_id / "payload.json"
    before = parent_path.read_bytes()
    latest = tmp_path / "screener_latest.json"
    latest.write_text("user-owned scan sentinel")
    policy = ShadowPolicy(max_tokens=1)
    child = await ShadowRunner(Market(policy), clock=lambda: NOW).run(parent, policy)
    archive = LocalShadowArchive(tmp_path)
    archive.write_run(child)
    stored = archive.read_run(child.shadow_id)
    assert (await replay_shadow(stored, core.read_scan(parent.scan_id)))["matches"]
    anchor = outcome_anchor(child.completed_at)

    class History:
        async def minute_candles_between(self, instrument, start, end):
            shift = start - ANCHOR
            return [
                replace(bar, opened_at=bar.opened_at + shift, closed_at=bar.closed_at + shift)
                for bar in minute_path()
            ]

    summary = await ShadowObserver(archive, History(), pace_seconds=0).observe(
        child.shadow_id, 24, 20, now=anchor + timedelta(hours=25)
    )
    assert summary["full_coverage"] and summary["total"] == 20
    assert parent_path.read_bytes() == before
    assert latest.read_text() == "user-owned scan sentinel"
    assert not (tmp_path / "outcomes").exists()
