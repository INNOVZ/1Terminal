from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.application.scan_replay import ReplayMismatch
from algotrader.domain.models import MarketKind, Quote, Venue
from algotrader.infrastructure.scan_archive import LocalScanArchive
from scripts.replay_contract import ReplayUnavailable, digest
from scripts.research_pipeline_comparison import (
    FundingCarryScreener,
    compare,
)
from tests.conftest import make_instrument, trending_candles
from tests.test_crypto_screener import FakeCryptoMarketData
from tests.test_verified_replay import derivative_case, fixture_record


async def comparison_fixture(settings):
    at = datetime.now(UTC)
    inst = make_instrument(
        symbol="SOLUSDT", venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES
    )

    def bars(count, hours):
        end = at.replace(hour=0, minute=0, second=0, microsecond=0)
        if hours == 168:
            end -= timedelta(days=at.weekday())
        else:
            end = at.replace(hour=at.hour // hours * hours, minute=0, second=0, microsecond=0)
        return [
            replace(
                c,
                opened_at=end - timedelta(hours=hours * (count - i)),
                closed_at=end - timedelta(hours=hours * (count - i - 1), milliseconds=1),
            )
            for i, c in enumerate(trending_candles(count=count))
        ]

    class Market(FakeCryptoMarketData):
        async def quotes(self, instruments):
            return [
                Quote(
                    inst.id,
                    Decimal("99.95"),
                    Decimal("100.05"),
                    Decimal("100"),
                    Decimal("15000000"),
                    funding_rate=Decimal("0"),
                    basis_pct=Decimal("0"),
                )
            ]

    market = Market([inst], {"SOLUSDT": bars(240, 1)}, {"SOLUSDT": bars(480, 4)})
    market._candles_1w = {"SOLUSDT": bars(40, 168)}
    record, manifest = await fixture_record(settings, market)
    for call in record.inputs:
        if call.method == "quotes":
            proof = next(p for p in manifest["calls"] if p["call_sha256"] == digest(call))
            proof["quote_funding"] = {
                q.instrument_id.symbol: {
                    "origin": "premium_index",
                    "interval_seconds": 14400,
                    "observed_at": proof["observed_at"],
                    "available_at": proof["available_at"],
                    "rate": str(q.funding_rate),
                }
                for q in call.result
            }
    return record, manifest


async def test_comparison_uses_actual_pipeline_and_preserves_control(settings, monkeypatch):
    record, manifest = await comparison_fixture(settings)
    seen = []
    original = FundingCarryScreener._accumulation_funding_rate

    def spy(self, quote, derivatives):
        seen.append((quote.funding_rate, derivatives))
        return original(self, quote, derivatives)

    monkeypatch.setattr(FundingCarryScreener, "_accumulation_funding_rate", spy)
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: pytest.fail("network attempted"))
    before = digest(record)
    report = await compare(record, manifest)
    assert report["status"] == "complete"
    assert report["control"]["candidate_results"]["counts"] == record.counts
    assert report["control"]["candidate_results"]["decisions"] == record.decisions
    assert report["control"]["unused_calls"] == 0
    assert report["quote_funding_carry"]["candidate_results"]["counts"]["evaluated"] == 1
    assert seen and any(ds is None for _, ds in seen)
    assert digest(record) == before
    assert report["performance_claims_permitted"] is False


def test_funding_seam_preserves_unknown_and_known_adverse_values(settings):
    inst = make_instrument()
    quote = Quote(
        inst.id,
        Decimal("1"),
        Decimal("1"),
        Decimal("1"),
        Decimal("1000000"),
        funding_rate=Decimal("-0.0002"),
    )
    baseline = CryptoOpportunityScreener(settings, object())
    challenger = FundingCarryScreener(settings, object())
    assert baseline._accumulation_funding_rate(quote, None) is None
    assert challenger._accumulation_funding_rate(quote, None) == quote.funding_rate
    call, _, _ = derivative_case()
    adverse = replace(call.result, funding_rate=Decimal("0.001"))
    assert baseline._accumulation_funding_rate(quote, adverse) == adverse.funding_rate
    assert challenger._accumulation_funding_rate(quote, adverse) == adverse.funding_rate


@pytest.mark.parametrize("change", ["missing", "rate", "future", "interval", "origin"])
async def test_unproven_funding_blocks_only_challenger(settings, change):
    record, manifest = await comparison_fixture(settings)
    proof = next(p for p in manifest["calls"] if "quote_funding" in p)
    row = proof["quote_funding"]["SOLUSDT"]
    if change == "missing":
        del proof["quote_funding"]
    elif change == "rate":
        row["rate"] = "-0.001"
    elif change == "future":
        row["available_at"] = (record.completed_at + timedelta(seconds=1)).isoformat()
    elif change == "interval":
        row["interval_seconds"] = None
    else:
        row["origin"] = "reconstructed_neutral"
    report = await compare(record, manifest)
    assert report["status"] == "incomplete"
    assert report["control"]["status"] == "complete"
    assert report["quote_funding_carry"]["candidate_results"] is None
    assert report["negative_forecasts"] == 0


