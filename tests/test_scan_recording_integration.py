from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.application.scan_recording import capture_scan
from algotrader.application.scan_replay import replay_scan
from algotrader.config import Settings
from algotrader.domain.crypto_screener_models import ScreenerMode
from algotrader.domain.models import MarketKind, Quote, Venue
from algotrader.domain.scan_observations import ScanDecisionJournal
from algotrader.infrastructure.runtime import Runtime
from algotrader.infrastructure.scan_archive import LocalScanArchive
from tests.conftest import make_instrument
from tests.test_crypto_screener import FakeCryptoMarketData
from tests.test_scan_replay import Macro, market_fixture
from tests.test_screener_publication_policy import _opportunity


async def test_journal_retains_eligible_unselected_and_excluded_symbols_without_changing_selection(settings: Settings) -> None:
    market = market_fixture()
    excluded = replace(market._instruments[0], base_asset="USDC", id=replace(market._instruments[0].id, symbol="USDCUSDT"))
    market._instruments.append(excluded)
    captured = await capture_scan(market, None, Macro(), settings, {"max_symbols": 1, "mode": ScreenerMode.ALL})
    assert {inst.id.symbol for inst in captured.record.cohort} == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    assert any(event["reason"] == "selection_limit" for event in captured.record.decisions)
    assert any(event["symbol"] == "USDCUSDT" and event["reason"] == "universe_excluded" for event in captured.record.decisions)
    assert (await replay_scan(captured.record)).matches


async def test_journal_records_publication_rejection_and_desk_caps(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    opportunities = [replace(_opportunity(), instrument=make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol=f"TOKEN{i}USDT")) for i in range(9)]
    opportunities[0] = replace(opportunities[0], risk_reward_ratio=Decimal("1.0"))
    class Market(FakeCryptoMarketData):
        async def quotes(self, instruments: Any) -> list[Quote]:
            return [Quote(inst.id, Decimal("99.95"), Decimal("100.05"), Decimal("100"), Decimal("5000000")) for inst in instruments]
    market = Market([opp.instrument for opp in opportunities], {}, {})
    async def evaluate(*, inst: Any, **kwargs: Any) -> list[Any]:
        return [next(opp for opp in opportunities if opp.instrument == inst)]
    monkeypatch.setattr(CryptoOpportunityScreener, "_evaluate_single", staticmethod(evaluate))
    journal = ScanDecisionJournal()
    engine = CryptoOpportunityScreener(settings, Macro(), journal=journal)
    result = await engine.screen(market, mode=ScreenerMode.ALL, enforce_quality=True)
    plain = await CryptoOpportunityScreener(settings, Macro()).screen(market, mode=ScreenerMode.ALL, enforce_quality=True)
    assert result == plain
    assert len(result) == 6
    assert any(event["reason"] == "publication:reward_risk_floor" for event in journal.events)
    assert sum(event["reason"] == "desk_cap" for event in journal.events) == 2


def runtime_at(tmp_path: Path, market: Any) -> Runtime:
    settings = Settings(**{"_env_file": None, "research_archive_dir": tmp_path})
    return Runtime(settings, (), market, market, Macro())  # type: ignore[arg-type]


async def test_concurrent_runtime_scans_get_their_own_durable_receipts(tmp_path: Path) -> None:
    runtime = runtime_at(tmp_path, FakeCryptoMarketData([], {}, {}))
    a, b = await asyncio.gather(runtime.run_crypto_scan(), runtime.run_crypto_scan())
    assert a.receipt.scan_id != b.receipt.scan_id
    assert a.receipt.status == b.receipt.status == "saved"
    archive = LocalScanArchive(tmp_path)
    assert set(archive.scan_ids()) == {a.receipt.scan_id, b.receipt.scan_id}
    assert archive.read_scan(a.receipt.scan_id).counts == a.counts


async def test_archive_failure_is_explicit_and_does_not_change_screening_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = runtime_at(tmp_path, FakeCryptoMarketData([], {}, {}))
    def fail(*args: Any) -> None:
        raise PermissionError("secret filesystem location")
    monkeypatch.setattr(LocalScanArchive, "write_scan", fail)
    run = await runtime.run_crypto_scan()
    assert run.opportunities == []
    assert run.receipt.status == "unavailable"
    assert run.receipt.error == "PermissionError"


async def test_failed_provider_scan_archives_partial_inputs_before_propagating(tmp_path: Path) -> None:
    market = FakeCryptoMarketData([], {}, {})
    market.instruments = AsyncMock(side_effect=TimeoutError("provider unavailable"))
    runtime = runtime_at(tmp_path, market)
    with pytest.raises(TimeoutError):
        await runtime.run_crypto_scan()
    archive = LocalScanArchive(tmp_path)
    (scan_id,) = archive.scan_ids()
    record = archive.read_scan(scan_id)
    assert record.status == "failed"
    assert record.inputs[-1].error == "TimeoutError"
    assert record.error == "TimeoutError"


