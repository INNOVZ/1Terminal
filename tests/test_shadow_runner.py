import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from algotrader.adapters.external.models import FearGreedReading, MacroSentimentContext
from algotrader.application.scan_recording import screening_configuration, screening_identity
from algotrader.application.shadow_budget import ShadowBudget, ShadowBudgetExceeded
from algotrader.application.shadow_runner import ShadowRunner
from algotrader.domain.models import DerivativesSnapshot
from algotrader.domain.scan_observations import RecordedCall
from algotrader.domain.shadow_discovery import ShadowPolicy
from algotrader.infrastructure.scan_archive import canonical_bytes
from tests.conftest import trending_candles
from tests.test_shadow_discovery import NOW, parent_fixture, ticker_fixture


class Market:
    def __init__(self, policy):
        self.budget = ShadowBudget(policy)
        self.calls = []

    async def candles(self, instrument, interval, limit):
        self.calls.append(("candles", instrument.id.symbol, interval, limit))
        bars = trending_candles(count=limit)
        hours = {"1h": 1, "4h": 4, "1w": 168}[interval]
        return [
            replace(
                bar,
                opened_at=NOW - timedelta(hours=(limit - i) * hours),
                closed_at=NOW - timedelta(hours=(limit - i - 1) * hours),
            )
            for i, bar in enumerate(bars)
        ]

    async def derivatives_snapshot(self, instrument):
        self.calls.append(("derivatives", instrument.id.symbol))
        return DerivativesSnapshot(
            funding_rate=Decimal("-0.001"),
            basis_rate=Decimal("0.01"),
            open_interest=Decimal("10000000"),
            open_interest_change_1h=Decimal("0.02"),
            taker_buy_sell_ratio=Decimal("1.1"),
            bid_depth_notional=Decimal("1000000"),
            ask_depth_notional=Decimal("900000"),
            observed_at=NOW,
        )

    async def decision_tickers(self):
        return ticker_fixture(parent_fixture())


def runner_parent(settings):
    parent = parent_fixture()
    identity = {**screening_identity(), "configuration": screening_configuration(settings)}
    btc = replace(
        parent.cohort[0], id=replace(parent.cohort[0].id, symbol="BTCUSDT"), base_asset="BTC"
    )
    bars = trending_candles(count=240)
    bars = [
        replace(
            bar, opened_at=NOW - timedelta(hours=240 - i), closed_at=NOW - timedelta(hours=239 - i)
        )
        for i, bar in enumerate(bars)
    ]
    return replace(
        parent,
        identity=identity,
        inputs=parent.inputs
        + (
            RecordedCall(
                "macro",
                0,
                {},
                MacroSentimentContext(
                    fear_greed=FearGreedReading(55, "Neutral", NOW), observed_at=NOW
                ),
            ),
            RecordedCall("candles", 0, {"instrument": btc, "interval": "1h", "limit": 240}, bars),
        ),
    )


async def test_runner_preserves_parent_and_preloads_derivatives(settings):
    parent = runner_parent(settings)
    before = canonical_bytes(parent)
    policy = ShadowPolicy(max_tokens=1)
    market = Market(policy)
    record = await ShadowRunner(market, clock=lambda: NOW).run(parent, policy)
    assert record.status == "complete", (record.error, record.journal)
    assert canonical_bytes(parent) == before
    assert record.parent_id == parent.scan_id
    assert record.timing["comparison"] == "staggered_shadow"
    assert record.admission[10]["symbol"]  # all decisions retained, not only winners
    assert len(record.cohort) == 20
    assert any(call[0] == "derivatives" for call in market.calls)
    assert record.inputs
    assert record.coverage["complete"]


async def test_missing_context_is_explicit_not_neutral_or_network_rescan(settings):
    parent = runner_parent(settings)
    parent = replace(parent, inputs=parent.inputs[:1])
    market = Market(ShadowPolicy(max_tokens=1))
    record = await ShadowRunner(market, clock=lambda: NOW).run(parent, ShadowPolicy(max_tokens=1))
    assert record.status == "partial"
    assert not market.calls
    assert any(event["reason"] == "context_unavailable" for event in record.journal)


async def test_budget_error_is_archived_as_partial_and_source_drift_is_rejected(settings):
    parent = runner_parent(settings)
    policy = ShadowPolicy(max_tokens=1)

    class Limited(Market):
        async def candles(self, *args):
            raise ShadowBudgetExceeded("attempt cap")

    record = await ShadowRunner(Limited(policy), clock=lambda: NOW).run(parent, policy)
    assert record.status == "partial"
    assert record.error == "ShadowBudgetExceeded"
    with pytest.raises(ValueError, match="identity"):
        await ShadowRunner(Market(policy), clock=lambda: NOW).run(
            replace(parent, identity={**parent.identity, "source_hash": "changed"}), policy
        )


async def test_cancelled_child_returns_persistable_evidence_without_mutating_parent(
    settings, tmp_path
):
    from algotrader.infrastructure.shadow_archive import LocalShadowArchive

    parent = runner_parent(settings)
    before = canonical_bytes(parent)
    policy = ShadowPolicy(max_tokens=1)

    class Cancelled(Market):
        async def candles(self, *args):
            raise asyncio.CancelledError

    record = await ShadowRunner(Cancelled(policy), clock=lambda: NOW).run(parent, policy)
    assert record.status == "cancelled"
    assert record.error == "CancelledError"
    archive = LocalShadowArchive(tmp_path)
    archive.write_run(record)
    assert archive.read_run(record.shadow_id) == record
    assert canonical_bytes(parent) == before


async def test_short_weekly_history_uses_existing_evaluator_fallback(settings):
    policy = ShadowPolicy(max_tokens=1)

    class Young(Market):
        async def candles(self, instrument, interval, limit):
            bars = await super().candles(instrument, interval, limit)
            return bars[-2:] if interval == "1w" else bars

    record = await ShadowRunner(Young(policy), clock=lambda: NOW).run(
        runner_parent(settings), policy
    )
    assert record.status == "complete", record.journal


async def test_future_closed_candles_are_not_passed_to_evaluator(settings):
    policy = ShadowPolicy(max_tokens=1)

    class Future(Market):
        async def candles(self, instrument, interval, limit):
            bars = await super().candles(instrument, interval, limit)
            return [*bars, replace(bars[-1], opened_at=NOW, closed_at=NOW + timedelta(hours=1))]

    record = await ShadowRunner(Future(policy), clock=lambda: NOW).run(
        runner_parent(settings), policy
    )
    assert record.status == "partial"
    assert any(event["reason"] == "input_or_evaluation_failure" for event in record.journal)
