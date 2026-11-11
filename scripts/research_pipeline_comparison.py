"""Offline actual-pipeline funding ablation; no collection, trading or live switch."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Any

from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.application.scan_recording import screening_identity
from algotrader.application.scan_replay import ReplayMarket, ReplayMismatch, ReplayTape
from algotrader.config import Settings
from algotrader.domain.models import DerivativesSnapshot, Quote
from algotrader.domain.scan_observations import (
    ScanDecisionJournal,
    ScanRecord,
    decode_value,
    encode_value,
)
from algotrader.infrastructure.scan_archive import LocalScanArchive, canonical_bytes
from scripts.replay_contract import ReplayUnavailable, digest, fresh, timestamp, verify_record
from scripts.research_immutable_capture import freeze
from scripts.research_verified_replay import offline, read_json, write_report


class FundingCarryScreener(CryptoOpportunityScreener):
    """Research-only subclass. Caller must first validate every quote funding row."""

    def _accumulation_funding_rate(
        self, quote: Quote, derivatives: DerivativesSnapshot | None
    ) -> Decimal | None:
        return derivatives.funding_rate if derivatives is not None else quote.funding_rate


class SnapshotCall:
    """Share immutable result values; each tape owns its call-consumption state."""

    def __init__(self, call):
        self.method, self.index, self.error = call.method, call.index, call.error
        self.arguments = decode_value(encode_value(call.arguments))
        self._result = call.result

    @property
    def result(self):
        return freeze(self._result)


class AuditedTape(ReplayTape):
    def __init__(self, calls):
        super().__init__(tuple(SnapshotCall(call) for call in calls))
        self.requested: list[dict[str, Any]] = []

    def take(self, method, arguments):
        self.requested.append({"method": method, "arguments_sha256": digest(arguments)})
        return super().take(method, arguments)


def validate_quote_funding(record: ScanRecord, manifest: dict[str, Any]) -> None:
    """Extra attestation extends strict v1; it is not inferred from reconstructed data."""
    proofs = {p["call_sha256"]: p for p in manifest["calls"]}
    for call in record.inputs:
        if call.method != "quotes":
            continue
        proof = proofs[digest(call)]
        rows = proof.get("quote_funding")
        expected = {q.instrument_id.symbol for q in call.result}
        if manifest.get("schema") in ("atlas-public-evidence-v2", "atlas-public-evidence-v3", "atlas-public-evidence-v4"):
            expected &= set(manifest["selected_symbols"])
        if not isinstance(rows, dict) or set(rows) != expected:
            raise ReplayUnavailable("quote_funding_provenance_incomplete")
        for quote in call.result:
            if quote.instrument_id.symbol not in expected:
                continue
            row = rows[quote.instrument_id.symbol]
            if not isinstance(row, dict) or row.get("origin") != "premium_index":
                raise ReplayUnavailable("quote_funding_origin_unverified")
            interval = row.get("interval_seconds")
            if type(interval) is not int or not 3600 <= interval <= 86400:
                raise ReplayUnavailable("quote_funding_interval_unknown")
            observed = fresh(row.get("observed_at"), timestamp(proof["observed_at"]), 60)
            available = timestamp(row.get("available_at"))
            if not observed <= available <= timestamp(proof["available_at"]):
                raise ReplayUnavailable("quote_funding_not_available")
            try:
                rate = Decimal(row["rate"])
            except (KeyError, TypeError, ArithmeticError) as exc:
                raise ReplayUnavailable("quote_funding_value_unverified") from exc
            if not rate.is_finite() or rate != quote.funding_rate:
                raise ReplayUnavailable("quote_funding_value_mismatch")


async def run_arm(record: ScanRecord, challenger: bool, supplemental=()) -> dict[str, Any]:
    # Each arm owns its tape and shares only read-only snapshot values.
    tape = AuditedTape((*record.inputs, *(supplemental if challenger else ())))
    market = ReplayMarket(tape)
    journal = ScanDecisionJournal()
    # Avoid ambient BaseSettings and dotenv. These are the only consumed screener settings.
    config = record.identity["configuration"]
    expected = {"excluded_base_assets", "support_resistance_lookback_bars"}
    if set(config) != expected:
        raise ReplayUnavailable("unsupported_screener_configuration")
    settings = Settings.model_construct(**config)
    cls = FundingCarryScreener if challenger else CryptoOpportunityScreener
    arguments = dict(record.request)
    feature_contract = arguments.pop("feature_contract", "legacy")
    screener = cls(settings, market, evaluated_at=record.started_at, journal=journal,
                   feature_contract=feature_contract)
    enabled = arguments.pop("derivatives_enabled")
    opportunities = await screener.screen(
        market, derivatives_port=market if enabled else None, **arguments
    )
    usage = {
        "requested_calls": tape.requested,
        "unused_calls": sum(len(group) for group in tape.calls.values()),
        "missing_input_requested": tape.missing,
    }
    if tape.missing:
        if not challenger:
            raise ReplayUnavailable("repeat_control_input_not_recorded")
        return {
            "status": "unavailable",
            "reason": "challenger_input_not_recorded",
            "candidate_results": None,
            **usage,
        }
    fixed_stages = {"universe", "liquidity", "selection"}
    fixed_decisions = tuple(e for e in journal.snapshot() if e["stage"] in fixed_stages)
    expected_decisions = tuple(e for e in record.decisions if e["stage"] in fixed_stages)
    if canonical_bytes(journal.cohort) != canonical_bytes(record.cohort) or (
        canonical_bytes(fixed_decisions) != canonical_bytes(expected_decisions)
    ):
        raise ReplayUnavailable("pre_funding_funnel_changed")
    result = {
        "opportunities": tuple(item.as_dict() for item in opportunities),
        "counts": screener.scan_counts,
        "decisions": journal.snapshot(),
        "cohort_symbols": tuple(inst.id.symbol for inst in journal.cohort),
    }
    if not challenger:
        tape.assert_consumed()
        if canonical_bytes(result) != canonical_bytes(
            {
                "opportunities": record.opportunities,
                "counts": record.counts,
                "decisions": record.decisions,
                "cohort_symbols": tuple(inst.id.symbol for inst in record.cohort),
            }
        ):
            raise ReplayUnavailable("repeat_control_mismatch")
    return {"status": "complete", "candidate_results": result, **usage}


async def compare(record: ScanRecord, manifest: dict[str, Any]) -> dict[str, Any]:
    # Strict gate includes full-provider universe, proven inputs, source identity and exact replay.
    await verify_record(record, manifest)
    control = await run_arm(record, False)
    try:
        validate_quote_funding(record, manifest)
        challenger = await run_arm(record, True)
    except ReplayUnavailable as exc:
        challenger = {"status": "unavailable", "reason": str(exc), "candidate_results": None}
    return {
        "schema": "atlas-pipeline-comparison-v1",
        "status": "complete" if challenger["status"] == "complete" else "incomplete",
        "scan_id": record.scan_id,
        "signal_evaluated_at": record.started_at.isoformat(),
        "decision_at": record.completed_at.isoformat(),
        "record_sha256": digest(record),
        "manifest_sha256": digest(manifest),
        "identity": screening_identity(),
        "control": control,
        "quote_funding_carry": challenger,
        "performance_claims_permitted": False,
        "negative_forecasts": 0,
        "predictive_outcomes_evaluated": False,
        "arm_policy": "quote-funding-carry-v1-raw-rate-unchanged-budgets",
        "scope": "Pipeline consistency only; provenance is operator attestation, not source authentication.",
    }


async def compare_bundle(bundle):
    from scripts.research_evidence_contract import funding_manifest, verify_bundle
    from scripts.research_evidence_stream import semantic_digest

    record = await verify_bundle(bundle)
    control = await run_arm(record, False)
    try:
        manifest = funding_manifest(bundle)
        validate_quote_funding(record, manifest)
        challenger = await run_arm(record, True, bundle["supplemental"])
    except ReplayUnavailable as exc:
        challenger = {"status": "unavailable", "reason": str(exc), "candidate_results": None}
    discovery = None
    if bundle["schema"] == "atlas-public-evidence-v4":
        from scripts.research_discovery import run_discovery

        discovery = await run_discovery(record, bundle["supplemental"]) if challenger["status"] == "complete" else {
            "status": "unavailable", "reason": "funding_provenance_unavailable", "candidate_results": None}
    return {
        "schema": "atlas-pipeline-comparison-v2",
        "status": "complete" if challenger["status"] == "complete" and (discovery is None or discovery["status"] == "complete") else "incomplete",
        "scan_id": record.scan_id,
        "record_sha256": semantic_digest(record),
        "bundle_sha256": semantic_digest(bundle),
        "input_contract": bundle["schema"],
        "capture_latency_seconds": (
            timestamp(bundle["completed_at"]) - record.started_at
        ).total_seconds(),
        "identity": screening_identity(),
        "signal_evaluated_at": record.started_at.isoformat(),
        "control_original_completed_at": record.completed_at.isoformat(),
        "decision_at": timestamp(bundle["completed_at"]).isoformat(),
        "control": control,
        "quote_funding_carry": challenger,
        "discovery_v2": discovery,
        "performance_claims_permitted": False,
        "predictive_outcomes_evaluated": False,
        "negative_forecasts": 0,
        "enrolled": False,
        "outcome_anchor": None,
        "scope": "Adapter-fidelity experiment on newly captured evidence; no outcome enrollment or forecast validation.",
    }


def tool_identity() -> dict[str, str]:
    root = Path(__file__).resolve().parent
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (
            Path(__file__),
            root / "replay_contract.py",
            root / "research_verified_replay.py",
            root / "research_evidence_contract.py",
            root / "research_capture_evidence.py",
            root / "research_evidence_stream.py",
            root / "research_immutable_capture.py",
            root / "research_discovery.py",
        )
    }


async def execute(args) -> tuple[dict[str, Any], int]:
    try:
        with offline():
            if args.bundle:
                from scripts.research_evidence_stream import read_bundle

                report = await compare_bundle(read_bundle(args.bundle))
            else:
                if not (args.archive and args.scan_id and args.manifest):
                    raise ReplayUnavailable("strict_archive_requires_scan_id_and_manifest")
                record = LocalScanArchive(args.archive).read_scan(args.scan_id)
                report = await compare(record, read_json(args.manifest))
        status = 0 if report["status"] == "complete" else 3
    except (
        ReplayUnavailable,
        ReplayMismatch,
        KeyError,
        TypeError,
        OSError,
        ValueError,
        ArithmeticError,
        AttributeError,
        IndexError,
    ) as exc:
        report = {
            "schema": "atlas-pipeline-comparison-v1",
            "status": "unavailable",
            "reason": str(exc) if isinstance(exc, ReplayUnavailable) else type(exc).__name__,
            "candidate_results": None,
            "performance_claims_permitted": False,
            "negative_forecasts": 0,
        }
        status = 3
    report["tool_sha256"] = tool_identity()
    return report, status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive", type=Path)
    source.add_argument("--bundle", type=Path)
    parser.add_argument("--scan-id")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; choose a new evidence file")
    report, status = asyncio.run(execute(args))
    write_report(args.output, report)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
