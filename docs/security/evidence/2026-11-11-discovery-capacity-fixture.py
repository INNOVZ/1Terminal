"""Synthetic full-budget adapter/capture/replay capacity. Networking stays disabled."""
import asyncio
import gc
import json
import resource
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx

from algotrader.adapters.binance import base, futures
from algotrader.adapters.external.models import (
    FearGreedReading, GlobalMarketMetrics, MacroSentimentContext, StablecoinSupplyMetric,
)
from algotrader.application import scan_recording
from algotrader.domain import models
from scripts import research_capture_evidence as capture
from scripts import research_immutable_capture
from scripts.research_evidence_stream import read_bundle, write_bundle
from scripts.research_pipeline_comparison import compare_bundle
from scripts.research_verified_replay import offline

AT = datetime.now(UTC).replace(minute=5, second=0, microsecond=0)
VARIANT = sys.argv[1] if len(sys.argv) > 1 else "full"
SYMBOLS = ("BTCUSDT", *(f"SYNTH{i}USDT" for i in range(320)))


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return AT.astimezone(tz) if tz else AT.replace(tzinfo=None)


class Macro:
    async def close(self):
        pass

    async def get_context(self):
        return MacroSentimentContext(
            FearGreedReading(50, "Neutral", AT),
            StablecoinSupplyMetric(Decimal("100"), Decimal("0"), Decimal("0"), Decimal("0"), AT),
            GlobalMarketMetrics(Decimal("60"), Decimal("0"), Decimal("100"), Decimal("100"), AT), AT)


def response(request):
    path, params = request.url.path, request.url.params
    now = int(AT.timestamp() * 1000)
    if path.endswith("exchangeInfo"):
        value = {"symbols": [{"symbol": s, "baseAsset": s[:-4], "quoteAsset": "USDT",
            "status": "TRADING", "contractType": "PERPETUAL", "filters": []} for s in SYMBOLS]}
    elif path.endswith("ticker/24hr"):
        value = [{"symbol": s, "quoteVolume": "900000" if VARIANT == "watch" and i >= 271 else "15000000",
                  "highPrice": "101", "lowPrice": "99", "priceChangePercent": "0", "closeTime": now}
                 for i, s in enumerate(SYMBOLS)]
    elif path.endswith("ticker/bookTicker"):
        value = [{"symbol": s, "bidPrice": "99.95", "askPrice": "100.05", "time": now} for s in SYMBOLS]
    elif path.endswith("premiumIndex"):
        def premium(symbol):
            return {"symbol": symbol, "lastFundingRate": "-0.0002", "markPrice": "100",
                    "indexPrice": "100", "time": now, "nextFundingTime": (now // 14400000 + 1) * 14400000}
        value = premium(params["symbol"]) if "symbol" in params else [premium(s) for s in SYMBOLS]
    elif path.endswith("klines"):
        interval = params["interval"]
        width = {"1h": 3600000, "4h": 14400000, "1w": 604800000}[interval]
        offset = 345600000 if interval == "1w" else 0
        last = (now - offset) // width * width + offset
        count = int(params["limit"])
        starts = (last - (count - 1 - i) * width for i in range(count))
        value = [[t, "100", "101", "99", "100", "1000", t + width - 1,
                  "100000", 1, "500", "50000", "0"] for t in starts]
    elif path.endswith("openInterest"):
        value = {"symbol": params["symbol"], "openInterest": "333", "time": now}
    elif path.endswith("openInterestHist"):
        latest = now // 3600000 * 3600000
        value = [{"timestamp": latest - (199 - i) * 3600000, "sumOpenInterest": str(100 + i)} for i in range(200)]
    elif path.endswith("takerlongshortRatio"):
        latest = now // 3600000 * 3600000 - 3600000
        value = [{"timestamp": latest - i * 3600000, "buyVol": "100", "sellVol": "100"} for i in range(26)]
    elif path.endswith("depth"):
        value = {"bids": [["100", "1000"]], "asks": [["100", "1000"]]}
    elif path.endswith("fundingInfo"):
        value = [{"symbol": s, "fundingIntervalHours": 4} for s in SYMBOLS]
    elif path.endswith("fundingRate"):
        value = [{"symbol": params["symbol"], "fundingTime": now // 14400000 * 14400000, "fundingRate": "0"}]
    else:
        raise ValueError(f"unexpected fixture request {path}")
    return httpx.Response(200, json=value)


async def main():
    start = time.monotonic()
    # Fixed synthetic clocks avoid crossing a real hour boundary during capacity tests.
    for module in (capture, research_immutable_capture, scan_recording, base, futures, models):
        module.datetime = Clock
    args = SimpleNamespace(max_attempts=3000, deadline_seconds=600, max_symbols=320,
                           max_supplemental=60, discovery_v2=True)
    bundle = await capture.execute(args, transport=httpx.MockTransport(response), macro_provider=Macro())
    assert bundle["status"] == "captured", bundle.get("error")
    bundle["synthetic_capacity_only"] = True
    captured = time.monotonic()
    root = Path("/output")
    path = root / "capture.jsonl"
    write_bundle(path, bundle)
    counts = dict(bundle["record"].counts)
    supplemental = len(bundle["supplemental"])
    del bundle
    gc.collect()
    print(json.dumps({"phase": "captured", "variant": VARIANT, "control_counts": counts,
                      "seconds": captured-start, "bytes": path.stat().st_size}), flush=True)
    restored = read_bundle(path)
    with offline():
        comparison = await compare_bundle(restored)
    assert comparison["status"] == "complete", comparison.get("discovery_v2")
    discovery = comparison["discovery_v2"]["candidate_results"]
    expected_control, expected_discovery = (270, 290) if VARIANT == "watch" else (320, 320)
    assert counts["evaluated"] == expected_control
    assert discovery["counts"]["evaluated"] == expected_discovery
    assert len(discovery["watch_symbols"]) == (20 if VARIANT == "watch" else 0)
    result = {"synthetic": True, "network": "none", "variant": VARIANT,
              "control_counts": counts, "discovery_counts": discovery["counts"],
              "watch_symbols": len(discovery["watch_symbols"]), "supplemental": supplemental,
              "capture_seconds": captured-start, "total_seconds": time.monotonic()-start,
              "bytes": path.stat().st_size, "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              "status": comparison["status"], "enrolled": False}
    (root / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


asyncio.run(main())
