import contextlib
import sqlite3

import pytest

from algotrader.application.watchlist_service import WatchlistService


@pytest.mark.parametrize("message", ["database is locked", "disk I/O error"])
def test_failed_watchlist_open_preserves_database_sidecars(tmp_path, monkeypatch, message):
    database = tmp_path / "state.sqlite3"
    shm = tmp_path / "state.sqlite3-shm"
    wal = tmp_path / "state.sqlite3-wal"
    shm.write_bytes(b"existing shared memory")
    wal.write_bytes(b"committed WAL evidence")

    @contextlib.contextmanager
    def unavailable(self):
        raise sqlite3.OperationalError(message)
        yield

    monkeypatch.setattr(WatchlistService, "_get_connection", unavailable)
    with pytest.raises(sqlite3.OperationalError, match=message):
        WatchlistService(database)
    assert shm.read_bytes() == b"existing shared memory"
    assert wal.read_bytes() == b"committed WAL evidence"
