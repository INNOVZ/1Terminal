"""Explicit synthetic full-cohort report capacity check, not a live load claim."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from algotrader.application.shadow_benchmark import ShadowBenchmark
from algotrader.domain.rank_observations import RankPolicy, normalize_ranks
from algotrader.domain.scan_observations import RecordedCall
from algotrader.infrastructure.rank_archive import LocalRankArchive, digest
from algotrader.infrastructure.scan_archive import LocalScanArchive
from algotrader.infrastructure.shadow_archive import LocalShadowArchive
from tests.test_rank_archive import snapshot_fixture
from tests.test_shadow_archive import record_fixture
from tests.test_shadow_discovery import NOW, parent_fixture, ticker_fixture


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--snapshots", type=int, choices=(120, 289, 577), default=577)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="atlas-report-capacity-") as directory:
        root = Path(directory)
        parent = replace(
            parent_fixture(1000),
            completed_at=NOW - timedelta(seconds=20),
            opportunities=({"symbol": "T1USDT", "direction": "short"},),
        )
        LocalScanArchive(root).write_scan(parent)
        child = replace(
            record_fixture(),
            cohort=parent.cohort,
            parent_checksum=digest(parent),
            started_at=NOW - timedelta(seconds=10),
            completed_at=NOW,
            inputs=(RecordedCall("decision_tickers", 0, {}, ticker_fixture(parent)),),
            timing={"snapshot_requested_at": NOW, "snapshot_arrived_at": NOW, "classified_at": NOW},
            results=({"symbol": "T0USDT", "qualified": True, "direction": "long"},),
        )
        LocalShadowArchive(root).write_run(child)
        # Archive round-trip helpers omit candidate semantics; preflight the
        # report before spending time generating a full horizon of evidence.
        ShadowBenchmark(root, RankPolicy()).freeze_inputs(
            child.shadow_id, 48, as_of=NOW + timedelta(hours=60)
        )
        template = replace(
            snapshot_fixture(),
            parent_checksum=digest(parent),
            cohort=parent.cohort,
            cohort_hash=digest(parent.cohort),
        )
        archive = LocalRankArchive(root)
        for slot in range(args.snapshots):
            stamp = NOW + timedelta(minutes=slot * 5)
            rows = tuple(
                dict(row, closeTime=int(stamp.timestamp() * 1000)) for row in ticker_fixture(parent)
            )
            archive.write_snapshot(
                replace(
                    template,
                    snapshot_id=str(uuid4()),
                    rows=rows,
                    normalized=normalize_ranks(parent.cohort, rows, stamp, RankPolicy()),
                    requested_at=stamp,
                    arrived_at=stamp,
                    completed_at=stamp,
                )
            )
            if slot % 120 == 0:
                print(json.dumps({"fixture_snapshots_written": slot + 1}), flush=True)
        # Synthetic temp-only root: allow the production non-root UID to create
        # its benchmark namespace, without chowning any user/project directory.
        os.chmod(root, 0o777)
        code = (
            "import json,resource,os; os.umask(0); from pathlib import Path; from datetime import datetime; "
            "from algotrader.application.shadow_benchmark import ShadowBenchmark; "
            "from algotrader.domain.rank_observations import RankPolicy; "
            f"r=ShadowBenchmark(Path('/app/data/research'),RankPolicy()).build('{child.shadow_id}',48,as_of=datetime.fromisoformat('{(NOW + timedelta(hours=60)).isoformat()}')); "
            f"assert len(r.manifest['rank_snapshots'])=={args.snapshots}; "
            "assert r.payload['original_cohort_size']==1000; "
            "print(json.dumps({'cohort':1000,'snapshots':len(r.manifest['rank_snapshots']),"
            "'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'network':'none','memory_limit_mib':256}))"
        )
        name = "atlas-report-capacity-" + uuid4().hex
        try:
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    name,
                    "--platform",
                    "linux/amd64",
                    "--network",
                    "none",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--memory",
                    "256m",
                    "--cpus",
                    "0.5",
                    "--user",
                    "10001:10001",
                    "--tmpfs",
                    "/tmp:size=64m,noexec,nosuid",
                    "--mount",
                    f"type=bind,source={root},target=/app/data/research",
                    args.image,
                    "python",
                    "-c",
                    code,
                ],
                check=False,
                timeout=900,
            )
            return result.returncode
        finally:
            subprocess.run(["docker", "rm", "--force", name], check=False, capture_output=True)


if __name__ == "__main__":
    raise SystemExit(main())
