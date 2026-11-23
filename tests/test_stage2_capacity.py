"""Allocation -> bounded acquisition -> publication -> recorded replay regressions."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.application.scan_recording import (
    capture_scan,
    screening_configuration,
    supported_screening_configuration,
)
from algotrader.application.scan_replay import replay_scan
from algotrader.config import Settings
from algotrader.domain.models import DerivativesSnapshot, Direction, MarketKind, Venue
from algotrader.domain.scan_observations import ScanDecisionJournal
from algotrader.domain.stage2_allocation import stage2_capacity
from tests.conftest import make_instrument
from tests.test_crypto_screener import FakeCryptoMarketData
from tests.test_scan_replay import Macro
from tests.test_screener_publication_policy import _opportunity

D = Decimal
NOW = datetime(2026, 11, 23, tzinfo=UTC)
PROTOTYPE = replace(
    _opportunity(Direction.LONG), suggested_stop=D("95"), suggested_targets=(D("110"), D("115"), D("120")),
    risk_reward_ratio=D("3"), taker_ratio=D("1.1"), evaluated_at=NOW,
)


class AllocationScreener(CryptoOpportunityScreener):
    async def _evaluate_single(self, **kw):
        inst = kw["inst"]
        symbol = inst.id.symbol
        if kw.get("explicit_derivatives") is not None:
            result = replace(PROTOTYPE, instrument=inst)
            if symbol == "PROBE040USDT":
                result = replace(result, conviction=D("1"))
            if symbol == "PROBE041USDT":
                result = replace(result, suggested_stop=D("101"))
            return [result]
        if kw.get("candle_cache") is not None:
            kw["candle_cache"][symbol] = ((), (), ())
        if symbol.startswith("SHORTLIST"):
            return [replace(PROTOTYPE, instrument=inst)]
        self._stage1_ready_symbols.add(symbol)
        return []


class Derivatives:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []
        self.active = self.peak = 0

    async def derivatives_snapshot(self, instrument):
        self.calls.append(instrument.id.symbol)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.001)
            if self.failure == "exception":
                raise TimeoutError("synthetic timeout")
            if self.failure == "missing":
                return None
            return DerivativesSnapshot(
                funding_rate=D("0"), basis_rate=D("0"), open_interest=D("1000000"),
                open_interest_change_1h=D("0.01"), taker_buy_sell_ratio=D("1.1"),
                bid_depth_notional=D("1000000"), ask_depth_notional=D("1000000"),
                open_interest_change_7d=D("0.1"), degraded_telemetry=self.failure == "degraded",
            )
        finally:
            self.active -= 1


class ProbeMarket(FakeCryptoMarketData):
    async def quotes(self, instruments):
        return [replace(q, funding_rate=D("-0.0002")) for q in await super().quotes(instruments)]


def market(shortlist, probes, reverse=False):
    symbols = [f"SHORTLIST{i:03}USDT" for i in range(shortlist)] + [f"PROBE{i:03}USDT" for i in range(probes)]
    if reverse:
        symbols.reverse()
    instruments = [make_instrument(symbol=s, venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES) for s in symbols]
    return ProbeMarket(instruments, {}, {})


@pytest.mark.parametrize(("shortlist", "rollover", "discovery", "expected"), [
    (13, True, True, 62), (45, True, True, 30), (0, True, True, 75),
    (13, False, True, 30), (13, True, False, 15),
])
async def test_actual_pipeline_capacity_bounds(shortlist, rollover, discovery, expected):
    journal = ScanDecisionJournal()
    settings = Settings(_env_file=None, stage2_capacity_rollover_enabled=rollover)
    engine = AllocationScreener(settings, Macro(), journal=journal, feature_contract="discovery_v2" if discovery else "legacy")
    derivatives = Derivatives()
    result = await engine.screen(market(shortlist, 100), derivatives, max_symbols=200, enforce_quality=True)
    assert len(derivatives.calls) == shortlist + expected
    assert len(derivatives.calls) <= engine.scan_counts["stage2_budget"]
    assert derivatives.peak <= 8
    assert engine.scan_counts["stage2_probes"] == expected
    if discovery:
        assert engine.scan_counts["stage2_probe_overflow"] == 100 - expected
    else:
        assert "stage2_probe_overflow" not in engine.scan_counts
    assert result
    # Newly inspected weak candidates still fail the shared, real publication policy.
    assert not {"PROBE040USDT", "PROBE041USDT"} & {o.instrument.id.symbol for o in result}
    events = [e for e in journal.snapshot() if e["stage"] == "stage2" and e["reason"] == "admitted"]
    assert len(events) == shortlist + expected
    assert all(e["allocation_policy"] for e in events)


async def test_spare_capacity_does_not_create_calls_when_probes_unavailable_or_ineligible():
    engine = AllocationScreener(Settings(_env_file=None), Macro(), feature_contract="discovery_v2")
    await engine.screen(market(13, 0), Derivatives(), max_symbols=200)
    assert engine.scan_counts["stage2_probes"] == 0
    assert engine.scan_counts["stage2_unused_budget"] == 62
    await engine.screen(market(13, 100), None, max_symbols=200)
    assert engine.scan_counts["stage2_probe_limit"] == 0
    assert engine.scan_counts["stage2_probes"] == 0


@pytest.mark.parametrize("failure", ["missing", "degraded", "exception"])
async def test_shortlist_never_publishes_stage1_fallback_on_enrichment_failure(failure):
    engine = AllocationScreener(Settings(_env_file=None), Macro(), feature_contract="discovery_v2")
    result = await engine.screen(market(1, 0), Derivatives(failure), enforce_quality=True)
    assert result == []
    assert engine.scan_counts["stage2_derivatives_attempted"] == 1
    assert engine.scan_counts["stage2_derivatives_unavailable"] == 1


async def test_missing_history_cache_fails_closed_without_wasting_derivative_requests():
    class MissingCacheScreener(AllocationScreener):
        async def _evaluate_single(self, **kw):
            result = await super()._evaluate_single(**kw)
            if kw.get("candle_cache") is not None:
                kw["candle_cache"].clear()
            return result

    derivatives = Derivatives()
    engine = MissingCacheScreener(Settings(_env_file=None), Macro(), feature_contract="discovery_v2")
    assert await engine.screen(market(1, 0), derivatives, enforce_quality=True) == []
    assert derivatives.calls == []


async def test_full_320_symbol_funnel_still_bounds_acquisition_to_75():
    class FullUniverseMarket(ProbeMarket):
        async def quotes(self, instruments):
            quotes = await super().quotes(instruments)
            return [replace(q, quote_volume_24h=D("1000000"))
                    if q.instrument_id.symbol.startswith("PROBE") and int(q.instrument_id.symbol[5:8]) >= 287
                    else q for q in quotes]

    original = market(13, 307)
    full = FullUniverseMarket(original._instruments, {}, {})
    derivatives = Derivatives()
    engine = AllocationScreener(Settings(_env_file=None), Macro(), feature_contract="discovery_v2")
    await engine.screen(full, derivatives, max_symbols=320, enforce_quality=True)
    assert engine.scan_counts["selected"] == 320
    assert len(derivatives.calls) == 75
    assert derivatives.peak <= 8
    assert engine.scan_counts["stage2_unused_budget"] == 0


async def test_rollover_is_deterministic_under_input_permutation():
    admitted = []
    for reverse in (False, True):
        derivatives = Derivatives()
        engine = AllocationScreener(Settings(_env_file=None), Macro(), feature_contract="discovery_v2")
        await engine.screen(market(13, 100, reverse), derivatives, max_symbols=200)
        admitted.append(set(derivatives.calls))
    assert admitted[0] == admitted[1]


@pytest.mark.parametrize("rollover", [False, True])
async def test_recorded_rollback_setting_and_actual_enrichment_calls_replay(monkeypatch, rollover):
    import algotrader.application.crypto_screener as module

    monkeypatch.setattr(module, "CryptoOpportunityScreener", AllocationScreener)
    settings = Settings(_env_file=None, stage2_capacity_rollover_enabled=rollover)
    captured = await capture_scan(market(13, 100), Derivatives(), Macro(), settings,
                                  {"max_symbols": 200, "enforce_quality": True}, feature_contract="discovery_v2")
    assert captured.failure is None
    assert captured.record.identity["configuration"]["stage2_capacity_rollover_enabled"] is rollover
    assert len([c for c in captured.record.inputs if c.method == "derivatives_snapshot"]) == (75 if rollover else 43)
    assert (await replay_scan(captured.record)).matches


def test_capacity_and_recording_configuration_reject_invalid_inputs():
    for size in (-1, 46):
        with pytest.raises(ValueError):
            stage2_capacity(size, discovery=True, probes_enabled=True, rollover_enabled=True)
    config = screening_configuration(Settings(_env_file=None))
    assert supported_screening_configuration(config)
    assert not supported_screening_configuration({**config, "unknown": True})
    assert not supported_screening_configuration({**config, "stage2_capacity_rollover_enabled": "false"})
