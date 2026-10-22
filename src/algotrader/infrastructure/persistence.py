from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def atomic_write_json(file_path: Path | str, payload: Any, indent: int = 2) -> None:
    """Atomically persists JSON payload using a tmp file, fsync, and atomic rename.

    Prevents partial reads by dashboard workers and eliminates race conditions.
    """
    target = Path(file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f"{target.name}.tmp")

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=indent, default=str)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp_path, target)
