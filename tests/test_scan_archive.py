from __future__ import annotations

import hashlib
import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from algotrader.adapters.external.models import MacroSentimentContext
from algotrader.domain.models import Candle
from algotrader.domain.scan_observations import RecordedCall, ScanRecord
from algotrader.infrastructure.scan_archive import (
    ArchiveConflict,
    ArchiveIntegrityError,
    LocalScanArchive,
)

SCAN_ID = "6158a217-8f7c-4888-8f92-3cb3fa656ef7"
NOW = datetime(2026, 10, 24, tzinfo=UTC)


def record(scan_id: str = SCAN_ID) -> ScanRecord:
    candle = Candle(NOW, NOW, *(Decimal("0.000000012345678901") for _ in range(5)))
    return ScanRecord(
        scan_id=scan_id, started_at=NOW, completed_at=NOW, status="complete",
        identity={"policy": "fixture"}, request={"min_score": Decimal("68")},
        inputs=(RecordedCall("candles", 0, {"symbol": "TESTUSDT"}, (candle,)),
                RecordedCall("macro", 0, {}, MacroSentimentContext(observed_at=NOW))),
        decisions=(), opportunities=(), counts={"returned": 0}, cohort=(),
    )


def _write_process(root: str, scan_id: str, score: str) -> str:
    try:
        LocalScanArchive(Path(root)).write_scan(replace(record(scan_id), request={"score": score}))
        return "saved"
    except ArchiveConflict:
        return "conflict"


def test_archive_round_trip_preserves_precision_missing_macro_and_checksum(tmp_path: Path) -> None:
    archive = LocalScanArchive(tmp_path)
    receipt = archive.write_scan(record())
    restored = archive.read_scan(SCAN_ID)
    assert receipt.status == "saved"
    assert restored == record()
    assert restored.inputs[0].result[0].open == Decimal("0.000000012345678901")
    assert restored.inputs[1].result.fear_greed is None
    directory = tmp_path / "scans" / SCAN_ID
    manifest = json.loads((directory / "manifest.json").read_text())
    payload = (directory / "payload.json").read_bytes()
    assert manifest["sha256"] == hashlib.sha256(payload).hexdigest()
    assert manifest["bytes"] == len(payload)


def test_same_id_is_idempotent_but_changed_payload_cannot_overwrite(tmp_path: Path) -> None:
    archive = LocalScanArchive(tmp_path)
    archive.write_scan(record())
    before = (tmp_path / "scans" / SCAN_ID / "payload.json").stat().st_mtime_ns
    archive.write_scan(record())
    assert (tmp_path / "scans" / SCAN_ID / "payload.json").stat().st_mtime_ns == before
    with pytest.raises(ArchiveConflict):
        archive.write_scan(replace(record(), status="failed"))
    assert archive.read_scan(SCAN_ID).status == "complete"


@pytest.mark.parametrize("scan_id", ["../escape", "/tmp/escape", "not-a-uuid"])
def test_archive_rejects_unsafe_ids(tmp_path: Path, scan_id: str) -> None:
    with pytest.raises(ValueError):
        LocalScanArchive(tmp_path).write_scan(record(scan_id))
    assert not (tmp_path / "scans").exists()


def test_corruption_is_rejected_before_deserialization(tmp_path: Path) -> None:
    archive = LocalScanArchive(tmp_path)
    archive.write_scan(record())
    (tmp_path / "scans" / SCAN_ID / "payload.json").write_text('{}')
    with pytest.raises(ArchiveIntegrityError):
        archive.read_scan(SCAN_ID)


def test_interrupted_publish_is_never_visible_as_complete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = LocalScanArchive(tmp_path)
    with monkeypatch.context() as patch:
        def fail(*args: object) -> None:
            raise OSError("interrupted before publish")
        patch.setattr("algotrader.infrastructure.scan_archive.os.rename", fail)
        with pytest.raises(OSError):
            archive.write_scan(record())
    assert archive.scan_ids() == ()
    archive.write_scan(record())
    assert archive.scan_ids() == (SCAN_ID,)


def test_concurrent_processes_cannot_replace_complete_archives(tmp_path: Path) -> None:
    jobs = [(str(tmp_path), SCAN_ID, str(i % 2)) for i in range(4)]
    jobs += [(str(tmp_path), str(UUID(int=i)), "same") for i in range(1, 3)]
    with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context("spawn")) as pool:
        results = list(pool.map(_write_process, *(zip(*jobs, strict=True))))
    assert "conflict" in results[:4]
    assert results[4:] == ["saved", "saved"]
    archive = LocalScanArchive(tmp_path)
    assert len(archive.scan_ids()) == 3
    assert archive.read_scan(SCAN_ID).request["score"] in {"0", "1"}


def test_streaming_canonical_archive_matches_reference_encoding_with_bounded_memory(tmp_path: Path) -> None:
    import tracemalloc

    from algotrader.domain.scan_observations import encode_value
    from algotrader.infrastructure.scan_archive import canonical_bytes

    candles = tuple(
        Candle(NOW, NOW, *(Decimal(f"{i}.12345678") for _ in range(5)))
        for i in range(2500)
    )
    large_record = replace(
        record(),
        inputs=(RecordedCall("candles", 0, {"symbol": "TESTUSDT"}, candles),),
    )
    reference_bytes = json.dumps(
        encode_value(large_record), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")

    assert canonical_bytes(large_record) == reference_bytes

    archive = LocalScanArchive(tmp_path)
    tracemalloc.start()
    try:
        archive.write_scan(large_record)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    written_bytes = (tmp_path / "scans" / SCAN_ID / "payload.json").read_bytes()
    assert written_bytes == reference_bytes
    assert peak_bytes < 2 * 1024 * 1024


def test_prune_scans_enforces_age_count_and_byte_caps(tmp_path: Path) -> None:
    import os
    from datetime import timedelta

    archive = LocalScanArchive(tmp_path)
    base_now = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    ids = [str(UUID(int=i)) for i in range(1, 7)]
    # Write 6 scans with staggered timestamps: oldest is 6 days old, others are recent
    ages_hours = [144, 20, 16, 12, 8, 1]
    for sid, age_h in zip(ids, ages_hours, strict=True):
        archive.write_scan(record(sid))
        ts = (base_now - timedelta(hours=age_h)).timestamp()
        os.utime(tmp_path / "scans" / sid / "manifest.json", (ts, ts))

    # Max age 5 days + max 3 scans should prune oldest (6d old) + next 2 oldest, keeping latest 3
    pruned = archive.prune_scans(
        max_age_days=5,
        max_scans=3,
        max_bytes=100_000_000,
        now=base_now,
        preserve_scan_ids=frozenset({ids[-1]}),
    )
    assert set(pruned) == {ids[0], ids[1], ids[2]}
    assert set(archive.scan_ids()) == {ids[3], ids[4], ids[5]}

    # Tight byte cap prunes down to only the preserved newest scan
    pruned_bytes = archive.prune_scans(
        max_age_days=5,
        max_scans=15,
        max_bytes=1,
        now=base_now,
        preserve_scan_ids=frozenset({ids[-1]}),
    )
    assert set(pruned_bytes) == {ids[3], ids[4]}
    assert archive.scan_ids() == (ids[5],)
