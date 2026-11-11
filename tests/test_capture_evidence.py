from __future__ import annotations

import copy
import gzip
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from algotrader.adapters.binance.futures import BinanceUsdmAdapter
from algotrader.adapters.external.models import (
    FearGreedReading,
    GlobalMarketMetrics,
    MacroSentimentContext,
    StablecoinSupplyMetric,
)
from algotrader.domain.models import MarketKind, Venue
from algotrader.domain.scan_observations import RecordedCall
from scripts.replay_contract import ReplayUnavailable, digest
from scripts.research_capture_evidence import (
    SCHEMA,
    EvidenceClient,
    EvidencePort,
    EvidenceStopped,
    EvidenceTransport,
    capture_evidence,
)
from scripts.research_evidence_contract import funding_manifest, verify_bundle, verify_call
from scripts.research_pipeline_comparison import compare_bundle
from scripts.research_verified_replay import diagnostic_settings, offline
from tests.conftest import make_instrument


class Macro:
    async def close(self):
        pass

    async def get_context(self):
        at = datetime.now(UTC)
        return MacroSentimentContext(
            FearGreedReading(50, "Neutral", at),
            StablecoinSupplyMetric(Decimal("100"), Decimal("0"), Decimal("0"), Decimal("0"), at),
            GlobalMarketMetrics(Decimal("60"), Decimal("0"), Decimal("100"), Decimal("100"), at),
            at,
        )


