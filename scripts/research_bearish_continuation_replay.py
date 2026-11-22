"""Offline structural counterfactual on an existing capture; never a strict original replay."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from algotrader.application.scan_recording import screening_identity
from algotrader.domain.bearish_continuation import DEFAULT_POLICY, evaluate_continuation
from algotrader.domain.models import Candle, DerivativesSnapshot, Instrument, Quote
from algotrader.domain.scan_observations import RecordedCall, decode_value
from scripts.research_verified_replay import offline, write_report


def analyze(path: Path, expected_sha256: str | None = None) -> dict[str, Any]:
    with path.open("rb") as source:
        checksum = hashlib.file_digest(source, "sha256").hexdigest()
    if expected_sha256 is not None and checksum != expected_sha256:
        raise ValueError("capture checksum mismatch")
    with path.open(encoding="utf-8") as source:
        raw = json.load(source)
    if raw.get("@") != "ScanRecord":
        raise ValueError("expected a canonical ScanRecord payload")
    fields = raw["fields"]
    if fields.get("status") != "complete":
        raise ValueError("incomplete capture")
    anchor = decode_value(fields["started_at"])
    quotes: dict[str, Quote] = {}
    instruments: dict[str, Instrument] = {}
    candles: dict[tuple[str, str], tuple[Candle, ...]] = {}
    derivatives: dict[str, DerivativesSnapshot] = {}
    for encoded in fields["inputs"]["items"]:
        call = decode_value(encoded)
        if not isinstance(call, RecordedCall) or call.error:
            continue
        if call.method == "quotes":
            for quote in call.result:
                quotes[quote.instrument_id.symbol] = quote
        elif call.method == "candles":
            inst = call.arguments["instrument"]
            instruments[inst.id.symbol] = inst
            key = (inst.id.symbol, call.arguments["interval"])
            bars = tuple(call.result)
            if len(bars) > len(candles.get(key, ())):
                candles[key] = bars
        elif call.method == "derivatives_snapshot":
            derivatives[call.arguments["instrument"].id.symbol] = call.result
    counts: Counter[str] = Counter()
    rows = []
    with offline():
        for symbol, inst in sorted(instruments.items()):
            if (symbol, "4h") not in candles:  # exclude benchmark-only hourly requests
                continue
            quote = quotes.get(symbol)
            if (
                quote is None
                or quote.observed_at.tzinfo is None
                or not timedelta(seconds=-5) <= anchor - quote.observed_at <= timedelta(minutes=5)
                or not all(v.is_finite() for v in (quote.last, quote.bid, quote.ask))
                or not 0 < quote.bid <= quote.ask
                or quote.last <= 0
            ):
                counts["quote_unavailable"] += 1
                continue
            evaluation = evaluate_continuation(
                candles.get((symbol, "1h"), ()),
                candles[(symbol, "4h")],
                now=anchor,
                price=quote.last,
                tick=inst.price_tick,
                derivatives=derivatives.get(symbol),
                spread_fraction=(quote.ask - quote.bid) / quote.last,
            )
            counts[evaluation.reason] += 1
            rows.append(
                {
                    "symbol": symbol,
                    "reason": evaluation.reason,
                    "gates": evaluation.gates,
                    "search_rejections": dict(evaluation.search_rejections),
                    "structurally_ready": evaluation.structurally_ready,
                    "confirmed": evaluation.confirmed,
                    "plan": evaluation.plan.as_dict() if evaluation.plan else None,
                }
            )
    return {
        "schema": "atlas-continuation-structural-counterfactual-v2",
        "capture_sha256": checksum,
        "scan_id": fields["scan_id"],
        "anchor": anchor.isoformat(),
        "policy": {
            k: str(v) if isinstance(v, Decimal) else v for k, v in asdict(DEFAULT_POLICY).items()
        },
        "capture_identity": decode_value(fields["identity"]),
        "counterfactual_identity": screening_identity(),
        "comparison": "new_policy_on_available_recorded_inputs_not_full_pipeline_replay",
        "scope": "recorded candle universe; not a reconstruction of unrecorded derivatives",
        "performance_claims_permitted": False,
        "network_enabled": False,
        "evaluated": len(rows),
        "reason_counts": dict(sorted(counts.items())),
        "structurally_ready": [r["symbol"] for r in rows if r["structurally_ready"]],
        "confirmed_on_available_inputs": [r["symbol"] for r in rows if r["confirmed"]],
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    report = analyze(args.capture, args.expected_sha256)
    write_report(args.output, report)
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "evaluated",
                    "reason_counts",
                    "structurally_ready",
                    "confirmed_on_available_inputs",
                )
            },
            default=str,
        )
    )


if __name__ == "__main__":
    main()
