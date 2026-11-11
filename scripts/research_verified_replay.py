"""Offline strict replay or explicitly assumption-based legacy diagnostic.

No fetching, enrollment, trading, source patching or overwrite of existing evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import socket
import tempfile
from contextlib import contextmanager, redirect_stdout
from dataclasses import replace
from datetime import timedelta
from io import StringIO
from pathlib import Path
from typing import Any

from algotrader.application.scan_recording import screening_configuration, screening_identity
from algotrader.config import Settings
from algotrader.infrastructure.scan_archive import LocalScanArchive
from scripts.replay_contract import (
    ReplayUnavailable,
    digest,
    funding_at,
    reconstruction_check,
    verify_record,
)


def read_json(path: Path) -> Any:
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ReplayUnavailable("input_too_large")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ReplayUnavailable("duplicate_json_key")
            result[key] = value
        return result

    def invalid(_):
        raise ReplayUnavailable("nonfinite_json")

    return json.loads(path.read_text(), object_pairs_hook=pairs, parse_constant=invalid)


@contextmanager
def offline():
    originals = socket.socket, socket.create_connection, socket.getaddrinfo

    def denied(*args, **kwargs):
        raise ReplayUnavailable("network_forbidden")

    socket.socket = socket.create_connection = socket.getaddrinfo = denied
    try:
        yield
    finally:
        socket.socket, socket.create_connection, socket.getaddrinfo = originals


def diagnostic_settings() -> Settings:
    # No BaseSettings constructor/environment sources. The production screener currently consumes
    # only these two settings; all defaults are stable inputs in the fingerprinted source tree.
    values = {
        key: Settings.model_fields[key].get_default()
        for key in ("excluded_base_assets", "support_resistance_lookback_bars")
    }
    return Settings.model_construct(**values)


async def diagnostic(raw: dict[str, Any], results_path: Path) -> dict[str, Any]:
    from scripts.diagnostics import legacy_faithful_48h as legacy

    assessment = reconstruction_check(raw)
    if any(row["data_errors"] for row in assessment["observations"]):
        return {
            **assessment,
            "mode": "diagnostic",
            "status": "unavailable",
            "candidate_results": None,
        }
    settings = diagnostic_settings()
    # Execute exactly the object that passed preflight, never a second read of mutable user input.
    frozen_path = results_path.with_name("frozen_inputs.json")
    with frozen_path.open("x") as handle:
        json.dump(raw, handle, allow_nan=False)
    original_market = legacy.PointInTimeReplayMarket
    journals = []
    original_journal = legacy.ScanDecisionJournal

    class DiagnosticMarket(original_market):
        def _funding_at_cutoff(self, symbol):
            return funding_at(self.raw["symbols"][symbol], self.cutoff_dt + timedelta(seconds=60))

        async def quotes(self, instruments):
            return [
                replace(q, observed_at=self.cutoff_dt + timedelta(seconds=60))
                for q in await super().quotes(instruments)
            ]

        async def derivatives_snapshot(self, instrument):
            return replace(
                await super().derivatives_snapshot(instrument),
                observed_at=self.cutoff_dt + timedelta(seconds=60),
            )

    def journal():
        item = original_journal()
        journals.append(item)
        return item

    # This is an isolated CLI process. No production application imports this diagnostic runner.
    originals = (
        legacy.PointInTimeReplayMarket,
        legacy.ScanDecisionJournal,
        legacy.load_settings,
        legacy.FAITHFUL_RAW_PATH,
        legacy.FAITHFUL_RESULTS_PATH,
    )
    legacy.PointInTimeReplayMarket = DiagnosticMarket
    legacy.ScanDecisionJournal = journal
    legacy.load_settings = lambda: settings
    legacy.FAITHFUL_RAW_PATH = frozen_path
    legacy.FAITHFUL_RESULTS_PATH = results_path
    try:
        with redirect_stdout(StringIO()):
            results = await legacy.run_faithful_replay()
    finally:
        (
            legacy.PointInTimeReplayMarket,
            legacy.ScanDecisionJournal,
            legacy.load_settings,
            legacy.FAITHFUL_RAW_PATH,
            legacy.FAITHFUL_RESULTS_PATH,
        ) = originals
    return {
        **assessment,
        "mode": "diagnostic",
        "status": "diagnostic_only",
        "performance_claims_permitted": False,
        "configuration": screening_configuration(settings),
        "candidate_results": results,
        "hypothesis_rule": "legacy_first_created_opportunity_not_publication_aware",
        "notes": [
            "Funding interval inferred from preceding settlements for diagnostics only.",
            "Decision availability assumed 60 seconds after signal close; arrivals unverified.",
            "Signal counts are not prediction success rates; no strict denominator is populated.",
            "Legacy close-to-close outcomes remain signal-bar price diagnostics, not executable decision-time returns.",
        ],
        "pipeline_journals": [
            {
                "horizon": legacy.HORIZONS_HOURS[i // 2],
                "model": "control" if i % 2 == 0 else "legacy_challenger",
                "events": j.snapshot(),
            }
            for i, j in enumerate(journals)
        ],
    }


async def execute(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    identity = screening_identity()
    if args.inputs:
        raw = read_json(args.inputs)
        assessment = reconstruction_check(raw)
        if args.mode == "strict":
            return {
                **assessment,
                "mode": "strict",
                "status": "unavailable",
                "candidate_results": None,
                "input_sha256": digest(raw),
                "identity": identity,
            }, 3
        # Keep the legacy runner's temporary write outside the input/evidence directories. The
        # final JSON embeds results; the temporary file is always removed on success or failure.
        import tempfile

        with tempfile.TemporaryDirectory(prefix="atlas-diagnostic-") as directory:
            report = await diagnostic(raw, Path(directory) / "results.json")
        return {**report, "input_sha256": digest(raw), "identity": identity}, (
            0 if report["status"] == "diagnostic_only" else 3
        )
    if args.mode != "strict" or not (args.archive and args.scan_id and args.manifest):
        raise ReplayUnavailable("strict_archive_requires_scan_id_and_manifest")
    record = LocalScanArchive(args.archive).read_scan(args.scan_id)
    report = await verify_record(record, read_json(args.manifest))
    return {
        "schema": "atlas-verified-replay-v1",
        "mode": "strict",
        "status": "verified",
        "scan_id": record.scan_id,
        "record_sha256": digest(record),
        "matches": report.matches,
        "signal_evaluated_at": record.started_at.isoformat(),
        "decision_at": record.completed_at.isoformat(),
        "predictive_outcomes_evaluated": False,
        "counts": report.counts,
        "negative_forecasts": 0,
        "identity": identity,
        "note": "Replay consistency verified; no claim of predictive edge or source authenticity.",
    }, 0


async def run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    try:
        with offline():
            report, status = await execute(args)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        # Do not write provider exception text, environment values or malformed payloads into reports.
        reason = str(exc) if isinstance(exc, ReplayUnavailable) else type(exc).__name__
        report = {
            "schema": "atlas-verified-replay-v1",
            "mode": args.mode,
            "status": "unavailable",
            "candidate_results": None,
            "negative_forecasts": 0,
            "reason": reason,
        }
        status = 3
    report["replay_tool_sha256"] = tool_identity()
    return report, status


def tool_identity() -> dict[str, str]:
    root = Path(__file__).resolve().parent
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (
            root / "replay_contract.py",
            root / "research_verified_replay.py",
            root / "diagnostics/legacy_faithful_48h.py",
        )
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    """Publish a complete JSON report atomically; an existing path is never replaced."""
    payload = (json.dumps(report, indent=2, allow_nan=False) + "\n").encode()
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=".atlas-replay-", delete=False
    ) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("strict", "diagnostic"), default="strict")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--inputs", type=Path)
    source.add_argument("--archive", type=Path)
    parser.add_argument("--scan-id")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; choose a new evidence file")
    report, status = asyncio.run(run(args))
    write_report(args.output, report)
    print(
        json.dumps({"status": report["status"], "mode": report["mode"], "output": str(args.output)})
    )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
