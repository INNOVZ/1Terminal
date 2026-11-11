"""Offline counterfactual using original inputs and verified overlapping hourly history.

Source changes intentionally produce new decisions. Missing inputs stay unavailable.
This diagnostic never changes strict replay validation or enrolls a research study.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
from contextlib import redirect_stdout
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.application.scan_recording import screening_identity
from algotrader.config import Settings
from algotrader.domain.models import Candle
from algotrader.domain.scan_observations import ScanDecisionJournal
from scripts.replay_contract import digest
from scripts.research_evidence_stream import read_bundle, semantic_digest
from scripts.research_prospective import load
from scripts.research_study_pre_gainer_analysis import read_result
from scripts.research_verified_replay import offline


class EvidenceMarket:
    def __init__(self, record, supplemental, manifest):
        self.record = record
        self.calls = {(c.method, digest(c.arguments)): c for c in (*record.inputs, *supplemental)}
        self.raw_hashes = {r["path"]: r["sha256"] for r in manifest["raw_files"]}
        self.missing = []
        self.extended = set()

    def recorded(self, method, arguments):
        call = self.calls.get((method, digest(arguments)))
        if call is None or call.error:
            self.missing.append({"method": method, "symbol": arguments.get("instrument").id.symbol
                                 if "instrument" in arguments else None,
                                 "arguments_sha256": digest(arguments)})
            raise ValueError("historical_input_unavailable")
        return call.result

    async def instruments(self):
        return self.recorded("instruments", {})

    async def quotes(self, instruments):
        requested = {i.id for i in instruments}
        for call in self.calls.values():
            if call.method == "quotes" and not call.error:
                recorded_ids = {i.id for i in call.arguments["instruments"]}
                if requested <= recorded_ids:
                    return tuple(q for q in call.result if q.instrument_id in requested)
        return self.recorded("quotes", {"instruments": tuple(instruments)})

    async def get_context(self):
        return self.recorded("macro", {})

    async def derivatives_snapshot(self, instrument):
        return self.recorded("derivatives_snapshot", {"instrument": instrument})

    async def candles(self, instrument, interval, limit):
        arguments = {"instrument": instrument, "interval": interval, "limit": limit}
        if interval == "1h" and limit == 360:
            path = Path("docs/security/evidence/2026-11-11-pre-gainer-raw") / f"{instrument.id.symbol}.json"
            original = self.calls.get(("candles", digest({**arguments, "limit": 240})))
            # Only extend histories with an exact original closed-bar overlap.
            if original is not None and not original.error and path.exists():
                raw = path.read_bytes()
                if self.raw_hashes.get(str(path)) != hashlib.sha256(raw).hexdigest():
                    self.missing.append({"method": "candles", "symbol": instrument.id.symbol,
                                         "reason": "historical_history_checksum_changed"})
                    raise ValueError("historical_history_checksum_changed")
                rows = read_result(json.loads(raw)["results"]["klines"])
                candles = tuple(Candle(
                    opened_at=datetime.fromtimestamp(r[0] / 1000, UTC),
                    closed_at=datetime.fromtimestamp(r[6] / 1000, UTC),
                    open=Decimal(r[1]), high=Decimal(r[2]), low=Decimal(r[3]), close=Decimal(r[4]),
                    volume=Decimal(r[5]), quote_volume=Decimal(r[7]),
                    taker_buy_volume=Decimal(r[9]), taker_buy_quote_volume=Decimal(r[10]),
                ) for r in rows if r[6] < self.record.started_at.timestamp() * 1000)
                overlap = tuple(c for c in original.result if c.closed)
                if overlap and candles[-len(overlap):] == overlap:
                    self.extended.add(instrument.id.symbol)
                    return candles[-360:]
                self.missing.append({"method": "candles", "symbol": instrument.id.symbol,
                                     "reason": "historical_history_overlap_changed"})
                raise ValueError("historical_history_overlap_changed")
        return self.recorded("candles", arguments)


async def replay(bundle, manifest, cohort, contract):
    record = bundle["record"]
    market = EvidenceMarket(record, bundle["supplemental"], manifest)
    journal = ScanDecisionJournal()
    engine = CryptoOpportunityScreener(Settings.model_construct(**record.identity["configuration"]),
                                      market, evaluated_at=record.started_at, journal=journal,
                                      feature_contract=contract)
    request = {**record.request, "max_symbols": 320}
    request.pop("derivatives_enabled")
    request.pop("feature_contract", None)
    with redirect_stdout(io.StringIO()), offline():
        opportunities = await engine.screen(market, market, **request)
    unavailable = {r["symbol"] for r in market.missing if r["symbol"]} | engine._feature_unavailable
    decisions = journal.snapshot()
    selected = {r["symbol"] for r in decisions if r["stage"] == "selection" and r["reason"] == "selected"}
    entrants = {r["symbol"] for r in cohort["symbols"]
                if r["initial_inside"] is False and r["first_observed_top10_at"] is not None}
    return {
        "feature_contract": contract, "engine_identity": screening_identity(), "counts": engine.scan_counts,
        "status": "incomplete" if unavailable or engine.scan_counts["failed"] else "complete",
        "selected_new_entrants": sorted(entrants & selected), "selected_symbols": sorted(selected),
        "unavailable_symbols": sorted(unavailable), "missing_requests": market.missing,
        "extended_hourly_symbols": sorted(market.extended),
        "verified_input_publications": [o.as_dict() for o in opportunities if o.instrument.id.symbol not in unavailable],
        "decisions": decisions,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--definition", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--contract", choices=("legacy", "discovery_v2"), default="discovery_v2")
    args = parser.parse_args()
    evidence = Path("docs/security/evidence")
    manifest = json.loads((evidence / "2026-11-11-pre-gainer-manifest.json").read_text())
    cohort_path = evidence / "2026-11-11-pre-gainer-cohort-summary.json"
    if hashlib.sha256(cohort_path.read_bytes()).hexdigest() != manifest["cohort_summary_sha256"]:
        raise ValueError("cohort_checksum_changed")
    bundle = read_bundle(args.capture)
    cohort = json.loads(cohort_path.read_text())
    definition = load(args.definition)
    wrapper = json.loads(args.definition.read_text())
    if (wrapper["sha256"] != cohort["definition_sha256"]
            or definition["study_id"] != cohort["study_id"]
            or definition["scan_id"] != bundle["record"].scan_id
            or definition["bundle_sha256"] != semantic_digest(bundle)
            or bundle["status"] != "captured" or bundle["record"].status != "complete"):
        raise ValueError("capture_study_binding_changed")
    result = asyncio.run(replay(bundle, manifest, cohort, args.contract))
    result.update(mode="counterfactual_partial_input_replay", capture_sha256=hashlib.sha256(args.capture.read_bytes()).hexdigest(),
                  scan_id=bundle["record"].scan_id, issued_input_time=bundle["record"].started_at.isoformat(),
                  study_id=cohort["study_id"], definition_sha256=wrapper["sha256"],
                  bundle_sha256=definition["bundle_sha256"],
                  original_counts=bundle["record"].counts, performance_claims_permitted=False,
                  limitations=["Retrospective hourly extension with exact archived overlap",
                               "Historical 320-symbol coverage lacks full candle/derivatives inputs",
                               "Source identity differs from original capture; strict replay remains guarded"])
    with args.output.open("x") as handle:
        json.dump(result, handle, default=str, sort_keys=True, allow_nan=False)
    print(json.dumps({k: result[k] for k in ("status", "counts", "selected_new_entrants", "unavailable_symbols")}))


if __name__ == "__main__":
    main()
