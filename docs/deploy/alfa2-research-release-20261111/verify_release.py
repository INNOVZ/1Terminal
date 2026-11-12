"""Verify the transferred release before loading it, then verify the loaded image."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--loaded-image", action="store_true")
    args = parser.parse_args()
    root = args.directory.resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    required = {"DEPLOYMENT.md", "compose.research-study.yaml", "discovery-policy.json",
                "preregistered-policy.json", "verify_release.py", "image.tar"}
    if (set(manifest["files"]) != required or manifest["collection_enabled"] is not False
            or manifest["enrolled"] is not False):
        raise ValueError("release file set or disabled default mismatch")
    for name, expected in manifest["files"].items():
        if Path(name).name != name:
            raise ValueError("release filenames must be basenames")
        path = args.archive if name == "image.tar" and args.archive else root / name
        if path.stat().st_size != expected["bytes"] or sha256(path) != expected["sha256"]:
            raise ValueError(f"release file mismatch: {name}")
    if args.loaded_image:
        image = manifest["image_id"]
        inspected = json.loads(subprocess.check_output(["docker", "image", "inspect", image]))[0]
        config = inspected["Config"]
        if (inspected["Id"] != image or inspected["Os"] != "linux"
                or inspected["Architecture"] != "amd64" or config["User"] not in ("atlas", "10001")
                or "ATLAS_RESEARCH_ENABLED=false" not in config["Env"]):
            raise ValueError("loaded image configuration mismatch")
        result = subprocess.check_output([
            "docker", "run", "--rm", "--platform", "linux/amd64", "--network", "none",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", "256m", "--memory-swap", "256m", "--cpus", "0.25", "--pids-limit", "64", image,
            "python", "-c",
            "import json; from scripts.research_prospective import identity; "
            "from scripts.research_discovery import POLICY; "
            "print(json.dumps(dict(identity=identity(),discovery=POLICY)))",
        ], timeout=90)
        loaded = json.loads(result)
        if (loaded["identity"] != manifest["runtime_identity"]
                or loaded["discovery"] != json.loads((root / "discovery-policy.json").read_text())
                or loaded["identity"]["policy"] != json.loads((root / "preregistered-policy.json").read_text())):
            raise ValueError("loaded image source/runtime identity mismatch")
    print(json.dumps({"status": "verified", "image_id": manifest["image_id"],
                      "loaded_image_verified": args.loaded_image,
                      "collection_enabled": False}))


if __name__ == "__main__":
    main()
