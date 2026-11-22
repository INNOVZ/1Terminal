"""Private offline canary over immutable baseline evidence; no network or publication."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from algotrader.domain.bearish_continuation import POLICY_VERSION, evaluate_continuation
from algotrader.domain.models import Instrument, Quote
from algotrader.domain.scan_observations import ScanRecord


def evaluate_canary(
    record: ScanRecord,
    *,
    max_symbols: int = 30,
    seconds: float = 10,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """No data provider is accepted, so missing evidence cannot trigger acquisition."""
    start = clock()
    if record.status != "complete":
        raise ValueError("canary requires a complete captured baseline")
    instruments: dict[str, Instrument] = {}
    quotes: dict[str, Quote] = {}
    bars: dict[tuple[str, str], Any] = {}
    derivatives = {}
    for call in record.inputs:
        if clock() - start >= seconds:
            return {
                "scan_id": record.scan_id,
                "status": "budget_exhausted",
                "rows": [],
                "evaluated": 0,
                "eligible": None,
                "performance_claims_permitted": False,
                "policy": POLICY_VERSION,
                "network_requests": 0,
                "publication_enabled": False,
            }
        if call.error:
            continue
        if call.method == "quotes":
            quotes.update({q.instrument_id.symbol: q for q in call.result})
        elif call.method == "candles":
            inst = call.arguments["instrument"]
            instruments[inst.id.symbol] = inst
            key = (inst.id.symbol, call.arguments["interval"])
            if len(call.result) > len(bars.get(key, ())):
                bars[key] = call.result
        elif call.method == "derivatives_snapshot":
            derivatives[call.arguments["instrument"].id.symbol] = call.result
    eligible = [s for s in instruments if (s, "4h") in bars and s in quotes]
    # Deterministic rotating coverage, explicitly sampling rather than claiming recall.
    bucket = int(record.completed_at.timestamp()) // 3600
    eligible.sort(key=lambda s: hashlib.sha256(f"{bucket}:{s}".encode()).hexdigest())
    rows = []
    status = "complete"
    for symbol in eligible[:max_symbols]:
        if clock() - start >= seconds:
            status = "budget_exhausted"
            break
        inst, quote = instruments[symbol], quotes[symbol]
        if (
            quote.observed_at.tzinfo is None
            or not quote.is_valid_book
            or quote.quote_volume_24h < 500000
        ):
            rows.append(
                {
                    "symbol": symbol,
                    "reason": "quote_unavailable",
                    "structurally_ready": False,
                    "confirmed_observation": False,
                }
            )
            continue
        result = evaluate_continuation(
            bars.get((symbol, "1h"), ()),
            bars[(symbol, "4h")],
            now=record.completed_at,
            price=quote.last,
            tick=inst.price_tick,
            derivatives=derivatives.get(symbol),
            spread_fraction=(quote.ask - quote.bid) / quote.last,
        )
        quote_current = (
            0 <= (record.completed_at - (quote.exchange_observed_at or quote.observed_at)).total_seconds() <= 60
            and quote.is_fresh_at(record.completed_at, max_age_seconds=60.0, max_future_seconds=0.0)
        )
        rows.append(
            {
                "symbol": symbol,
                "reason": result.reason,
                "gates": result.gates,
                "structurally_ready": result.structurally_ready,
                "confirmed_observation": result.confirmed and quote_current,
                "quote_current": quote_current,
                "plan": result.plan.as_dict() if result.plan else None,
            }
        )
    return {
        "scan_id": record.scan_id,
        "status": status,
        "policy": POLICY_VERSION,
        "scan_anchor": record.started_at.isoformat(),
        "decision_at": record.completed_at.isoformat(),
        "source_identity": record.identity.get("source_hash"),
        "eligible": len(eligible),
        "evaluated": len(rows),
        "max_symbols": max_symbols,
        "deadline_seconds": seconds,
        "unobserved": len(eligible) - len(rows),
        "network_requests": 0,
        "publication_enabled": False,
        "performance_claims_permitted": False,
        "rows": rows,
    }


def write_canary(root: Path, report: dict[str, Any], *, keep: int = 48) -> Path:
    directory = root / "private-continuation"
    if directory.is_symlink():
        raise ValueError("canary directory cannot be a symlink")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    target = directory / f"{UUID(str(report['scan_id']))}.json"
    payload = (json.dumps(report, allow_nan=False, separators=(",", ":")) + "\n").encode()
    if len(payload) > 131072:
        raise ValueError("canary report byte budget exceeded")
    fd, temporary = tempfile.mkstemp(prefix=".canary-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    files = sorted(
        (p for p in directory.glob("*.json") if not p.is_symlink()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in files[keep:]:
        path.unlink()
    return target
