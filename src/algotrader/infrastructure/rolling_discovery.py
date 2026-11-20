"""Bounded compressed rolling state and atomic episode admission evidence."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import zlib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from algotrader.domain.models import Candle
from algotrader.domain.scan_observations import decode_value
from algotrader.infrastructure.recovery_episodes import RecoveryEpisodeStore, serialize

MAX_FRAME_BYTES = 16 * 1024**2


def summary_text(summary: dict[str, Any]) -> str:
    digest = hashlib.sha256(serialize(summary).encode()).hexdigest()
    return serialize({"record": summary, "sha256": digest})


def decode_summary(text: str) -> dict[str, Any]:
    envelope = decode_value(json.loads(text))
    record = envelope["record"]
    if hashlib.sha256(serialize(record).encode()).hexdigest() != envelope["sha256"]:
        raise ValueError("rolling summary digest mismatch")
    return cast(dict[str, Any], record)


def pack(value: Any) -> tuple[bytes, str]:
    raw = serialize(value).encode()
    if len(raw) > MAX_FRAME_BYTES:
        raise ValueError("rolling discovery frame exceeds memory bound")
    return zlib.compress(raw), hashlib.sha256(raw).hexdigest()


def unpack(payload: bytes, digest: str) -> Any:
    decoder = zlib.decompressobj()
    raw = decoder.decompress(payload, MAX_FRAME_BYTES + 1)
    if (len(raw) > MAX_FRAME_BYTES or not decoder.eof or decoder.unused_data
            or hashlib.sha256(raw).hexdigest() != digest):
        raise ValueError("invalid rolling discovery frame")
    return decode_value(json.loads(raw))


class RollingDiscoveryStore:
    def __init__(self, episodes: RecoveryEpisodeStore) -> None:
        self.episodes = episodes
        with episodes._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS discovery_cache (
                    symbol TEXT NOT NULL, interval TEXT NOT NULL, history_limit INTEGER NOT NULL,
                    observed_at TEXT NOT NULL, payload BLOB NOT NULL, sha256 TEXT NOT NULL,
                    PRIMARY KEY(symbol,interval)
                );
                CREATE TABLE IF NOT EXISTS discovery_scouts (
                    symbol TEXT PRIMARY KEY, observed_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discovery_signals (signal_key TEXT PRIMARY KEY, episode_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS discovery_frames (
                    decision_id TEXT PRIMARY KEY, issued_at TEXT NOT NULL, payload BLOB NOT NULL,
                    sha256 TEXT NOT NULL, summary TEXT NOT NULL
                );
            """)

    def cache(self, symbol: str, interval: str) -> tuple[tuple[Candle, ...], int]:
        with self.episodes._connect() as connection:
            row = connection.execute("SELECT payload,sha256,history_limit FROM discovery_cache WHERE symbol=? AND interval=?",
                                     (symbol, interval)).fetchone()
        if row is None:
            return (), 0
        return tuple(unpack(row[0], row[1])), row[2]

    def write_cache(self, symbol: str, interval: str, candles: Sequence[Candle], limit: int, at: datetime) -> None:
        payload, digest = pack(tuple(candles))
        with self.episodes._connect() as connection:
            connection.execute("INSERT OR REPLACE INTO discovery_cache VALUES(?,?,?,?,?,?)",
                               (symbol, interval, limit, at.isoformat(), payload, digest))
            # Keep deep histories for at most 64 instruments; shallow scouting retains
            # one small window per member of the preregistered cohort.
            deep = connection.execute("SELECT symbol,MAX(observed_at) FROM discovery_cache WHERE interval!='15m' GROUP BY symbol ORDER BY MAX(observed_at) DESC,symbol").fetchall()
            for old, _ in deep[64:]:
                connection.execute("DELETE FROM discovery_cache WHERE symbol=? AND interval!='15m'", (old,))

    def scouts(self) -> dict[str, dict[str, Any]]:
        with self.episodes._connect() as connection:
            rows = connection.execute("SELECT symbol,payload FROM discovery_scouts").fetchall()
        return {symbol: decode_value(json.loads(payload)) for symbol, payload in rows}

    def write_scout(self, symbol: str, at: datetime, features: dict[str, Any]) -> None:
        with self.episodes._connect() as connection:
            connection.execute("INSERT OR REPLACE INTO discovery_scouts VALUES(?,?,?)",
                               (symbol, at.isoformat(), serialize({"observed_at": at, "features": features})))

    def frame(self, decision_id: str) -> dict[str, Any] | None:
        with self.episodes._connect() as connection:
            row = connection.execute("SELECT payload,sha256 FROM discovery_frames WHERE decision_id=?", (decision_id,)).fetchone()
        return unpack(row[0], row[1]) if row else None

    def summaries(self) -> tuple[dict[str, Any], ...]:
        with self.episodes._connect() as connection:
            rows = connection.execute("SELECT summary FROM discovery_frames ORDER BY issued_at,decision_id").fetchall()
        return tuple(decode_summary(row[0]) for row in rows)

    def admit(self, records: Sequence[dict[str, Any]], frame: dict[str, Any], *, total_capacity: int = 120) -> dict[str, Any]:
        from algotrader.infrastructure.recovery_episodes import episode_values, validate_seed_record

        if not 1 <= total_capacity <= 120:
            raise ValueError("invalid study episode capacity")
        decision_id = frame["decision_id"]
        prepared = []
        for record in records:
            record = {**record, "evidence": {**record["evidence"], "discovery_decision_id": decision_id}}
            prepared.append((record, validate_seed_record(record)))
        payload, digest = pack(frame)
        with self.episodes._connect() as connection:
            previous = connection.execute("SELECT sha256,summary FROM discovery_frames WHERE decision_id=?", (decision_id,)).fetchone()
            if previous:
                if previous[0] != digest:
                    raise ValueError("rolling discovery decision conflict")
                return decode_summary(previous[1])
            active = connection.execute("SELECT COUNT(*) FROM episodes WHERE active=1").fetchone()[0]
            total = connection.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
            admissions = []
            for record, episode in prepared:
                key = serialize(episode.instrument.id)
                existing = connection.execute("SELECT episode_id FROM episodes WHERE episode_id=? OR (instrument_key=? AND active=1)",
                                              (episode.episode_id, key)).fetchone()
                signal = record["evidence"].get("signal_key")
                observed = connection.execute("SELECT episode_id FROM discovery_signals WHERE signal_key=?", (signal,)).fetchone() if signal else None
                # This also covers issuance seeds created before rolling admission.
                # A terminal setup can be reconsidered after a new hourly signal.
                previous_states = connection.execute("SELECT payload FROM episodes WHERE instrument_key=?", (key,)).fetchall()
                signal_hour = episode.source_at.replace(minute=0, second=0, microsecond=0)
                same_hour = any(decode_value(json.loads(row[0]))["source_at"].replace(
                    minute=0, second=0, microsecond=0) == signal_hour for row in previous_states)
                reason = "enrolled"
                if existing:
                    reason = "existing_episode"
                elif observed or same_hour:
                    reason = "signal_already_observed"
                elif active >= self.episodes.capacity:
                    reason = "pool_capacity"
                elif total >= total_capacity:
                    reason = "study_episode_capacity"
                else:
                    connection.execute("INSERT INTO episodes VALUES(?,?,?,?,?)", (episode.episode_id, key, 1, 0, serialize(episode_values(episode))))
                    self.episodes._record(connection, episode, record["evidence"])
                    if signal:
                        connection.execute("INSERT INTO discovery_signals VALUES(?,?)", (signal,episode.episode_id))
                    active += 1
                    total += 1
                admissions.append({"symbol": episode.instrument.id.symbol, "episode_id": episode.episode_id, "status": reason})
            summary = {"decision_id": decision_id, "study_id": frame["study_id"], "policy": frame["policy"],
                       "issued_at": frame["issued_at"], "evaluation_at": frame["evaluation_at"], "frame_sha256": digest,
                       "seed_proofs": {record["state"]["episode_id"]: hashlib.sha256(serialize(record).encode()).hexdigest()
                                       for record in frame["seeds"]},
                       "pool": frame["pool"], "admissions": tuple(admissions),
                       "control_symbols": tuple(o["symbol"] for o in frame["control"]["opportunities"]),
                       "challenger_symbols": tuple(o["symbol"] for o in frame["challenger"]["opportunities"]),
                       "evaluated_features": dict(frame.get("evaluated_features", {})),
                       "unavailable_symbols": tuple(frame.get("unavailable_symbols", ())),
                       "qualification_admission": tuple(frame.get("qualification_admission", ())),
                       "positioning": frame["positioning"], "scout_counts": frame["scout_counts"]}
            connection.execute("INSERT INTO discovery_frames VALUES(?,?,?,?,?)",
                               (decision_id, frame["issued_at"].isoformat(), payload, digest, summary_text(summary)))
            return summary


def read_rolling_summaries(path: Path) -> tuple[dict[str, Any], ...]:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return tuple(decode_summary(row[0]) for row in connection.execute(
            "SELECT summary FROM discovery_frames ORDER BY issued_at,decision_id"))
    finally:
        connection.close()


def read_discovery_frame(path: Path, decision_id: str) -> dict[str, Any]:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        row = connection.execute("SELECT payload,sha256 FROM discovery_frames WHERE decision_id=?", (decision_id,)).fetchone()
        if row is None:
            raise ValueError("rolling admission frame is absent")
        return cast(dict[str, Any], unpack(row[0], row[1]))
    finally:
        connection.close()
