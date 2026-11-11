from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from algotrader.application.scan_recording import capture_scan
from algotrader.domain.models import DerivativesSnapshot
from algotrader.infrastructure.scan_archive import canonical_bytes
from scripts.replay_contract import ReplayUnavailable, check_call, verify_record
from tests.test_scan_replay import Macro


def proof(call, at):
    return {
        "call_sha256": hashlib.sha256(canonical_bytes(call)).hexdigest(),
        "origin": "recorded_public",
        "observed_at": at.isoformat(),
        "available_at": at.isoformat(),
    }


class EmptyMarket:
    async def instruments(self):
        return []

    async def quotes(self, instruments):
        return []


async def fixture_record(settings, market=None):
    from algotrader.adapters.external.models import (
        FearGreedReading,
        GlobalMarketMetrics,
        MacroSentimentContext,
        StablecoinSupplyMetric,
    )

    class CompleteMacro:
        async def get_context(self):
            at = datetime.now(UTC)
            return MacroSentimentContext(
                FearGreedReading(50, "Neutral", at),
                StablecoinSupplyMetric(
                    Decimal("100"), Decimal("0"), Decimal("0"), Decimal("0"), at
                ),
                GlobalMarketMetrics(
                    Decimal("60"), Decimal("0"), Decimal("100"), Decimal("100"), at
                ),
                at,
            )

    from tests.test_crypto_screener import FakeDerivativesData

    captured = await capture_scan(
        market or EmptyMarket(),
        FakeDerivativesData({}),
        CompleteMacro(),
        settings,
        {"enforce_quality": True},
    )
    record = captured.record
    manifest = {
        "schema": "atlas-verified-replay-v1",
        "scan_id": record.scan_id,
        "record_sha256": hashlib.sha256(canonical_bytes(record)).hexdigest(),
        "universe_scope": "full_provider_response",
        "selection_applied": False,
        "calls": [proof(call, record.completed_at) for call in record.inputs],
    }
    return record, manifest


async def test_strict_replay_invokes_existing_offline_production_replay(settings, monkeypatch):
    record, manifest = await fixture_record(settings)
    monkeypatch.setattr(
        "httpx.AsyncClient", lambda *a, **kw: pytest.fail("network client constructed")
    )
    report = await verify_record(record, manifest)
    assert report.matches


def test_strict_archive_cli_replays_verified_record_without_overwriting_evidence(
    settings, tmp_path
):
    import json
    import subprocess
    import sys
    from pathlib import Path

    from algotrader.infrastructure.scan_archive import LocalScanArchive

    record, manifest = asyncio.run(fixture_record(settings))
    root = tmp_path / "archive"
    LocalScanArchive(root).write_scan(record)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    output = tmp_path / "result.json"
    command = [
        sys.executable,
        "-m",
        "scripts.research_verified_replay",
        "--archive",
        str(root),
        "--scan-id",
        record.scan_id,
        "--manifest",
        str(manifest_path),
        "--output",
        str(output),
    ]
    first = subprocess.run(
        command, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20
    )
    assert first.returncode == 0, first.stderr
    result = json.loads(output.read_text())
    assert result["status"] == "verified" and result["matches"]
    before = output.read_bytes()
    second = subprocess.run(
        command, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20
    )
    assert second.returncode != 0 and output.read_bytes() == before


