from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal

import pytest

from algotrader.domain.shadow_observations import ShadowRunRecord
from algotrader.infrastructure.scan_archive import ArchiveConflict, ArchiveIntegrityError
from algotrader.infrastructure.shadow_archive import LocalShadowArchive
from tests.test_shadow_discovery import NOW, parent_fixture


def record_fixture():
    return ShadowRunRecord(
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        parent_fixture().scan_id,
        "a" * 64,
        NOW,
        NOW,
        "complete",
        {},
        {},
        parent_fixture().cohort,
        results=({"score": Decimal("82.40")},),
    )


def test_shadow_archive_roundtrip_conflict_and_parent_separation(tmp_path):
    archive = LocalShadowArchive(tmp_path)
    record = record_fixture()
    assert archive.write_run(record).status == "saved"
    assert archive.read_run(record.shadow_id) == record
    archive.write_run(record)
    with pytest.raises(ArchiveConflict):
        archive.write_run(replace(record, status="partial"))
    assert not (tmp_path / "scans").exists()
    with pytest.raises(ValueError):
        archive.read_run("../unsafe")
    with pytest.raises(ValueError):
        archive.write_run(replace(record, schema_version=2))


def test_shadow_archive_checksum_blocks_tampered_payload(tmp_path):
    archive = LocalShadowArchive(tmp_path)
    record = record_fixture()
    archive.write_run(record)
    payload = tmp_path / "shadow" / record.shadow_id / "payload.json"
    payload.write_bytes(b"{}")
    with pytest.raises(ArchiveIntegrityError):
        archive.read_run(record.shadow_id)


def test_shadow_archive_concurrent_writes_and_interrupted_publication(tmp_path, monkeypatch):
    archive = LocalShadowArchive(tmp_path)
    record = record_fixture()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(
            receipt.status == "saved" for receipt in pool.map(archive.write_run, [record, record])
        )
    assert archive.read_run(record.shadow_id) == record

    def failed_rename(*args):
        raise OSError("interrupted")

    monkeypatch.setattr("algotrader.infrastructure.scan_archive.os.rename", failed_rename)
    other = replace(record, shadow_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc")
    with pytest.raises(OSError):
        archive.write_run(other)
    assert not (tmp_path / "shadow" / other.shadow_id).exists()
