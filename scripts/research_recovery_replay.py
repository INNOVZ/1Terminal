"""Offline current-source core/recovery counterfactual on an original public capture.

Verifies capture receipts and optional study binding. Strict historical replay keeps
its existing source guards. This command never fetches, enrolls or starts workers.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.application.recovery_screener import RecoveryScreener
from algotrader.application.scan_recording import screening_identity
from algotrader.application.scan_replay import ReplayMarket, ReplayMismatch
from algotrader.config import Settings
from algotrader.domain.models import Direction
from algotrader.domain.recovery import RECOVERY_POLICY_VERSION
from algotrader.domain.scan_observations import ScanDecisionJournal
from scripts.replay_contract import ReplayUnavailable, timestamp
from scripts.research_evidence_contract import verify_bundle_inputs
from scripts.research_evidence_stream import read_bundle, semantic_digest
from scripts.research_pipeline_comparison import AuditedTape, tool_identity
from scripts.research_prospective import load
from scripts.research_verified_replay import offline, write_report


class CounterfactualTape(AuditedTape):
    def __init__(self, calls):
        super().__init__(calls)
        self.unavailable = []

    def take(self, method, arguments):
        try:
            return super().take(method, arguments)
        except ReplayMismatch:
            instrument = arguments.get("instrument")
            self.unavailable.append({"method": method,
                                     "symbol": instrument.id.symbol if instrument else None})
            raise


async def run_arm(bundle, recovery):
    record = bundle["record"]
    tape = CounterfactualTape((*record.inputs, *bundle["supplemental"]))
    market = ReplayMarket(tape)
    journal = ScanDecisionJournal()
    if set(record.identity["configuration"]) != {"excluded_base_assets", "support_resistance_lookback_bars"}:
        raise ReplayUnavailable("unsupported_screener_configuration")
    settings = Settings.model_construct(**record.identity["configuration"])
    arguments = dict(record.request)
    enabled = arguments.pop("derivatives_enabled")
    arguments.pop("feature_contract", None)
    constructor = {"evaluated_at": record.started_at, "journal": journal}
    if recovery:
        engine = RecoveryScreener(settings, market, decision_at=timestamp(bundle["completed_at"]), **constructor)
    else:
        engine = CryptoOpportunityScreener(settings, market, feature_contract="discovery_v2", **constructor)
    with redirect_stdout(io.StringIO()), offline():
        opportunities = await engine.screen(market, market if enabled else None, **arguments)
    unknown = {row["symbol"] for row in tape.unavailable if row["symbol"]}
    unknown.update(engine._feature_unavailable)
    unknown.update(engine._fetch_failures)
    # Missing benchmark/global inputs can affect every candidate's context.
    global_missing = any(row["symbol"] in (None, "BTCUSDT") for row in tape.unavailable)
    return {"status": "incomplete" if tape.missing or unknown else "complete",
            "policy": RECOVERY_POLICY_VERSION if recovery else "discovery_v2_current_core",
            "counts": engine.scan_counts, "unavailable_symbols": sorted(unknown),
            "global_input_unavailable": global_missing, "missing_requests": tape.unavailable,
            "verified_input_publications": [o.as_dict() for o in opportunities
                                             if not global_missing and o.instrument.id.symbol not in unknown],
            "decisions": journal.snapshot()}


def replay_identity():
    return {"screening": screening_identity(), "tools": {
        **tool_identity(), Path(__file__).name: hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }}


async def compare(bundle, definition=None):
    identity = await asyncio.to_thread(replay_identity)
    with offline():
        await verify_bundle_inputs(bundle)
    if definition is not None and (
        definition["scan_id"] != bundle["record"].scan_id
        or definition["bundle_sha256"] != semantic_digest(bundle)
    ):
        raise ReplayUnavailable("capture_study_binding_changed")
    core = await run_arm(bundle, False)
    recovery = await run_arm(bundle, True)
    if identity != await asyncio.to_thread(replay_identity):
        raise ReplayUnavailable("counterfactual_source_changed_during_run")
    core_symbols = {row["symbol"] for row in core["verified_input_publications"] if row["direction"] == Direction.LONG.value}
    recovery_symbols = {row["symbol"] for row in recovery["verified_input_publications"] if row["direction"] == Direction.LONG.value}
    return {"mode": "counterfactual_current_source_pair", "scan_id": bundle["record"].scan_id,
            "study_id": definition["study_id"] if definition else None,
            "input_digest": semantic_digest(bundle), "source_identity": identity["screening"],
            "tool_identity": identity["tools"],
            "core": core, "recovery": recovery,
            "long_publication_delta": {"added": sorted(recovery_symbols - core_symbols),
                                        "removed": sorted(core_symbols - recovery_symbols)},
            "performance_claims_permitted": False,
            "limitations": ["one original decision frame", "current-source counterfactual",
                            "missing requests stay unknown", "targets and EV are model assumptions"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--definition", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    result = asyncio.run(compare(read_bundle(args.capture), load(args.definition) if args.definition else None))
    write_report(args.output, result)
    print(json.dumps({"core": result["core"]["status"], "recovery": result["recovery"]["status"],
                      "long_publication_delta": result["long_publication_delta"]}))


if __name__ == "__main__":
    main()
