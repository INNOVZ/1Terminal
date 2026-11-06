import os
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from algotrader.infrastructure.research_sessions import LocalResearchSessions
from algotrader.infrastructure.scan_archive import ArchiveConflict, ArchiveIntegrityError
from tests.test_research_session_policy import ANCHOR, session_fixture


def test_session_roundtrip_events_and_reservations(tmp_path):
    store = LocalResearchSessions(tmp_path)
    session = session_fixture()
    store.write(session)
    assert store.read(session.session_id) == session
    first = store.append(session.session_id, "ranks", "heartbeat", {}, ANCHOR)
    second = store.append(
        session.session_id, "ranks", "heartbeat", {}, ANCHOR - timedelta(seconds=1)
    )
    assert (first.sequence, second.sequence) == (1, 2)
    assert store.events(session.session_id) == (first, second)
    rank = store.reserve_rank(session.session_id, 1, ANCHOR)
    assert store.reserve_rank(session.session_id, 1, ANCHOR + timedelta(seconds=1)) == rank
    assert rank["scheduled_at"] == ANCHOR + timedelta(seconds=300)
    inputs = {"rank_snapshots": (), "considered_outcomes": {}}
    report = store.reserve_report(session.session_id, 24, session.endpoint_24h, inputs)
    assert store.reserve_report(session.session_id, 24, session.endpoint_24h, inputs) == report
    with pytest.raises(ArchiveConflict):
        store.reserve_report(session.session_id, 24, session.endpoint_24h, {"different": True})
    with pytest.raises(ArchiveConflict):
        store.write(replace(session, identity={"different": True}))


def test_role_locks_are_cross_process_exclusive_and_journals_do_not_deadlock(tmp_path):
    store = LocalResearchSessions(tmp_path)
    session = session_fixture()
    store.write(session)
    code = """
from pathlib import Path
import sys
from algotrader.infrastructure.research_sessions import LocalResearchSessions
with LocalResearchSessions(Path(sys.argv[1])).role_lock('ranks'):
    print('owned', flush=True)
    sys.stdin.read()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    try:
        assert child.stdout.readline().strip() == "owned"
        with pytest.raises(RuntimeError), store.role_lock("ranks"):
            pass
        with store.role_lock("outcomes"):
            store.append(session.session_id, "control", "stop_requested", {}, ANCHOR)
            assert len(store.events(session.session_id)) == 1
    finally:
        child.kill()
        child.communicate(timeout=5)
    with store.role_lock("ranks"):
        pass


def test_corruption_sequence_gaps_and_symlinks_fail_closed(tmp_path):
    store = LocalResearchSessions(tmp_path)
    session = session_fixture()
    store.write(session)
    first = store.append(session.session_id, "ranks", "heartbeat", {}, ANCHOR)
    store.append(session.session_id, "ranks", "heartbeat", {}, ANCHOR)
    directory = tmp_path / "sessions" / session.session_id / "events" / "ranks"
    # A renamed sequence cannot silently disappear from the authoritative stream.
    original = next(path for path in directory.iterdir() if first.event_id in path.name)
    original.rename(original.with_name("invalid"))
    with pytest.raises(ArchiveIntegrityError):
        store.events(session.session_id)
    payload = tmp_path / "sessions" / session.session_id / "definition" / "payload.json"
    payload.write_bytes(b"corrupt")
    with pytest.raises(ArchiveIntegrityError):
        store.read(session.session_id)
    other = tmp_path / "sessions" / str(uuid4())
    other.symlink_to(tmp_path / "sessions" / session.session_id, target_is_directory=True)
    with pytest.raises(ArchiveIntegrityError):
        store.read(other.name)


def test_interrupted_publish_does_not_make_partial_definition_readable(tmp_path, monkeypatch):
    store = LocalResearchSessions(tmp_path)
    session = session_fixture()

    def interrupted(*args):
        raise OSError("injected publication failure")

    monkeypatch.setattr("algotrader.infrastructure.scan_archive.os.rename", interrupted)
    with pytest.raises(OSError):
        store.write(session)
    assert store.session_ids() == ()
    with pytest.raises(ArchiveIntegrityError):
        store.read(session.session_id)


def test_interrupted_journal_batch_leaves_only_valid_contiguous_prefix(tmp_path, monkeypatch):
    store = LocalResearchSessions(tmp_path)
    session = session_fixture()
    store.write(session)
    original = store._write
    writes = 0

    def interrupt_second(path, payload):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("injected process failure")
        return original(path, payload)

    monkeypatch.setattr(store, "_write", interrupt_second)
    with pytest.raises(OSError):
        store.append_many(
            session.session_id, "ranks", tuple(("heartbeat", {}, ANCHOR) for _ in range(3))
        )
    assert tuple(event.sequence for event in store.events(session.session_id)) == (1,)
    monkeypatch.setattr(store, "_write", original)
    assert store.append(session.session_id, "ranks", "heartbeat", {}, ANCHOR).sequence == 2
