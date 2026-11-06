"""Checksummed shadow namespace, separate from immutable production scans."""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path
from typing import Any

from algotrader.domain.scan_observations import ScanReceipt
from algotrader.domain.shadow_observations import ShadowRunRecord
from algotrader.infrastructure.scan_archive import (
    ArchiveIntegrityError,
    LocalScanArchive,
    validated_id,
)


class LocalShadowArchive:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._store = LocalScanArchive(root)

    def write_run(self, record: ShadowRunRecord) -> ScanReceipt:
        validated_id(record.shadow_id)
        validated_id(record.parent_id)
        if record.schema_version != 1:
            raise ValueError("unsupported shadow schema")
        payload = {
            "kind": "shadow_run",
            "record": {f.name: getattr(record, f.name) for f in fields(record)},
        }
        self._store._write(self.root / "shadow" / record.shadow_id, payload)
        return ScanReceipt(record.shadow_id, "saved")

    def read_run(self, shadow_id: str) -> ShadowRunRecord:
        payload = self._store._read(self.root / "shadow" / validated_id(shadow_id))
        try:
            if payload["kind"] != "shadow_run":
                raise ValueError("invalid record kind")
            record = ShadowRunRecord(**payload["record"])
            if record.schema_version != 1 or record.shadow_id != shadow_id:
                raise ValueError("invalid shadow identity/schema")
            validated_id(record.parent_id)
            return record
        except (KeyError, TypeError, ValueError) as exc:
            raise ArchiveIntegrityError("invalid shadow record") from exc

    def _outcome_directory(self, shadow_id: str, horizon: int, symbol: str) -> Path:
        validated_id(shadow_id)
        if horizon not in (24, 48) or re.fullmatch(r"[A-Z0-9_]{1,80}", symbol) is None:
            raise ValueError("invalid outcome horizon or symbol")
        return self.root / "shadow-outcomes" / shadow_id / str(horizon) / symbol

    def write_outcome(
        self, shadow_id: str, horizon: int, symbol: str, attempt_id: str, payload: dict[str, Any]
    ) -> None:
        self._store._write(
            self._outcome_directory(shadow_id, horizon, symbol) / validated_id(attempt_id), payload
        )

    def outcome_attempts(
        self, shadow_id: str, horizon: int, symbol: str
    ) -> tuple[dict[str, Any], ...]:
        directory = self._outcome_directory(shadow_id, horizon, symbol)
        if not directory.exists():
            return ()
        records = []
        for path in directory.iterdir():
            if path.name.startswith(".staging-"):
                continue
            validated_id(path.name)
            payload = self._store._read(path)
            if not isinstance(payload, dict) or payload.get("attempt_id") != path.name:
                raise ArchiveIntegrityError("invalid shadow outcome attempt")
            records.append(payload)
        return tuple(sorted(records, key=lambda item: (item["observed_at"], item["attempt_id"])))
