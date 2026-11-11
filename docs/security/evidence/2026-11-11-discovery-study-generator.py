import json
import resource
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from algotrader.domain.models import AssetClass, Instrument, InstrumentId, MarketKind, Venue
from scripts import research_prospective as s

start = time.monotonic()
at = datetime(2026, 11, 8, 12, tzinfo=UTC)
cohort = tuple(
    Instrument(
        id=InstrumentId(Venue.BINANCE_USDM, f"SYNTH{i}USDT"),
        base_asset=f"SYNTH{i}",
        quote_asset="USDT",
        asset_class=AssetClass.CRYPTO,
        market_kind=MarketKind.FUTURES,
        trading=True,
        price_tick=Decimal(".01"),
        quantity_step=Decimal(".001"),
        min_quantity=Decimal(".001"),
        min_notional=Decimal("5"),
        max_leverage=20,
    )
    for i in range(1000)
)
root = Path("/output/study")
root.mkdir()
d = {
    "schema": "atlas-paired-study-v1",
    "study_id": "synthetic-capacity-only",
    "identity": s.identity(),
    "policy": dict(s.POLICY),
    "issued_at": at,
    "cohort": cohort,
    "arms": {
        "control": {"engine": ("SYNTH0USDT",), "radar": ("SYNTH0USDT",), "decisions": ()},
        "funding_carry": {"engine": ("SYNTH1USDT",), "radar": ("SYNTH1USDT",), "decisions": ()},
    },
    "momentum_reference": ("SYNTH2USDT",),
    "synthetic_capacity_only": True,
}
comparison_path = Path("/comparison/comparison.json")
if comparison_path.exists():
    comparison = json.loads(comparison_path.read_text())
    d["arms"] = {
        "control": s.long_arms(comparison["control"]),
        "funding_carry": s.long_arms(comparison["quote_funding_carry"]),
        "discovery_v2": s.long_arms(comparison["discovery_v2"]),
    }
    result = comparison["discovery_v2"]
    observations = tuple(
        {**row, "evidence": s.compact_decision(row["evidence"])}
        for row in result["candidate_results"]["discovery_observations"]
    )
    d["arms"]["discovery_v2"].update(
        source_policy=result["policy"],
        discovery_observations=observations,
        feature_unavailable_symbols=result["candidate_results"]["feature_unavailable_symbols"],
        research_views={
            "watch_selection": result["candidate_results"]["watch_symbols"],
            "readiness": tuple(
                row["symbol"]
                for row in observations
                if row["state"] in ("readiness_activation_unverified", "ready_waiting_activation")
            ),
            "emerging": tuple(
                row["symbol"]
                for row in observations
                if row["temporal_features"].get("emerging") is True
            ),
        },
    )
    d["comparison_sha256"] = s.semantic_digest(comparison)
    del comparison
s.save(root / "definition.json", d)
e = s.enroll(root, now=at)
for slot in range(577):
    stamp = e["anchor"] + timedelta(seconds=300 * slot)
    rows = tuple(
        {
            "symbol": f"SYNTH{i}USDT",
            "lastPrice": str(100 + i / 100),
            "priceChangePercent": str(i / 100),
            "quoteVolume": "10000000",
            "closeTime": int(stamp.timestamp() * 1000),
        }
        for i in range(1000)
    )
    normalized = s.normalize_ranks(cohort, rows, stamp)
    sample = {
        "study_id": d["study_id"],
        "definition_sha256": e["definition_sha256"],
        "slot": slot,
        "scheduled_at": stamp,
        "requested_at": stamp,
        "arrived_at": stamp,
        "status": "observed",
        "rows": rows,
        "normalized": normalized,
        "http_attempts": 0,
        "receipts": (
            {
                "status": 200,
                "path": "/fapi/v1/ticker/24hr",
                "payload": rows,
                "started_at": stamp,
                "available_at": stamp,
            },
        ),
    }
    s.save(root / "snapshots" / f"{slot:06d}.json", sample)
    if slot % 144 == 0:
        print(
            json.dumps({"phase": "write", "slots": slot + 1, "seconds": time.monotonic() - start}),
            flush=True,
        )

s.write_progress(root, e, latest=sample, now=e["endpoint_48h"])
result = {
    "synthetic": True,
    "phase": "generated",
    "cohort": 1000,
    "slots": 577,
    "seconds": time.monotonic() - start,
    "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    "definition_bytes": (root / "definition.json").stat().st_size,
}
Path("/output/generation-result.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result), flush=True)
