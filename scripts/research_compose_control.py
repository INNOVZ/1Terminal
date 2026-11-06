"""Research-only deployment controls. Never source operator data as shell code."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from uuid import UUID

CONTROL_FILE = Path("/home/deploy/alfa-terminal/research-control.env")
ARCHIVE_DIR = "/home/deploy/alfa-terminal/data/research"
SERVICES = ["research-ranks", "research-outcomes"]


def _validate(control: dict[str, str]) -> None:
    if set(control) - {"ATLAS_RESEARCH_ENABLED", "ATLAS_RESEARCH_SESSION_ID"}:
        raise ValueError("unknown research control key")
    if control.get("ATLAS_RESEARCH_ENABLED") not in ("true", "false"):
        raise ValueError("research enabled must be true or false")
    identifier = control.get("ATLAS_RESEARCH_SESSION_ID")
    if identifier is not None and str(UUID(identifier)) != identifier:
        raise ValueError("research session must be a canonical UUID")
    if control["ATLAS_RESEARCH_ENABLED"] == "true" and identifier is None:
        raise ValueError("enabled research requires an explicit session")


def parse_research_control(path: Path) -> dict[str, str]:
    if path.is_symlink():
        raise ValueError("research control must not be a symlink")
    if not path.exists():
        return {"ATLAS_RESEARCH_ENABLED": "false"}
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 4096 or info.st_mode & 0o022:
        raise ValueError("research control must be a small non-group/world-writable file")
    control = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key in control:
            raise ValueError("malformed or duplicate research control")
        control[key] = value
    _validate(control)
    return control


def compose_argv(release: Path, control: dict[str, str], action: list[str]) -> list[str]:
    _validate(control)
    if not release.is_absolute() or re.fullmatch(r"[0-9a-f]{40}", release.name) is None:
        raise ValueError("release must be an absolute full-SHA directory")
    if action not in (["build"], ["up"], ["health"], ["stop"]):
        raise ValueError("unsupported research deployment action")
    base = [
        "docker",
        "--context",
        "default",
        "compose",
        "--project-name",
        "atlas-research",
        "--file",
        str(release / "compose.yaml"),
        "--profile",
        "research",
    ]
    operation = action[0]
    if operation == "up" and control["ATLAS_RESEARCH_ENABLED"] == "false":
        operation = "stop"
    commands = {
        "build": ["build"],
        "up": ["up", "-d", "--wait", "--wait-timeout", "300", "--remove-orphans"],
        "health": ["ps", "--all", "--format", "json"],
        "stop": ["stop"],
    }
    return base + commands[operation] + SERVICES


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy only the isolated research worker project")
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--action", required=True, choices=["build", "up", "health", "stop"])
    args = parser.parse_args()
    try:
        control = parse_research_control(CONTROL_FILE)
        release = args.release.resolve()
        if not release.is_dir():
            raise ValueError("release directory is missing")
        argv = compose_argv(release, control, [args.action])
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("ATLAS_")
            and key
            not in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")
        }
        env.update(control)
        env["ATLAS_IMAGE_TAG"] = release.name
        env["ATLAS_RESEARCH_ARCHIVE_DIR"] = ARCHIVE_DIR
        if args.action != "health":
            return subprocess.run(argv, env=env, check=False).returncode
        result = subprocess.run(argv, env=env, check=False, capture_output=True, text=True)
        if result.returncode:
            print(json.dumps({"healthy": False, "reason": "docker_query_failed"}))
            return 1
        raw = result.stdout.strip()
        try:
            records = json.loads(raw or "[]")
        except json.JSONDecodeError:
            records = [json.loads(line) for line in raw.splitlines()]
        records = records if isinstance(records, list) else [records]
        relevant = [row for row in records if row.get("Service") in SERVICES]
        if control["ATLAS_RESEARCH_ENABLED"] == "false":
            healthy = not any(row.get("State") == "running" for row in relevant)
        else:
            healthy = (
                len(relevant) == 2
                and {row["Service"] for row in relevant} == set(SERVICES)
                and all(
                    row.get("State") == "running" and row.get("Health") == "healthy"
                    for row in relevant
                )
            )
        print(
            json.dumps(
                {
                    "healthy": healthy,
                    "enabled": control["ATLAS_RESEARCH_ENABLED"] == "true",
                    "services": relevant,
                }
            )
        )
        return 0 if healthy else 1
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"status": "failed", "reason": "research_deployment_preflight_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