async def test_swallowed_missing_request_invalidates_whole_arm(settings, monkeypatch):
    record, manifest = await comparison_fixture(settings)
    original = FundingCarryScreener.screen

    async def changed(self, market, *args, **kwargs):
        # Production catches provider failures in several evaluation paths.
        with suppress(ReplayMismatch):
            await market.candles(make_instrument(), "1h", 999)
        return await original(self, market, *args, **kwargs)

    monkeypatch.setattr(FundingCarryScreener, "screen", changed)
    report = await compare(record, manifest)
    arm = report["quote_funding_carry"]
    assert arm["status"] == "unavailable" and arm["candidate_results"] is None
    assert arm["missing_input_requested"]
    assert arm["reason"] == "challenger_input_not_recorded"


async def test_source_mismatch_never_runs_challenger(settings, monkeypatch):
    record, manifest = await comparison_fixture(settings)
    record = replace(record, identity={**record.identity, "source_hash": "old-source"})
    manifest["record_sha256"] = digest(record)
    monkeypatch.setattr(
        FundingCarryScreener, "screen", lambda *a, **kw: pytest.fail("challenger ran")
    )
    with pytest.raises(ReplayMismatch, match="identity mismatch"):
        await compare(record, manifest)


def test_comparison_cli_is_offline_reproducible_and_exclusive(settings, tmp_path):
    record, manifest = asyncio.run(comparison_fixture(settings))
    root = tmp_path / "archive"
    LocalScanArchive(root).write_scan(record)
    sidecar = tmp_path / "manifest.json"
    sidecar.write_text(json.dumps(manifest))
    output = tmp_path / "comparison.json"
    command = [
        sys.executable,
        "-m",
        "scripts.research_pipeline_comparison",
        "--archive",
        str(root),
        "--scan-id",
        record.scan_id,
        "--manifest",
        str(sidecar),
        "--output",
        str(output),
    ]
    first = subprocess.run(
        command, capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1], timeout=30
    )
    assert first.returncode == 0, first.stderr
    payload = output.read_bytes()
    assert json.loads(payload)["status"] == "complete"
    second = subprocess.run(
        command, capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1], timeout=30
    )
    assert second.returncode != 0 and output.read_bytes() == payload


async def test_failed_repeat_control_cannot_produce_complete_report(settings, monkeypatch):
    from scripts import research_pipeline_comparison as comparison

    record, manifest = await comparison_fixture(settings)

    # The strict baseline has already been independently exercised above. Inject a second-run
    # missing request to test the repeat-control failure path specifically.
    async def admitted(*args):
        return None

    monkeypatch.setattr(comparison, "verify_record", admitted)
    original = CryptoOpportunityScreener.screen

    async def missing(self, market, *args, **kwargs):
        with suppress(ReplayMismatch):
            await market.candles(make_instrument(), "1h", 999)
        return await original(self, market, *args, **kwargs)

    monkeypatch.setattr(CryptoOpportunityScreener, "screen", missing)
    with pytest.raises(ReplayUnavailable, match="repeat_control_input_not_recorded"):
        await compare(record, manifest)


async def test_challenger_must_preserve_pre_funding_selection(settings, monkeypatch):
    record, manifest = await comparison_fixture(settings)
    original = FundingCarryScreener.screen

    async def changed(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        self._journal.note("SOLUSDT", "selection", "selection_limit")
        return result

    monkeypatch.setattr(FundingCarryScreener, "screen", changed)
    report = await compare(record, manifest)
    assert report["quote_funding_carry"]["status"] == "unavailable"
    assert report["quote_funding_carry"]["reason"] == "pre_funding_funnel_changed"
    assert report["status"] == "incomplete"


async def test_arms_cannot_mutate_archived_inputs(settings, monkeypatch):
    record, manifest = await comparison_fixture(settings)
    before = digest(record)
    original = FundingCarryScreener.screen

    async def mutate(self, market, *args, **kwargs):
        # A provider universe is a mutable list; the arm must own its detached copy.
        for group in market.tape.calls.values():
            if group and group[0].method == "instruments":
                with pytest.raises(TypeError):
                    group[0].result.clear()
        return await original(self, market, *args, **kwargs)

    monkeypatch.setattr(FundingCarryScreener, "screen", mutate)
    report = await compare(record, manifest)
    assert digest(record) == before
    # Accessing a tape result cannot mutate its shared immutable snapshot.
    assert report["quote_funding_carry"]["status"] == "complete"
