"""Local immutable directories; publish only complete, checksummed records."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from algotrader.domain.scan_observations import (
    SCHEMA_VERSION,
    ScanReceipt,
    ScanRecord,
    decode_value,
    encode_value,
)


class ArchiveConflict(ValueError):
    pass


class ArchiveIntegrityError(ValueError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(encode_value(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def validated_id(value: str) -> str:
    if str(UUID(value)) != value:
        raise ValueError("archive ID must be a canonical UUID")
    return value


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class LocalScanArchive:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _write(self, target: Path, payload: Any) -> None:
        data = canonical_bytes(payload)
        manifest = json.dumps({"schema_version": SCHEMA_VERSION, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}, sort_keys=True).encode()
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=target.parent))
        try:
            for name, content in (("payload.json", data), ("manifest.json", manifest)):
                with (staging / name).open("xb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            _sync_directory(staging)
            try:
                # An existing complete (nonempty) directory cannot be replaced by rename.
                os.rename(staging, target)
            except OSError as exc:
                if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                    raise
                self._read(target)
                if (target / "payload.json").read_bytes() != data:
                    raise ArchiveConflict("archive ID already contains different content") from exc
            _sync_directory(target.parent)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def _read(self, target: Path) -> Any:
        try:
            manifest = json.loads((target / "manifest.json").read_bytes())
            data = (target / "payload.json").read_bytes()
            if manifest["schema_version"] != SCHEMA_VERSION:
                raise ArchiveIntegrityError("unsupported archive schema")
            if manifest["bytes"] != len(data) or manifest["sha256"] != hashlib.sha256(data).hexdigest():
                raise ArchiveIntegrityError("archive checksum mismatch")
            return decode_value(json.loads(data))
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise ArchiveIntegrityError(f"invalid archive: {target.name}") from exc

    def write_scan(self, record: ScanRecord) -> ScanReceipt:
        validated_id(record.scan_id)
        if record.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported scan schema")
        self._write(self.root / "scans" / record.scan_id, record)
        return ScanReceipt(record.scan_id, "saved")

    def read_scan(self, scan_id: str) -> ScanRecord:
        result = self._read(self.root / "scans" / validated_id(scan_id))
        if not isinstance(result, ScanRecord) or result.scan_id != scan_id or result.schema_version != SCHEMA_VERSION:
            raise ArchiveIntegrityError("invalid scan record")
        return result

    def scan_ids(self) -> tuple[str, ...]:
        directory = self.root / "scans"
        if not directory.exists():
            return ()
        result = []
        for path in directory.iterdir():
            try:
                validated_id(path.name)
            except ValueError:
                continue
            if (path / "manifest.json").is_file():
                result.append(path.name)
        return tuple(sorted(result))

    def _outcome_directory(self, scan_id: str, horizon: int, symbol: str) -> Path:
        validated_id(scan_id)
        if horizon not in (24, 48) or re.fullmatch(r"[A-Z0-9_]{1,80}", symbol) is None:
            raise ValueError("invalid outcome horizon or symbol")
        return self.root / "outcomes" / scan_id / str(horizon) / symbol

    def write_outcome(self, scan_id: str, horizon: int, symbol: str, attempt_id: str, payload: dict[str, Any]) -> None:
        self._write(self._outcome_directory(scan_id, horizon, symbol) / validated_id(attempt_id), payload)

    def outcome_attempts(self, scan_id: str, horizon: int, symbol: str) -> tuple[dict[str, Any], ...]:
        directory = self._outcome_directory(scan_id, horizon, symbol)
        if not directory.exists():
            return ()
        records = []
        for path in directory.iterdir():
            if path.name.startswith(".staging-"):
                continue
            validated_id(path.name)
            payload = self._read(path)
            if not isinstance(payload, dict) or payload.get("attempt_id") != path.name:
                raise ArchiveIntegrityError("invalid outcome attempt")
            records.append(payload)
        return tuple(sorted(records, key=lambda item: (item["observed_at"], item["attempt_id"])))

    def write_summary(self, scan_id: str, horizon: int, summary_id: str, payload: dict[str, Any]) -> None:
        directory = self._outcome_directory(scan_id, horizon, "_SUMMARIES")
        self._write(directory / validated_id(summary_id), payload)
