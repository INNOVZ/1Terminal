from __future__ import annotations

import json

import pytest

from scripts import research_evidence_stream as stream
from scripts.replay_contract import ReplayUnavailable, digest
from scripts.research_pipeline_comparison import AuditedTape, compare_bundle
from scripts.research_verified_replay import offline, write_report
from tests.test_capture_evidence import bundle_fixture


async def test_stream_preserves_exact_bundle_and_real_adapter_comparison(tmp_path):
    bundle = await bundle_fixture()
    path = tmp_path / "evidence.jsonl"
    stream.write_bundle(path, bundle)
    restored = stream.read_bundle(path)
    assert digest(restored) == digest(bundle)
    with offline():
        result = await compare_bundle(restored)
    assert result["status"] == "complete"
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        stream.write_bundle(path, bundle)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".atlas-evidence-*"))


@pytest.mark.parametrize(
    "change", ["truncate", "payload", "footer", "trailing", "header", "section", "missing_control"]
)
async def test_stream_refuses_uncommitted_or_tampered_evidence(tmp_path, change):
    bundle = await bundle_fixture()
    path = tmp_path / "evidence.jsonl"
    stream.write_bundle(path, bundle)
    rows = path.read_bytes().splitlines(keepends=True)
    if change == "truncate":
        rows.pop()
    elif change == "payload":
        row = json.loads(rows[0])
        row["value"]["items"]["status"] = "changed"
        rows[0] = (json.dumps(row, separators=(",", ":")) + "\n").encode()
    elif change == "footer":
        row = json.loads(rows[-1])
        row["sha256"] = "0" * 64
        rows[-1] = (json.dumps(row) + "\n").encode()
    elif change == "trailing":
        rows.append(b"\n")
    elif change == "header":
        rows.insert(2, rows[0])
    elif change == "section":
        row = json.loads(rows[2])
        row["kind"] = "unknown"
        rows[2] = (json.dumps(row) + "\n").encode()
    else:
        rows.pop(1)
    path.write_bytes(b"".join(rows))
    with pytest.raises((ReplayUnavailable, ValueError)):
        stream.read_bundle(path)


async def test_stream_limits_fail_atomically_and_legacy_limit_is_preserved(tmp_path, monkeypatch):
    bundle = await bundle_fixture()
    path = tmp_path / "bounded.jsonl"
    monkeypatch.setattr(stream, "MAX_FILE", 100)
    with pytest.raises(ReplayUnavailable, match="limit"):
        stream.write_bundle(path, bundle)
    assert not path.exists() and not list(tmp_path.glob(".atlas-evidence-*"))
    monkeypatch.setattr(stream, "MAX_FILE", 512 * 1024 * 1024)
    stream.write_bundle(path, bundle)
    monkeypatch.setattr(stream, "MAX_LINE", 10)
    with pytest.raises(ReplayUnavailable):
        stream.read_bundle(path)


async def test_deferred_tape_copies_only_requested_results_and_retains_archive():
    bundle = await bundle_fixture()
    calls = bundle["record"].inputs
    before = digest(calls)
    tape = AuditedTape(calls)
    value = tape.take("instruments", {})
    with pytest.raises(TypeError):
        value.clear()
    assert digest(calls) == before
    assert sum(len(group) for group in tape.calls.values()) == len(calls) - 1


async def test_legacy_json_and_stream_have_identical_semantic_digest(tmp_path):
    from algotrader.domain.scan_observations import encode_value

    bundle = await bundle_fixture()
    legacy = tmp_path / "legacy.json"
    write_report(legacy, encode_value(bundle))
    streamed = tmp_path / "stream.jsonl"
    stream.write_bundle(streamed, stream.read_bundle(legacy))
    assert digest(stream.read_bundle(legacy)) == digest(stream.read_bundle(streamed))
    assert streamed.stat().st_size < legacy.stat().st_size


async def test_streamed_semantic_hash_matches_existing_archive_encoding():
    from datetime import UTC, datetime
    from decimal import Decimal

    from algotrader.domain.models import Direction

    bundle = await bundle_fixture()
    for value in [
        bundle,
        bundle["record"],
        None,
        {
            "unicode": "é😀",
            "values": (Decimal("1.00"), datetime.now(UTC), Direction.LONG, True, 1, 1.25),
        },
        [],
        {},
    ]:
        assert stream.semantic_digest(value) == digest(value)
    with pytest.raises(ValueError):
        stream.semantic_digest(float("nan"))
