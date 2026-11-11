"""Commands for screening and serving the research portal."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import uvicorn

from algotrader.config import load_settings
from algotrader.domain.crypto_screener_models import ScreenerMode
from algotrader.domain.screener_policy import SCREENING_POLICY_VERSION
from algotrader.infrastructure.runtime import build_runtime
from algotrader.infrastructure.scan_archive import LocalScanArchive, validated_id


async def _screen(mode: str, min_score: float, max_symbols: int) -> int:
    runtime = await build_runtime(load_settings())
    try:
        run = await runtime.run_crypto_scan(
            mode=ScreenerMode(mode),
            min_score=Decimal(str(min_score)),
            max_symbols=max_symbols,
            feature_contract="discovery_v2",
        )
        print(
            json.dumps(
                {
                    "mode": "screener",
                    "order_execution_enabled": False,
                    "validation_status": "uncalibrated_research",
                    "screening_policy_version": SCREENING_POLICY_VERSION,
                    "screening_feature_contract": "discovery_v2",
                    "scan_counts": run.counts,
                    "scan_id": run.receipt.scan_id,
                    "scan_archive": run.receipt.as_dict(),
                    "opportunities": [item.as_dict() for item in run.opportunities],
                },
                indent=2,
                default=str,
            )
        )
        return 0
    finally:
        await runtime.close()


async def _replay(root: Path, scan_id: str) -> int:
    from algotrader.application.scan_replay import replay_scan

    record = await asyncio.to_thread(LocalScanArchive(root).read_scan, scan_id)
    with contextlib.redirect_stdout(sys.stderr):
        report = await replay_scan(record)
    print(json.dumps({"scan_id": scan_id, **asdict(report)}, default=str))
    return 0


async def _observe(root: Path, scan_id: str, horizon: int, max_symbols: int) -> int:
    from algotrader.jobs.outcome_observer import observe_scan

    with contextlib.redirect_stdout(sys.stderr):
        result = await observe_scan(root, scan_id, horizon, max_symbols)
    print(json.dumps(result, default=str))
    return 0


async def _shadow(root: Path, identifier: str, *, replay: bool) -> int:
    from algotrader.infrastructure.shadow_archive import LocalShadowArchive

    try:
        with contextlib.redirect_stdout(sys.stderr):
            if replay:
                from algotrader.application.shadow_replay import replay_shadow

                record = await asyncio.to_thread(LocalShadowArchive(root).read_run, identifier)
                parent = await asyncio.to_thread(LocalScanArchive(root).read_scan, record.parent_id)
                result = await replay_shadow(record, parent)
                code = 0
            else:
                from algotrader.jobs.shadow_scan import run_shadow_scan

                record = await run_shadow_scan(root, identifier)
                result = {
                    "shadow_id": record.shadow_id,
                    "parent_id": record.parent_id,
                    "status": record.status,
                    "arm": "discovery_shadow",
                    "validation_status": "uncalibrated_research",
                    "qualified_count": sum(item["qualified"] for item in record.results),
                }
                code = 0 if record.status == "complete" else 1
    except (ValueError, OSError) as exc:
        result, code = (
            {"status": "failed", "error": type(exc).__name__, "arm": "discovery_shadow"},
            1,
        )
    print(json.dumps(result, default=str))
    return code


async def _observe_shadow(root: Path, identifier: str, horizon: int, max_symbols: int) -> int:
    from algotrader.jobs.shadow_scan import observe_shadow_scan

    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = await observe_shadow_scan(root, identifier, horizon, max_symbols)
        code = 0
    except (ValueError, OSError) as exc:
        result, code = (
            {"status": "failed", "error": type(exc).__name__, "arm": "discovery_shadow"},
            1,
        )
    print(json.dumps(result, default=str))
    return code


async def _snapshot_ranks(root: Path, identifier: str) -> int:
    from algotrader.application.rank_collector import RankCollector
    from algotrader.domain.rank_observations import RankPolicy

    try:
        with contextlib.redirect_stdout(sys.stderr):
            record = await RankCollector(root, RankPolicy()).collect(identifier)
        result = {
            "snapshot_id": record.snapshot_id,
            "parent_id": record.parent_id,
            "status": record.status,
            "coverage": record.normalized.get("coverage", {}),
            "accounting": record.accounting,
        }
        code = 0 if record.status == "complete" else 1
    except (ValueError, OSError) as exc:
        result, code = {"status": "failed", "error": type(exc).__name__}, 1
    print(json.dumps(result, default=str))
    return code


def _benchmark_shadow(root: Path, identifier: str, horizon: int, as_of: datetime) -> int:
    from algotrader.application.shadow_benchmark import ShadowBenchmark
    from algotrader.domain.rank_observations import RankPolicy

    try:
        report = ShadowBenchmark(root, RankPolicy()).build(identifier, horizon, as_of=as_of)
        result = {
            "report_id": report.report_id,
            "parent_id": report.parent_id,
            "shadow_id": report.shadow_id,
            "as_of": report.as_of,
            **report.payload,
        }
        code = 0
    except (ValueError, OSError) as exc:
        result, code = {"status": "failed", "error": type(exc).__name__}, 1
    print(json.dumps(result, default=str))
    return code


def _research_command(args: argparse.Namespace) -> int:
    from algotrader.application.research_sessions import ResearchSessions
    from algotrader.jobs.research_worker import run_research_worker, utc_now, worker_health

    root = (
        args.archive_dir if args.archive_dir is not None else load_settings().research_archive_dir
    )
    try:
        service = ResearchSessions(root)
        if args.command == "research-start":
            session = service.start(args.shadow_id, now=utc_now())
            result = service.status(session.session_id, now=utc_now())
        elif args.command == "research-health":
            result = worker_health(root, args.session_id, args.role)
            print(json.dumps(result, default=str))
            return 0 if result["healthy"] else 1
        elif args.command == "research-worker":
            return asyncio.run(run_research_worker(root, args.session_id, args.role))
        else:
            if args.command == "research-stop":
                service.stop(args.session_id, now=utc_now())
            result = service.status(args.session_id, now=utc_now())
        print(json.dumps(result, default=str))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"status": "failed", "error": type(exc).__name__}))
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Atlas public-data crypto screener")
    commands = parser.add_subparsers(dest="command", required=True)
    screen = commands.add_parser(
        "crypto-screen", aliases=["screen"], help="Screen public USD-M market data"
    )
    screen.add_argument("--mode", choices=[mode.value for mode in ScreenerMode], default="all")
    screen.add_argument("--min-score", type=float, default=68.0)
    screen.add_argument("--max-symbols", type=int, default=320)
    serve = commands.add_parser("portal", aliases=["serve"], help="Serve the screener API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    commands.add_parser("doctor", help="Report local screener configuration without network access")
    for name in (
        "research-start",
        "research-status",
        "research-stop",
        "research-worker",
        "research-health",
    ):
        research = commands.add_parser(name, help="Explicit opt-in research session operation")
        research.add_argument(
            "--shadow-id" if name == "research-start" else "--session-id",
            required=True,
            type=validated_id,
        )
        research.add_argument("--archive-dir", type=Path)
        if name in ("research-worker", "research-health"):
            research.add_argument("--role", required=True, choices=["ranks", "outcomes"])
    rank_command = commands.add_parser(
        "snapshot-ranks", help="Capture one bounded public rank snapshot"
    )
    rank_command.add_argument("--scan-id", required=True, type=validated_id)
    rank_command.add_argument("--archive-dir", type=Path)
    benchmark = commands.add_parser(
        "benchmark-shadow", help="Build an offline matched-anchor research benchmark"
    )
    benchmark.add_argument("--shadow-id", required=True, type=validated_id)
    benchmark.add_argument("--archive-dir", type=Path)
    benchmark.add_argument("--horizon-hours", type=int, required=True, choices=[24, 48])
    benchmark.add_argument("--as-of", type=datetime.fromisoformat)
    for name in ("shadow-scan", "replay-shadow", "observe-shadow"):
        command = commands.add_parser(
            name, help="Manual shadow-only research; never changes published results"
        )
        command.add_argument(
            "--scan-id" if name == "shadow-scan" else "--shadow-id",
            required=True,
            type=validated_id,
        )
        command.add_argument("--archive-dir", type=Path)
        if name == "observe-shadow":
            command.add_argument("--horizon-hours", required=True, type=int, choices=[24, 48])
            command.add_argument("--max-symbols", type=int, default=100)
    for name, help_text in (
        ("replay-scan", "Replay an archived scan offline"),
        ("observe-scan", "Measure a fixed 24h/48h public-data price path"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--scan-id", required=True, type=validated_id)
        command.add_argument("--archive-dir", type=Path)
        if name == "observe-scan":
            command.add_argument("--horizon-hours", type=int, required=True, choices=[24, 48])
            command.add_argument("--max-symbols", type=int, default=100)
    args = parser.parse_args()
    if args.command.startswith("research-"):
        return _research_command(args)
    if args.command == "benchmark-shadow":
        if args.as_of is not None and args.as_of.utcoffset() is None:
            parser.error("as-of must include a timezone")
        root = (
            args.archive_dir
            if args.archive_dir is not None
            else load_settings().research_archive_dir
        )
        return _benchmark_shadow(
            root, args.shadow_id, args.horizon_hours, args.as_of or datetime.now(UTC)
        )
    if args.command == "snapshot-ranks":
        root = (
            args.archive_dir
            if args.archive_dir is not None
            else load_settings().research_archive_dir
        )
        return asyncio.run(_snapshot_ranks(root, args.scan_id))
    if args.command in {"shadow-scan", "replay-shadow", "observe-shadow"}:
        root = (
            args.archive_dir
            if args.archive_dir is not None
            else load_settings().research_archive_dir
        )
        if args.command == "observe-shadow":
            if not 1 <= args.max_symbols <= 1000:
                parser.error("max-symbols must be 1..1000")
            return asyncio.run(
                _observe_shadow(root, args.shadow_id, args.horizon_hours, args.max_symbols)
            )
        identifier = args.shadow_id if args.command == "replay-shadow" else args.scan_id
        return asyncio.run(_shadow(root, identifier, replay=args.command == "replay-shadow"))
    if args.command in {"replay-scan", "observe-scan"}:
        root = (
            args.archive_dir
            if args.archive_dir is not None
            else load_settings().research_archive_dir
        )
        if args.command == "replay-scan":
            return asyncio.run(_replay(root, args.scan_id))
        if not 1 <= args.max_symbols <= 1000:
            parser.error("max-symbols must be 1..1000")
        return asyncio.run(_observe(root, args.scan_id, args.horizon_hours, args.max_symbols))
    if args.command in {"screen", "crypto-screen"}:
        if not 0 <= args.min_score <= 100 or not 1 <= args.max_symbols <= 500:
            parser.error("min-score must be 0..100 and max-symbols must be 1..500")
        return asyncio.run(_screen(args.mode, args.min_score, args.max_symbols))
    if args.command in {"portal", "serve"}:
        uvicorn.run("algotrader.api.portal_app:app", host=args.host, port=args.port)
        return 0
    settings = load_settings()
    print(
        json.dumps(
            {
                "mode": "screener",
                "order_execution_enabled": False,
                "credentials_required": False,
                "environment": settings.environment.value,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
