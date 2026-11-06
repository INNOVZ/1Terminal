import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from algotrader.application.scan_replay import ReplayMismatch
from algotrader.application.shadow_replay import replay_shadow
from algotrader.application.shadow_runner import ShadowRunner
from algotrader.domain.shadow_discovery import ShadowPolicy
from algotrader.infrastructure.shadow_archive import LocalShadowArchive
from tests.test_shadow_discovery import NOW
from tests.test_shadow_runner import Market, runner_parent


@pytest.mark.parametrize("kind", ["degraded", "stale"])
async def test_unavailable_derivatives_are_not_complete_negative_observations(settings, kind):
    policy = ShadowPolicy(max_tokens=1)

    class Unavailable(Market):
        async def derivatives_snapshot(self, instrument):
            value = await super().derivatives_snapshot(instrument)
            return (
                replace(value, degraded_telemetry=True)
                if kind == "degraded"
                else replace(value, observed_at=NOW - timedelta(days=1))
            )

    record = await ShadowRunner(Unavailable(policy), clock=lambda: NOW).run(
        runner_parent(settings), policy
    )
    assert record.status == "partial"
    assert any(event["reason"] == "derivatives_unavailable" for event in record.journal)
    assert not any(event["reason"] == "no_setup" for event in record.journal)


@pytest.mark.parametrize("kind", ["empty", "old_context", "old_component"])
async def test_empty_and_stale_macro_evidence_is_explicitly_unavailable(settings, kind):
    from algotrader.adapters.external.models import FearGreedReading, MacroSentimentContext

    context = MacroSentimentContext(observed_at=NOW)
    if kind != "empty":
        context = MacroSentimentContext(
            fear_greed=FearGreedReading(
                55, "Neutral", NOW if kind == "old_context" else NOW - timedelta(days=365)
            ),
            observed_at=NOW - timedelta(days=365) if kind == "old_context" else NOW,
        )
    parent = runner_parent(settings)
    parent = replace(
        parent,
        inputs=tuple(
            replace(call, result=context) if call.method == "macro" else call
            for call in parent.inputs
        ),
    )
    policy = ShadowPolicy(max_tokens=1)
    market = Market(policy)
    record = await ShadowRunner(market, clock=lambda: NOW).run(parent, policy)
    assert record.status == "partial"
    assert not market.calls
    assert any(event["reason"] == "context_unavailable" for event in record.journal)


@pytest.mark.parametrize("phase", ["acquisition", "evaluation"])
async def test_real_deadline_cause_replays_instead_of_becoming_external_cancellation(
    settings, monkeypatch, phase
):
    policy = ShadowPolicy(max_tokens=1, deadline_seconds=1)
    parent = runner_parent(settings)

    class Slow(Market):
        async def candles(self, *args):
            if phase == "acquisition":
                await asyncio.Event().wait()
            return await super().candles(*args)

    async def blocked(*args, **kwargs):
        await asyncio.Event().wait()

    if phase == "evaluation":
        monkeypatch.setattr(
            "algotrader.application.shadow_runner.CryptoOpportunityScreener._evaluate_single",
            blocked,
        )
    record = await ShadowRunner(Slow(policy), clock=lambda: NOW).run(parent, policy)
    assert record.status == "partial" and record.error == "TimeoutError"
    monkeypatch.undo()
    assert (await replay_shadow(record, parent))["matches"]


async def test_replay_rejects_child_configuration_drift(settings):
    parent = runner_parent(settings)
    policy = ShadowPolicy(max_tokens=1)
    record = await ShadowRunner(Market(policy), clock=lambda: NOW).run(parent, policy)
    changed = replace(
        record, identity={**record.identity, "configuration": {"excluded_base_assets": ("T10",)}}
    )
    with pytest.raises(ReplayMismatch, match="configuration"):
        await replay_shadow(changed, parent)


async def test_snapshot_acquisition_times_survive_archive_and_replay(settings, tmp_path):
    parent = runner_parent(settings)
    policy = ShadowPolicy(max_tokens=1)
    record = await ShadowRunner(Market(policy), clock=lambda: NOW).run(parent, policy)
    assert record.timing["snapshot_requested_at"] == NOW
    assert record.timing["snapshot_arrived_at"] == NOW
    archive = LocalShadowArchive(tmp_path)
    archive.write_run(record)
    stored = archive.read_run(record.shadow_id)
    assert stored.timing == record.timing
    assert (await replay_shadow(stored, parent))["matches"]


async def test_empty_weekly_listing_history_keeps_core_resampling_fallback(settings):
    policy = ShadowPolicy(max_tokens=1)

    class NewListing(Market):
        async def candles(self, instrument, interval, limit):
            if interval == "1w":
                return []
            return await super().candles(instrument, interval, limit)

    record = await ShadowRunner(NewListing(policy), clock=lambda: NOW).run(
        runner_parent(settings), policy
    )
    assert record.status == "complete", record.journal
    assert any(event["reason"] == "weekly_resampled_from_4h" for event in record.journal)
