"""Transactional opt-in recovery state and immutable observation evidence."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Any

from algotrader.domain.crypto_screener_models import CryptoOpportunity
from algotrader.domain.recovery import RECOVERY_POLICY_VERSION
from algotrader.domain.recovery_episodes import TERMINAL, RecoveryEpisode
from algotrader.domain.scan_observations import decode_value, encode_value


def episode_values(episode: RecoveryEpisode) -> dict[str, Any]:
    return {field.name: getattr(episode, field.name) for field in fields(episode)}


def serialize(values: Any) -> str:
    return json.dumps(encode_value(values), sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate_seed_record(record: dict[str, Any]) -> RecoveryEpisode:
    episode = RecoveryEpisode(**record["state"])
    candidate = record["evidence"]["candidate"]
    if (record["evidence"]["kind"] != "enrollment" or episode.revision != 0
            or episode.status != "waiting" or episode.triggered_at is not None
            or candidate["symbol"] != episode.instrument.id.symbol
            or candidate["setup_label"] != "RECOVERY_RECLAIM_LONG"
            or candidate.get("research_policy_version") != RECOVERY_POLICY_VERSION
            or len(candidate["checklist"]) != 5
            or not all(item["passed"] is True for item in candidate["checklist"])
            or not 0 <= (episode.first_seen - episode.source_at).total_seconds() <= 600):
        raise ValueError("invalid prepared recovery enrollment")
    return episode


class RecoveryEpisodeStore:
    def __init__(self, path: Path, *, capacity: int = 30) -> None:
        if not 1 <= capacity <= 30:
            raise ValueError("recovery pool capacity must be between 1 and 30")
        if path.is_symlink():
            raise ValueError("recovery database symlinks refused")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = path.parent.resolve() / path.name
        if any(Path(str(path) + suffix).is_symlink() for suffix in ("", "-wal", "-shm", "-journal")):
            raise ValueError("recovery database symlinks refused")
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        self.path, self.capacity = path, capacity
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("unsupported recovery database version")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS episodes (
                    episode_id TEXT PRIMARY KEY, instrument_key TEXT NOT NULL,
                    active INTEGER NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_recovery
                    ON episodes(instrument_key) WHERE active = 1;
                CREATE TABLE IF NOT EXISTS observations (
                    sequence INTEGER PRIMARY KEY, episode_id TEXT NOT NULL,
                    payload TEXT NOT NULL, sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS binding (id INTEGER PRIMARY KEY CHECK (id=1), payload TEXT NOT NULL);
                PRAGMA user_version = 1;
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def episodes(self, *, active_only: bool = False) -> tuple[RecoveryEpisode, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM episodes "
                                      + ("WHERE active=1 " if active_only else "")
                                      + "ORDER BY episode_id").fetchall()
        return tuple(RecoveryEpisode(**decode_value(json.loads(row[0]))) for row in rows)

    @staticmethod
    def _record(connection: sqlite3.Connection, episode: RecoveryEpisode, evidence: dict[str, Any]) -> None:
        payload = serialize({"state": episode_values(episode), "evidence": evidence})
        connection.execute("INSERT INTO observations(episode_id,payload,sha256) VALUES(?,?,?)",
                           (episode.episode_id, payload, hashlib.sha256(payload.encode()).hexdigest()))

    def enroll(self, opportunities: Sequence[CryptoOpportunity], at: datetime) -> dict[str, str]:
        records = [{"state": episode_values(RecoveryEpisode.enroll(opportunity, at)),
                    "evidence": {"kind": "enrollment", "candidate": opportunity.as_dict()}}
                   for opportunity in opportunities]
        return self.enroll_records(records)

    def bind(self, binding: dict[str, Any]) -> None:
        payload = serialize(binding)
        with self._connect() as connection:
            previous = connection.execute("SELECT payload FROM binding WHERE id=1").fetchone()
            if previous is not None and previous[0] != payload:
                raise ValueError("recovery database belongs to another study")
            connection.execute("INSERT OR IGNORE INTO binding VALUES(1,?)", (payload,))

    def enroll_records(self, records: Sequence[dict[str, Any]]) -> dict[str, str]:
        prepared = [(record, validate_seed_record(record)) for record in records]
        result = {}
        with self._connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM episodes WHERE active=1").fetchone()[0]
            for record, episode in prepared:
                key = serialize(episode.instrument.id)
                existing = connection.execute(
                    "SELECT episode_id FROM episodes WHERE episode_id=? OR (instrument_key=? AND active=1)",
                    (episode.episode_id, key)).fetchone()
                if existing:
                    result[episode.instrument.id.symbol] = "existing_episode"
                    continue
                if count >= self.capacity:
                    result[episode.instrument.id.symbol] = "pool_capacity"
                    continue
                connection.execute("INSERT INTO episodes VALUES(?,?,?,?,?)",
                                   (episode.episode_id, key, 1, 0, serialize(episode_values(episode))))
                self._record(connection, episode, record["evidence"])
                count += 1
                result[episode.instrument.id.symbol] = "enrolled"
        return result

    def commit(self, previous: RecoveryEpisode, state: RecoveryEpisode, evidence: dict[str, Any]) -> None:
        if state == previous:
            return
        if state.episode_id != previous.episode_id or state.revision != previous.revision + 1:
            raise ValueError("invalid episode revision")
        frozen = ("instrument", "source_at", "first_seen", "qualified_at", "entry", "stop", "target", "policy")
        if any(getattr(previous, name) != getattr(state, name) for name in frozen):
            raise ValueError("recovery episode anchors and bracket are immutable")
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE episodes SET active=?,revision=?,payload=? WHERE episode_id=? AND revision=?",
                (int(state.status not in TERMINAL), state.revision, serialize(episode_values(state)),
                 previous.episode_id, previous.revision)).rowcount
            if changed != 1:
                raise RuntimeError("recovery episode changed concurrently; refresh again")
            self._record(connection, state, evidence)

    def evidence(self) -> tuple[dict[str, Any], ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload,sha256 FROM observations ORDER BY sequence").fetchall()
        result = []
        for payload, digest in rows:
            if hashlib.sha256(payload.encode()).hexdigest() != digest:
                raise ValueError("recovery evidence digest mismatch")
            result.append(decode_value(json.loads(payload)))
        return tuple(result)


def read_recovery_snapshot(path: Path, *, binding: dict[str, Any] | None = None) -> tuple[tuple[RecoveryEpisode, ...], tuple[dict[str, Any], ...]]:
    """Read a consistent existing database without creating or migrating it."""
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        connection.execute("BEGIN")
        if binding is not None:
            row = connection.execute("SELECT payload FROM binding WHERE id=1").fetchone()
            if row is None or row[0] != serialize(binding):
                raise ValueError("recovery snapshot belongs to another study")
        states = connection.execute("SELECT payload FROM episodes ORDER BY episode_id").fetchall()
        # Stream rows so the read-only report does not retain both complete JSON
        # strings and their decoded objects at the same time.
        rows = connection.execute("SELECT payload,sha256 FROM observations ORDER BY sequence")
        episodes = tuple(RecoveryEpisode(**decode_value(json.loads(row[0]))) for row in states)
        evidence = []
        for payload, digest in rows:
            if hashlib.sha256(payload.encode()).hexdigest() != digest:
                raise ValueError("recovery evidence digest mismatch")
            evidence.append(decode_value(json.loads(payload)))
        return episodes, tuple(evidence)
    finally:
        connection.close()