@pytest.mark.parametrize("failure_site", ["identity", "copy"])
async def test_recording_failures_preserve_nonempty_screening_without_restarting_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_site: str) -> None:
    from algotrader.adapters.external.models import MacroSentimentContext
    from algotrader.application import scan_recording
    opportunity = _opportunity()
    market = FakeCryptoMarketData([opportunity.instrument], {}, {})
    from algotrader.domain.models import DerivativesSnapshot
    market.derivatives_snapshot = AsyncMock(return_value=DerivativesSnapshot(
        Decimal("0.0001"), Decimal("0"), Decimal("1000000"), Decimal("0.01"),
        Decimal("0.9"), Decimal("100000"), Decimal("100000"), open_interest_change_7d=Decimal("0.1")))
    original_instruments = market.instruments
    calls = []
    async def instruments():
        calls.append("instruments")
        return await original_instruments()
    market.instruments = instruments
    async def evaluate(*, inst, candle_cache=None, **kwargs):
        if candle_cache is not None:
            candle_cache[inst.id.symbol] = ((), (), ())
        return [opportunity]
    monkeypatch.setattr(CryptoOpportunityScreener, "_evaluate_single", staticmethod(evaluate))
    if failure_site == "identity":
        def unavailable_identity():
            raise PermissionError("do not expose this filesystem path")
        monkeypatch.setattr(scan_recording, "screening_identity", unavailable_identity)
        expected_error = "PermissionError"
    else:
        original_encode = scan_recording.encode_value
        def broken_copy(value):
            if isinstance(value, MacroSentimentContext):
                raise ValueError("cannot record context")
            return original_encode(value)
        monkeypatch.setattr(scan_recording, "encode_value", broken_copy)
        expected_error = "ValueError"
    run = await runtime_at(tmp_path, market).run_crypto_scan(mode=ScreenerMode.ALL)
    assert run.opportunities == [opportunity]
    assert calls == ["instruments"]
    assert run.receipt.status == "unavailable"
    assert run.receipt.error == expected_error
    archived = LocalScanArchive(tmp_path).read_scan(run.receipt.scan_id)
    assert archived.status == "incomplete"


async def test_canonical_cohort_tracks_the_published_liquid_pair_and_its_outcome(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import timedelta

    from algotrader.application.forward_observer import ForwardObserver
    from algotrader.domain.forward_outcomes import outcome_anchor
    from tests.test_forward_outcomes import ANCHOR, minute_path
    usdt = _opportunity().instrument
    usdc = replace(usdt, id=replace(usdt.id, symbol="TESTUSDC"), quote_asset="USDC")
    class Market(FakeCryptoMarketData):
        async def quotes(self, instruments):
            return [Quote(usdt.id, Decimal("99"), Decimal("101"), Decimal("100"), Decimal("10000000")),
                    Quote(usdc.id, Decimal("99.95"), Decimal("100.05"), Decimal("100"), Decimal("5000000"))]
    monkeypatch.setattr(CryptoOpportunityScreener, "_evaluate_single", AsyncMock(return_value=[replace(_opportunity(), instrument=usdc)]))
    captured = await capture_scan(Market([usdt, usdc], {}, {}), None, Macro(), settings, {"enforce_quality": True})
    assert [inst.id.symbol for inst in captured.record.cohort] == ["TESTUSDC"]
    assert captured.record.opportunities[0]["symbol"] == "TESTUSDC"
    archive = LocalScanArchive(tmp_path)
    archive.write_scan(captured.record)
    anchor = outcome_anchor(captured.record.completed_at)
    class History:
        async def minute_candles_between(self, instrument, start, end):
            assert instrument.id.symbol == "TESTUSDC"
            return [replace(bar, opened_at=anchor + (bar.opened_at - ANCHOR), closed_at=anchor + (bar.closed_at - ANCHOR)) for bar in minute_path()]
    result = await ForwardObserver(archive, History(), pace_seconds=0).observe(captured.record.scan_id, 24, 1, now=anchor + timedelta(hours=25))
    assert result["full_coverage"] is True
    (attempt,) = archive.outcome_attempts(captured.record.scan_id, 24, "TESTUSDC")
    assert attempt["observation"]["directional_return_pct"] == Decimal("-10")
    assert attempt["touches"] is not None


async def test_exhausted_fetches_are_not_mislabelled_as_market_rejection(settings: Settings) -> None:
    inst = _opportunity().instrument
    class FailedMarket(FakeCryptoMarketData):
        async def candles(self, instrument, interval, limit):
            raise TimeoutError("provider unavailable")
    captured = await capture_scan(FailedMarket([inst], {}, {}), None, Macro(), settings, {})
    facts = [event for event in captured.record.decisions if event["symbol"] == inst.id.symbol]
    assert any(event["reason"] == "fetch_exhausted" for event in facts)
    assert not any(event["stage"] == "stage1" and event["reason"] == "no_setup" for event in facts)


async def test_recovered_candle_retries_are_not_recorded_as_terminal_fetch_failure(settings: Settings) -> None:
    inst = _opportunity().instrument
    class RecoveringMarket(FakeCryptoMarketData):
        attempts = {}
        async def candles(self, instrument, interval, limit):
            self.attempts[interval] = self.attempts.get(interval, 0) + 1
            if self.attempts[interval] == 1:
                raise TimeoutError("transient outage")
            return []
    captured = await capture_scan(RecoveringMarket([inst], {}, {}), None, Macro(), settings, {})
    assert all(event["reason"] != "fetch_exhausted" for event in captured.record.decisions)
    assert (await replay_scan(captured.record)).matches