def public_response(request):
    path, params = request.url.path, request.url.params
    now = int(datetime.now(UTC).timestamp() * 1000)
    symbols = ("BTCUSDT", "SOLUSDT")
    if path.endswith("exchangeInfo"):
        value = {
            "symbols": [
                {
                    "symbol": s,
                    "baseAsset": s[:-4],
                    "quoteAsset": "USDT",
                    "status": "TRADING",
                    "contractType": "PERPETUAL",
                    "filters": [],
                }
                for s in symbols
            ]
        }
    elif path.endswith("ticker/24hr"):
        value = [
            {
                "symbol": s,
                "quoteVolume": "30000000" if s == "SOLUSDT" else "15000000",
                "highPrice": "101",
                "lowPrice": "99",
                "priceChangePercent": "0",
                "closeTime": now,
            }
            for s in symbols
        ]
    elif path.endswith("ticker/bookTicker"):
        value = [
            {"symbol": s, "bidPrice": "99.95", "askPrice": "100.05", "time": now} for s in symbols
        ]
    elif path.endswith("premiumIndex"):

        def premium(s):
            return {
                "symbol": s,
                "lastFundingRate": "0",
                "markPrice": "100",
                "indexPrice": "100",
                "time": now,
                "nextFundingTime": (now // 14400000 + 1) * 14400000,
            }

        value = premium(params["symbol"]) if "symbol" in params else [premium(s) for s in symbols]
    elif path.endswith("klines"):
        interval = params["interval"]
        ms = {"1h": 3600000, "4h": 14400000, "1w": 604800000}[interval]
        # Binance weeks start on Monday.
        offset = 345600000 if interval == "1w" else 0
        last = ((now - offset) // ms) * ms + offset
        count = int(params["limit"])
        value = [
            [
                last - (count - 1 - i) * ms,
                "100",
                "101",
                "99",
                "100",
                "1000",
                last - (count - 1 - i) * ms + ms - 1,
                "100000",
                1,
                "500",
                "50000",
                "0",
            ]
            for i in range(count)
        ]
    elif path.endswith("openInterest"):
        value = {"symbol": params["symbol"], "openInterest": "333", "time": now}
    elif path.endswith("openInterestHist"):
        latest = now // 3600000 * 3600000
        value = [
            {"timestamp": latest - (199 - i) * 3600000, "sumOpenInterest": str(100 + i)}
            for i in range(200)
        ]
    elif path.endswith("takerlongshortRatio"):
        latest = now // 3600000 * 3600000 - 3600000
        value = [
            {"timestamp": latest - i * 3600000, "buyVol": "100", "sellVol": "100"}
            for i in range(26)
        ]
    elif path.endswith("depth"):
        value = {"bids": [["100", "1000"]], "asks": [["100", "1000"]]}
    elif path.endswith("fundingInfo"):
        value = [{"symbol": "SOLUSDT", "fundingIntervalHours": 4}]
    elif path.endswith("fundingRate"):
        value = [
            {
                "symbol": params["symbol"],
                "fundingTime": now // 14400000 * 14400000,
                "fundingRate": "0",
            }
        ]
    else:
        pytest.fail(f"unexpected public path {path}")
    return httpx.Response(200, json=value)


async def bundle_fixture():
    transport = EvidenceTransport(httpx.MockTransport(public_response), attempts=100, seconds=60)
    client = EvidenceClient(
        "https://fapi.binance.com",
        transport=transport,
        max_rate_limit_retries=0,
        max_transport_retries=0,
    )
    port = EvidencePort(BinanceUsdmAdapter(client))
    try:
        record, supplemental = await capture_evidence(
            port, Macro(), diagnostic_settings(), max_symbols=1
        )
        await client.request("GET", "/fapi/v1/fundingInfo")
        await client.request(
            "GET", "/fapi/v1/fundingRate", params={"symbol": "SOLUSDT", "limit": 1}
        )
    finally:
        await client.close()
    return {
        "schema": SCHEMA,
        "status": "captured",
        "record": record,
        "supplemental": supplemental,
        "frames": port.frames,
        "receipts": transport.receipts,
        "completed_at": datetime.now(UTC),
        "enrolled": False,
        "outcome_anchor": None,
    }


async def test_real_adapters_capture_and_compare_partial_candles_offline(monkeypatch):
    bundle = await bundle_fixture()
    assert any(
        not c.closed
        for call in bundle["record"].inputs
        if call.method == "candles"
        for c in call.result
    )
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: pytest.fail("network client created"))
    before = digest(bundle)
    with offline():
        result = await compare_bundle(bundle)
    assert result["status"] == "complete"
    assert result["control"]["candidate_results"]["counts"]["evaluated"] == 1
    assert result["enrolled"] is False and result["outcome_anchor"] is None
    assert digest(bundle) == before
    rows = funding_manifest(bundle)["calls"][0]["quote_funding"]
    assert rows["SOLUSDT"]["interval_seconds"] == 14400


async def test_live_oi_is_not_mistaken_for_hourly_oi():
    transport = EvidenceTransport(httpx.MockTransport(public_response), attempts=20, seconds=60)
    client = EvidenceClient(
        "https://fapi.binance.com",
        transport=transport,
        max_rate_limit_retries=0,
        max_transport_retries=0,
    )
    port = EvidencePort(BinanceUsdmAdapter(client))
    inst = make_instrument(
        symbol="SOLUSDT", venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES
    )
    try:
        value = await port.derivatives_snapshot(inst)
    finally:
        await client.close()
    assert value.open_interest == Decimal("333")
    assert value.open_interest_change_1h == Decimal(1) / Decimal(298)
    call = RecordedCall("derivatives_snapshot", 0, {"instrument": inst}, value)
    await verify_call(call, port.frames[0], transport.receipts, datetime.now(UTC))
    broken = replace_call(call, open_interest=Decimal("299"))
    frame = {**port.frames[0], "call_sha256": digest(broken)}
    with pytest.raises(ReplayUnavailable, match="adapter_result_mismatch"):
        await verify_call(broken, frame, transport.receipts, datetime.now(UTC))


def replace_call(call, **changes):
    from dataclasses import replace

    return replace(call, result=replace(call.result, **changes))


@pytest.mark.parametrize(
    "change", ["payload", "future", "scope", "source", "missing", "supplement"]
)
async def test_evidence_tampering_or_missing_data_blocks_comparison(change):
    bundle = copy.deepcopy(await bundle_fixture())
    if change == "payload":
        row = next(r for r in bundle["receipts"] if r["path"].endswith("premiumIndex"))
        row["payload"][0]["lastFundingRate"] = "0.1"
    elif change == "future":
        bundle["receipts"][0]["available_at"] = bundle["completed_at"] + timedelta(seconds=1)
    elif change == "scope":
        bundle["frames"][0]["scope"] = "changed"
    elif change == "source":
        bundle["record"].identity["source_hash"] = "old-source"
    elif change == "missing":
        bundle["receipts"].pop(0)
    else:
        bundle["supplemental"] = (RecordedCall("macro", 0, {}, None),)
    with pytest.raises(ValueError):
        await verify_bundle(bundle)


async def test_funding_interval_fallback_uses_recorded_schedule_not_default():
    bundle = await bundle_fixture()
    row = next(r for r in bundle["receipts"] if r["path"].endswith("fundingInfo"))
    row["payload"] = []
    row["payload_sha256"] = digest([])
    result = funding_manifest(bundle)["calls"][0]["quote_funding"]["SOLUSDT"]
    assert result["interval_seconds"] == 14400
    history = next(r for r in bundle["receipts"] if r["path"].endswith("fundingRate"))
    history["payload"] = []
    history["payload_sha256"] = digest([])
    with pytest.raises(ReplayUnavailable, match="schedule_unknown"):
        funding_manifest(bundle)


@pytest.mark.parametrize(
    "url,headers",
    [
        ("https://evil.test/fapi/v1/premiumIndex", {}),
        ("https://fapi.binance.com/fapi/v1/account", {}),
        ("https://fapi.binance.com/fapi/v1/premiumIndex?signature=secret", {}),
        ("https://fapi.binance.com/fapi/v1/premiumIndex", {"X-MBX-APIKEY": "secret"}),
    ],
)
async def test_transport_rejects_private_or_out_of_scope_requests(url, headers):
    transport = EvidenceTransport(httpx.MockTransport(lambda r: pytest.fail("wire touched")))
    with pytest.raises(EvidenceStopped, match="scope_violation"):
        await transport.handle_async_request(httpx.Request("GET", url, headers=headers))
    assert transport.attempts == 0
    await transport.aclose()


async def test_throttling_and_attempt_caps_stop_subsequent_wire_requests():
    calls = []

    def throttled(request):
        calls.append(request)
        return httpx.Response(429, json={})

    transport = EvidenceTransport(httpx.MockTransport(throttled), attempts=2)
    request = httpx.Request("GET", "https://fapi.binance.com/fapi/v1/premiumIndex")
    for _ in range(2):
        with pytest.raises(EvidenceStopped):
            await transport.handle_async_request(request)
    assert len(calls) == 1
    await transport.aclose()
    transport = EvidenceTransport(
        httpx.MockTransport(lambda r: httpx.Response(200, json=[])), attempts=1
    )
    await transport.handle_async_request(request)
    with pytest.raises(EvidenceStopped):
        await transport.handle_async_request(request)
    assert transport.attempts == 1
    await transport.aclose()


async def test_byte_cap_and_gzip_responses():
    request = httpx.Request("GET", "https://fapi.binance.com/fapi/v1/premiumIndex")
    transport = EvidenceTransport(
        httpx.MockTransport(lambda r: httpx.Response(200, content=b'"too large"')), byte_limit=2
    )
    with pytest.raises(EvidenceStopped, match="byte_limit"):
        await transport.handle_async_request(request)
    await transport.aclose()
    body = gzip.compress(b'[{"symbol":"SOLUSDT"}]')
    transport = EvidenceTransport(
        httpx.MockTransport(
            lambda r: httpx.Response(200, content=body, headers={"Content-Encoding": "gzip"})
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get(str(request.url))
        assert response.json() == [{"symbol": "SOLUSDT"}]


async def test_bounded_supplemental_fetch_is_retained_and_consumed(monkeypatch):
    from contextlib import suppress

    from algotrader.application.scan_replay import ReplayMismatch
    from scripts.research_pipeline_comparison import FundingCarryScreener

    original = FundingCarryScreener.screen

    async def require_second_snapshot(self, market, *args, **kwargs):
        result = await original(self, market, *args, **kwargs)
        inst = next(i for i in self._journal.cohort if i.id.symbol == "SOLUSDT")
        with suppress(ReplayMismatch):
            await market.derivatives_snapshot(inst)
        return result

    monkeypatch.setattr(FundingCarryScreener, "screen", require_second_snapshot)
    bundle = await bundle_fixture()
    assert len(bundle["supplemental"]) == 1
    assert bundle["supplemental"][0].index == 1
    assert (await compare_bundle(bundle))["status"] == "complete"
    bundle["supplemental"] = ()
    bundle["frames"] = [f for f in bundle["frames"] if f["index"] == 0]
    # Missing input is visible even when the evaluator swallows its provider exception.
    report = await compare_bundle(bundle)
    assert report["quote_funding_carry"]["status"] == "unavailable"


async def test_execute_closes_resources_and_preserves_original_on_metadata_failure():
    from types import SimpleNamespace

    from scripts.research_capture_evidence import execute

    class ClosingMacro(Macro):
        closed = False

        async def close(self):
            self.closed = True

    macro = ClosingMacro()

    def handler(request):
        if request.url.path.endswith("fundingInfo"):
            return httpx.Response(429, json={})
        return public_response(request)

    bundle = await execute(
        SimpleNamespace(max_attempts=100, deadline_seconds=60, max_symbols=1, max_supplemental=0),
        transport=httpx.MockTransport(handler),
        macro_provider=macro,
    )
    assert macro.closed
    assert bundle["status"] == "incomplete" and bundle["record"] is not None
    assert bundle["record"].status == "complete"
    assert bundle["stopped"] == "exchange_cooldown"


def test_bundle_cli_offline_and_exclusive_output(tmp_path):
    import asyncio
    import json
    import subprocess
    import sys
    from pathlib import Path

    from algotrader.domain.scan_observations import encode_value
    from scripts.research_verified_replay import write_report

    bundle = asyncio.run(bundle_fixture())
    source, output = tmp_path / "bundle.json", tmp_path / "report.json"
    write_report(source, encode_value(bundle))
    command = [
        sys.executable,
        "-m",
        "scripts.research_pipeline_comparison",
        "--bundle",
        str(source),
        "--output",
        str(output),
    ]
    result = subprocess.run(
        command, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text())["status"] == "complete"
    before = output.read_bytes()
    assert subprocess.run(command, capture_output=True, timeout=30).returncode != 0
    assert output.read_bytes() == before


async def test_weight_ceiling_and_clock_restoration_on_error():
    from algotrader.adapters.binance import base, futures
    from scripts.research_evidence_contract import adapter_clock

    transport = EvidenceTransport(
        httpx.MockTransport(
            lambda r: httpx.Response(200, json=[], headers={"x-mbx-used-weight-1m": "2150"})
        )
    )
    request = httpx.Request("GET", "https://fapi.binance.com/fapi/v1/premiumIndex")
    await transport.handle_async_request(request)
    with pytest.raises(EvidenceStopped, match="weight_ceiling"):
        await transport.handle_async_request(request)
    await transport.aclose()
    original = base.datetime, futures.datetime
    with pytest.raises(RuntimeError), adapter_clock(datetime.now(UTC)):
        raise RuntimeError("test")
    assert (base.datetime, futures.datetime) == original
