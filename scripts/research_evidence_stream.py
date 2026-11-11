"""Bounded, committed JSONL envelope for v2 evidence; preserves its semantic contract."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from algotrader.domain.scan_observations import ScanRecord, decode_value, encode_value
from scripts.replay_contract import ReplayUnavailable
from scripts.research_verified_replay import read_json

FORMAT = "atlas-evidence-jsonl-v1"
MAX_LINE = 16 * 1024 * 1024
MAX_FILE = 512 * 1024 * 1024
MAX_RECORDS = 16384
SECTIONS = ("inputs", "supplemental", "frames", "receipts")
RESERVED = {"record", "supplemental", "frames", "receipts"}


def _json(line: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ReplayUnavailable("duplicate_stream_json_key")
            result[key] = value
        return result

    def invalid(value):
        raise ReplayUnavailable("nonfinite_stream_json")

    return json.loads(line, object_pairs_hook=pairs, parse_constant=invalid)


def write_bundle(path: Path, bundle: dict[str, Any]) -> None:
    """Encode one call/receipt at a time; never build a second whole-bundle JSON tree."""
    count = total = 0
    checksum = hashlib.sha256()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".atlas-evidence-", delete=False
        ) as handle:
            temporary = Path(handle.name)

            def emit(row, *, committed=True):
                nonlocal count, total
                line = (json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n").encode()
                total += len(line)
                if len(line) > MAX_LINE or total > MAX_FILE or count >= MAX_RECORDS:
                    raise ReplayUnavailable("evidence_stream_limit_exceeded")
                handle.write(line)
                if committed:
                    checksum.update(line)
                    count += 1

            metadata = {k: v for k, v in bundle.items() if k not in RESERVED}
            emit({"format": FORMAT, "kind": "header", "value": encode_value(metadata)})
            record = bundle.get("record")
            emit(
                {
                    "kind": "control",
                    "value": encode_value(
                        replace(record, inputs=()) if record is not None else None
                    ),
                }
            )
            groups = (
                record.inputs if record is not None else (),
                bundle["supplemental"],
                bundle["frames"],
                bundle["receipts"],
            )
            for kind, items in zip(SECTIONS, groups, strict=True):
                for value in items:
                    emit({"kind": kind, "value": encode_value(value)})
            emit(
                {"kind": "commit", "records": count, "sha256": checksum.hexdigest()},
                committed=False,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_bundle(path: Path) -> dict[str, Any]:
    """Bound each line and total size; reject truncation, duplicates and trailing bytes."""
    with path.open("rb") as handle:
        prefix = handle.read(80)
    if not prefix.startswith(b'{"format":"atlas-evidence-jsonl-v1",'):
        # Existing JSON evidence remains supported with its original 128 MiB admission rule.
        return decode_value(read_json(path))
    if path.stat().st_size > MAX_FILE:
        raise ReplayUnavailable("evidence_stream_limit_exceeded")
    checksum = hashlib.sha256()
    groups = {kind: [] for kind in SECTIONS}
    metadata = control = None
    seen_header = seen_control = False
    count = total = 0
    last_section = -1
    with path.open("rb") as handle:
        while True:
            line = handle.readline(MAX_LINE + 1)
            if not line or len(line) > MAX_LINE or not line.endswith(b"\n"):
                raise ReplayUnavailable("truncated_or_oversized_evidence_stream")
            total += len(line)
            if total > MAX_FILE or count >= MAX_RECORDS:
                raise ReplayUnavailable("evidence_stream_limit_exceeded")
            row = _json(line)
            if not isinstance(row, dict):
                raise ReplayUnavailable("invalid_evidence_stream_row")
            kind = row.get("kind")
            if kind == "commit":
                if (
                    not seen_header
                    or not seen_control
                    or row.get("records") != count
                    or row.get("sha256") != checksum.hexdigest()
                    or handle.read(1)
                ):
                    raise ReplayUnavailable("evidence_stream_commit_mismatch")
                break
            if kind == "header":
                if count != 0 or row.get("format") != FORMAT:
                    raise ReplayUnavailable("duplicate_or_misplaced_stream_header")
                metadata = decode_value(row["value"])
                if not isinstance(metadata, dict) or RESERVED.intersection(metadata):
                    raise ReplayUnavailable("invalid_stream_metadata")
                seen_header = True
            elif kind == "control":
                if count != 1 or not seen_header:
                    raise ReplayUnavailable("duplicate_or_misplaced_stream_control")
                control = decode_value(row["value"])
                if control is not None and (not isinstance(control, ScanRecord) or control.inputs):
                    raise ReplayUnavailable("invalid_stream_control")
                seen_control = True
            elif kind in groups:
                section = SECTIONS.index(kind)
                if not seen_control or section < last_section:
                    raise ReplayUnavailable("misordered_stream_section")
                last_section = section
                groups[kind].append(decode_value(row["value"]))
            else:
                raise ReplayUnavailable("unknown_evidence_stream_section")
            checksum.update(line)
            count += 1
    if control is None and groups["inputs"]:
        raise ReplayUnavailable("stream_inputs_without_control")
    return {
        **metadata,
        "record": replace(control, inputs=tuple(groups["inputs"])) if control is not None else None,
        "supplemental": tuple(groups["supplemental"]),
        "frames": groups["frames"],
        "receipts": groups["receipts"],
    }


def semantic_digest(value: Any) -> str:
    """Hash the existing canonical archive bytes without materializing the whole encoding."""
    from dataclasses import fields, is_dataclass

    from algotrader.domain.scan_observations import _TYPES

    def scalar(item):
        return json.dumps(item, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

    def mapping(items):
        yield b"{"
        for index, (key, item) in enumerate(items):
            if index:
                yield b","
            yield scalar(key)
            yield b":"
            yield from chunks(item)
        yield b"}"

    def sequence(items):
        yield b"["
        for index, item in enumerate(items):
            if index:
                yield b","
            yield from chunks(item)
        yield b"]"

    def chunks(item):
        if is_dataclass(item) and not isinstance(item, type):
            if type(item).__name__ not in _TYPES:
                raise ValueError("unsupported archive record type")
            yield b'{"@":'
            yield scalar(type(item).__name__)
            yield b',"fields":'
            yield from mapping(
                (f.name, getattr(item, f.name)) for f in sorted(fields(item), key=lambda f: f.name)
            )
            yield b"}"
        elif isinstance(item, tuple):
            yield b'{"@":"tuple","items":'
            yield from sequence(item)
            yield b"}"
        elif isinstance(item, list):
            yield from sequence(item)
        elif isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("archive mapping keys must be strings")
            yield b'{"@":"mapping","items":'
            yield from mapping((key, item[key]) for key in sorted(item))
            yield b"}"
        else:
            yield scalar(encode_value(item))

    checksum = hashlib.sha256()
    for chunk in chunks(value):
        checksum.update(chunk)
    return checksum.hexdigest()