async def test_strict_full_pipeline_with_an_evaluated_symbol(settings):
    from algotrader.domain.models import MarketKind, Quote, Venue
    from tests.conftest import make_instrument, trending_candles
    from tests.test_crypto_screener import FakeCryptoMarketData

    at = datetime.now(UTC)
    instrument = make_instrument(
        symbol="SOLUSDT", venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES
    )

    def bars(count, hours):
        source = trending_candles(count=count)
        if hours == 168:
            end = at.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
                days=at.weekday()
            )
        else:
            end = at.replace(hour=(at.hour // hours) * hours, minute=0, second=0, microsecond=0)
        return [
            replace(
                c,
                opened_at=end - timedelta(hours=hours * (count - i)),
                closed_at=end - timedelta(hours=hours * (count - i - 1), milliseconds=1),
            )
            for i, c in enumerate(source)
        ]

    class Market(FakeCryptoMarketData):
        async def quotes(self, instruments):
            return [
                Quote(
                    instrument.id,
                    Decimal("99.95"),
                    Decimal("100.05"),
                    Decimal("100"),
                    Decimal("15000000"),
                    funding_rate=Decimal("0"),
                    basis_pct=Decimal("0"),
                )
            ]

    market = Market([instrument], {"SOLUSDT": bars(240, 1)}, {"SOLUSDT": bars(480, 4)})
    market._candles_1w = {"SOLUSDT": bars(40, 168)}
    record, manifest = await fixture_record(settings, market)
    result = await verify_record(record, manifest)
    assert result.matches and result.counts["evaluated"] == 1


@pytest.mark.parametrize(
    "change", ["subset", "hash", "missing_proof", "duplicate", "synthetic", "future"]
)
async def test_strict_replay_rejects_invalid_provenance_before_execution(settings, change):
    record, manifest = await fixture_record(settings)
    if change == "subset":
        manifest["universe_scope"] = "selected_tokens"
    elif change == "hash":
        manifest["record_sha256"] = "0" * 64
    elif change == "missing_proof":
        manifest["calls"].pop()
    elif change == "duplicate":
        manifest["calls"].append(manifest["calls"][0])
    elif change == "synthetic":
        manifest["calls"][0]["origin"] = "neutral_macro"
    else:
        manifest["calls"][0]["available_at"] = (
            record.completed_at + timedelta(seconds=1)
        ).isoformat()
    with pytest.raises(ReplayUnavailable):
        await verify_record(record, manifest)


def derivative_case():
    from algotrader.domain.scan_observations import RecordedCall

    at = datetime(2026, 11, 3, 12, 1, tzinfo=UTC)
    ds = DerivativesSnapshot(
        Decimal("0"),
        Decimal("0"),
        Decimal("100"),
        Decimal("0"),
        Decimal("1"),
        Decimal("1"),
        Decimal("1"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("1"),
        Decimal("1"),
        at,
        False,
    )
    call = RecordedCall("derivatives_snapshot", 0, {}, ds)
    meta = proof(call, at)
    meta["funding"] = {
        "settled_at": (at - timedelta(minutes=1)).isoformat(),
        "interval_seconds": 14400,
        "rate": "0",
    }
    latest = at - timedelta(minutes=1)
    meta["oi_windows"] = {
        str(h): {
            "latest_at": latest.isoformat(),
            "baseline_at": (latest - timedelta(hours=h)).isoformat(),
            "latest_quantity": "100",
            "baseline_quantity": "100",
        }
        for h in (1, 4, 24, 168)
    }
    return call, meta, at


def test_observed_zero_basis_and_funding_are_valid_not_missing():
    call, meta, at = derivative_case()
    check_call(call, meta, at)


@pytest.mark.parametrize(
    "change",
    [
        "stale_funding",
        "stale_snapshot",
        "short_oi",
        "missing_basis",
        "missing_horizon",
        "degraded",
        "wrong_oi",
        "wrong_funding",
    ],
)
def test_derivative_contract_rejects_stale_missing_and_shortened_inputs(change):
    call, meta, at = derivative_case()
    if change == "stale_funding":
        meta["funding"]["settled_at"] = (at - timedelta(hours=708)).isoformat()
    elif change == "stale_snapshot":
        call = replace(call, result=replace(call.result, observed_at=at - timedelta(hours=12)))
    elif change == "short_oi":
        meta["oi_windows"]["168"]["baseline_at"] = (at - timedelta(hours=155)).isoformat()
    elif change == "missing_basis":
        call = replace(call, result=replace(call.result, basis_rate=None))
    elif change == "missing_horizon":
        call = replace(call, result=replace(call.result, open_interest_change_24h=None))
    elif change == "wrong_oi":
        meta["oi_windows"]["168"]["baseline_quantity"] = "90"
    elif change == "wrong_funding":
        meta["funding"]["rate"] = "0.01"
    else:
        call = replace(call, result=replace(call.result, degraded_telemetry=True))
    meta["call_sha256"] = hashlib.sha256(canonical_bytes(call)).hexdigest()
    with pytest.raises(ReplayUnavailable):
        check_call(call, meta, at)


@pytest.mark.parametrize("change", ["short", "stale", "future", "gap", "duplicate"])
def test_candle_contract_rejects_bad_windows(change):
    from algotrader.domain.scan_observations import RecordedCall
    from tests.conftest import trending_candles

    at = datetime(2026, 11, 3, 12, 1, tzinfo=UTC)
    bars = trending_candles(count=480)
    shift = at.replace(minute=0) - bars[-1].closed_at
    bars = [replace(c, opened_at=c.opened_at + shift, closed_at=c.closed_at + shift) for c in bars]
    if change == "short":
        bars = bars[-10:]
    elif change == "stale":
        bars = [
            replace(
                c,
                opened_at=c.opened_at - timedelta(hours=12),
                closed_at=c.closed_at - timedelta(hours=12),
            )
            for c in bars
        ]
    elif change == "future":
        bars[-1] = replace(bars[-1], closed_at=at + timedelta(hours=1))
    elif change == "gap":
        bars.pop(-10)
    else:
        bars[-2] = bars[-1]
    call = RecordedCall("candles", 0, {"interval": "1h", "limit": 240}, bars[-240:])
    with pytest.raises(ReplayUnavailable):
        check_call(call, proof(call, at), at)


async def test_neutral_macro_is_unavailable_not_a_negative_prediction(settings):
    captured = await capture_scan(EmptyMarket(), None, Macro(), settings, {"enforce_quality": True})
    call = next(c for c in captured.record.inputs if c.method == "macro")
    with pytest.raises(ReplayUnavailable):
        check_call(call, proof(call, captured.record.completed_at), captured.record.completed_at)


@pytest.mark.parametrize("mode", ["strict", "diagnostic"])
async def test_reconstruction_unavailable_never_enters_prediction_denominator(
    tmp_path, mode, monkeypatch
):
    import argparse
    import json

    from scripts.research_verified_replay import run

    path = tmp_path / "inputs.json"
    path.write_text(
        json.dumps(
            {
                "metadata": {
                    "schema": "faithful-point-in-time-replay-v1",
                    "anchor_end_utc": "2026-11-03T11:59:59.999000+00:00",
                },
                "symbols": {},
            }
        )
    )
    monkeypatch.setattr(
        "httpx.AsyncClient", lambda *a, **kw: pytest.fail("network client constructed")
    )
    report, status = await run(argparse.Namespace(inputs=path, mode=mode))
    assert status == 3 and report["status"] == "unavailable"
    assert report["candidate_results"] is None
    assert report["strict_eligible_observations"] == report["negative_forecasts"] == 0
    assert len(report["observations"]) == 72


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'])
def test_invalid_json_is_rejected(tmp_path, text):
    from scripts.research_verified_replay import read_json

    path = tmp_path / "input.json"
    path.write_text(text)
    with pytest.raises(ReplayUnavailable):
        read_json(path)


def test_report_publication_is_exclusive_and_never_leaves_partial_json(tmp_path):
    from scripts.research_verified_replay import write_report

    path = tmp_path / "report.json"
    with pytest.raises(ValueError):
        write_report(path, {"value": float("nan")})
    assert not path.exists()
    write_report(path, {"status": "unavailable"})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_report(path, {"status": "verified"})
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".atlas-replay-*"))
