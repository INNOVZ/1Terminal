"""Explicit Docker smoke check; synthetic evidence only, no exchange network."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from algotrader.application.research_sessions import ResearchSessions
from algotrader.infrastructure.rank_archive import digest
from algotrader.infrastructure.scan_archive import LocalScanArchive
from algotrader.infrastructure.shadow_archive import LocalShadowArchive
from tests.test_shadow_archive import record_fixture
from tests.test_shadow_discovery import parent_fixture


def docker(*args: str) -> str:
    return subprocess.run(
        ["docker", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def fixtures(root: Path) -> tuple[str, str]:
    now = datetime.now(UTC)
    parent = replace(
        parent_fixture(2),
        started_at=now - timedelta(minutes=3),
        completed_at=now - timedelta(minutes=2),
        opportunities=({"symbol": "T1USDT", "direction": "short"},),
    )
    LocalScanArchive(root).write_scan(parent)
    child = replace(
        record_fixture(),
        shadow_id=str(uuid4()),
        cohort=parent.cohort,
        parent_checksum=digest(parent),
        started_at=now - timedelta(minutes=1),
        completed_at=now - timedelta(seconds=15),
        results=({"symbol": "T0USDT", "qualified": False, "direction": "long"},),
    )
    LocalShadowArchive(root).write_run(child)
    sessions = ResearchSessions(root)
    stopped = sessions.start(child.shadow_id, now=now)
    sessions.stop(stopped.session_id, now=now)
    child = replace(child, shadow_id=str(uuid4()))
    LocalShadowArchive(root).write_run(child)
    active = sessions.start(child.shadow_id, now=now)
    return stopped.session_id, active.session_id


def wait_for(name: str, command: list[str]) -> dict:
    deadline = time.monotonic() + 45
    last_error = ""
    while time.monotonic() < deadline:
        try:
            return json.loads(docker("exec", name, *command))
        except (subprocess.CalledProcessError, ValueError) as exc:
            last_error = str(exc)
            time.sleep(1)
    raise AssertionError(f"container did not become ready: {last_error}; {docker('logs', name)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    prefix = "atlas-research-smoke-" + uuid4().hex
    volume = prefix + "-archive"
    created: list[str] = []
    docker("volume", "create", volume)
    try:
        with tempfile.TemporaryDirectory(prefix="atlas-research-smoke-") as directory:
            root = Path(directory)
            stopped, active = fixtures(root)
            staging = prefix + "-staging"
            docker(
                "create",
                "--name",
                staging,
                "--network",
                "none",
                "--user",
                "0:0",
                "--mount",
                f"type=volume,source={volume},target=/app/data/research",
                args.image,
                "sleep",
                "120",
            )
            created.append(staging)
            docker("cp", str(root) + "/.", staging + ":/app/data/research")
            docker("start", staging)
            docker("exec", staging, "chown", "-R", "10001:10001", "/app/data/research")
            common = [
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--user",
                "10001:10001",
                "--memory",
                "256m",
                "--cpus",
                "0.5",
                "--tmpfs",
                "/tmp:size=64m,noexec,nosuid",
            ]
            api = prefix + "-api"
            docker(
                "run",
                "-d",
                "--name",
                api,
                *common,
                "--tmpfs",
                "/app/state:uid=10001,gid=10001,mode=0700",
                "--tmpfs",
                "/app/data:uid=10001,gid=10001,mode=0700",
                "-e",
                "ATLAS_SCREENER_CRON_ENABLED=false",
                "-e",
                "ATLAS_STATE_DB_PATH=/app/state/watchlist.sqlite3",
                args.image,
            )
            created.append(api)
            api_result = wait_for(
                api,
                [
                    "python",
                    "-c",
                    "import json,urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz',timeout=2); print(urllib.request.urlopen('http://127.0.0.1:8000/').read().decode())",
                ],
            )
            assert (
                api_result["mode"] == "screener" and api_result["order_execution_enabled"] is False
            )
            for role, identifier in (("ranks", stopped), ("outcomes", active)):
                name = prefix + "-" + role
                docker(
                    "run",
                    "-d",
                    "--name",
                    name,
                    *common,
                    "--mount",
                    f"type=volume,source={volume},target=/app/data/research",
                    args.image,
                    "python",
                    "-m",
                    "algotrader.cli",
                    "research-worker",
                    "--role",
                    role,
                    "--session-id",
                    identifier,
                    "--archive-dir",
                    "/app/data/research",
                )
                created.append(name)
                health_command = [
                    "python",
                    "-m",
                    "algotrader.cli",
                    "research-health",
                    "--role",
                    role,
                    "--session-id",
                    identifier,
                    "--archive-dir",
                    "/app/data/research",
                ]
                result = wait_for(name, health_command)
                assert result["healthy"] and not result["collecting"]
                if role == "ranks":
                    assert result["state"] == "stopped"
                    docker("stop", "--time", "15", name)
                    docker("start", name)
                    assert wait_for(name, health_command)["state"] == "stopped"
                configuration = json.loads(docker("inspect", name))[0]
                assert configuration["HostConfig"]["NetworkMode"] == "none"
                assert not configuration["HostConfig"].get("PortBindings")
                assert configuration["HostConfig"]["ReadonlyRootfs"]
                assert configuration["HostConfig"]["Memory"] == 256 * 1024**2
            print(
                json.dumps(
                    {
                        "api": "ready",
                        "stopped_role": "idle_after_restart",
                        "pending_role": "idle",
                        "exchange_network": "disabled",
                        "synthetic_data_only": True,
                    }
                )
            )
    finally:
        for name in reversed(created):
            subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
        subprocess.run(["docker", "volume", "rm", volume], check=False, capture_output=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
