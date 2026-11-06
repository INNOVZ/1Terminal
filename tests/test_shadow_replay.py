from dataclasses import replace

import pytest

from algotrader.application.scan_replay import ReplayMismatch
from algotrader.application.shadow_replay import replay_shadow
from algotrader.application.shadow_runner import ShadowRunner
from algotrader.domain.shadow_discovery import ShadowPolicy
from tests.test_shadow_discovery import NOW
from tests.test_shadow_runner import Market, runner_parent


async def test_shadow_replay_is_exact_and_never_constructs_http_clients(settings, monkeypatch):
    parent = runner_parent(settings)
    policy = ShadowPolicy(max_tokens=1)
    record = await ShadowRunner(Market(policy), clock=lambda: NOW).run(parent, policy)

    def forbidden(*args, **kwargs):
        raise AssertionError("offline replay attempted network construction")

    monkeypatch.setattr("httpx.AsyncClient", forbidden)
    assert (await replay_shadow(record, parent))["matches"]
    for broken in (
        replace(record, inputs=record.inputs[:-1]),
        replace(record, parent_checksum="bad"),
        replace(record, identity={**record.identity, "source_hash": "bad"}),
    ):
        with pytest.raises(ReplayMismatch):
            await replay_shadow(broken, parent)


async def test_budget_ended_child_replays_failure_without_live_fallback(settings):
    from algotrader.application.shadow_budget import ShadowBudgetExceeded

    policy = ShadowPolicy(max_tokens=1)
    parent = runner_parent(settings)

    class Limited(Market):
        async def candles(self, *args):
            raise ShadowBudgetExceeded("exhausted")

    record = await ShadowRunner(Limited(policy), clock=lambda: NOW).run(parent, policy)
    assert (await replay_shadow(record, parent))["matches"]
