"""SQLite persistence for Top-Gainer Short Watch episodes and cycle summaries."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from algotrader.domain.top_gainer_short_watch import TopGainerWatchEpisode, TopGainerWatchState


class TopGainerShortWatchStore:
    """Thread-safe SQLite store for rolling top-gainer short watch episodes."""

    def __init__(self, path: Path) -> None:
        if path.is_symlink():
            raise ValueError("top-gainer watch database symlinks refused")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        resolved = path.parent.resolve() / path.name
        if any(Path(str(resolved) + suffix).is_symlink() for suffix in ("", "-wal", "-shm", "-journal")):
            raise ValueError("top-gainer watch database symlinks refused")
        fd = os.open(resolved, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self.path = resolved
        self._lock = threading.Lock()
        self._ensure_tables()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_tables(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS top_gainer_short_episodes (
                    episode_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    base_asset TEXT NOT NULL,
                    state TEXT NOT NULL,
                    enrolled_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tgsw_symbol_state
                    ON top_gainer_short_episodes (symbol, state);
                CREATE INDEX IF NOT EXISTS idx_tgsw_updated
                    ON top_gainer_short_episodes (updated_at DESC);
                CREATE TABLE IF NOT EXISTS top_gainer_short_cycles (
                    cycle_id TEXT PRIMARY KEY,
                    evaluated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tgsw_cycles_eval
                    ON top_gainer_short_cycles (evaluated_at DESC);
                """
            )

    @staticmethod
    def _serialize_episode(episode: TopGainerWatchEpisode) -> str:
        raw = {
            "episode_id": episode.episode_id,
            "symbol": episode.symbol,
            "base_asset": episode.base_asset,
            "state": episode.state.value,
            "enrolled_at": episode.enrolled_at.isoformat(),
            "updated_at": episode.updated_at.isoformat(),
            "anchor_price": str(episode.anchor_price),
            "anchor_24h_pct": str(episode.anchor_24h_pct),
            "peak_price": str(episode.peak_price),
            "peak_24h_pct": str(episode.peak_24h_pct),
            "latest_price": str(episode.latest_price),
            "latest_24h_pct": str(episode.latest_24h_pct),
            "current_archetype": episode.current_archetype,
            "gates_passed": episode.gates_passed,
            "checklist_passed": episode.checklist_passed,
            "failed_checklist_items": list(episode.failed_checklist_items),
            "latest_blocker": episode.latest_blocker,
            "latest_score": str(episode.latest_score) if episode.latest_score is not None else None,
            "latest_conviction": str(episode.latest_conviction) if episode.latest_conviction is not None else None,
            "pos_in_range_30d": str(episode.pos_in_range_30d) if episode.pos_in_range_30d is not None else None,
            "watch_tier": episode.watch_tier,
            "confirmed_at": episode.confirmed_at.isoformat() if episode.confirmed_at else None,
            "confirmed_opportunity": episode.confirmed_opportunity,
            "invalidation_reason": episode.invalidation_reason,
            "collapse_streak": episode.collapse_streak,
            "evaluation_count": episode.evaluation_count,
            "policy_version": episode.policy_version,
        }
        return json.dumps(raw, sort_keys=True, allow_nan=False)

    @staticmethod
    def _deserialize_episode(payload: str) -> TopGainerWatchEpisode:
        d = json.loads(payload)
        return TopGainerWatchEpisode(
            episode_id=str(d["episode_id"]),
            symbol=str(d["symbol"]),
            base_asset=str(d["base_asset"]),
            state=TopGainerWatchState(str(d["state"])),
            enrolled_at=datetime.fromisoformat(d["enrolled_at"]),
            updated_at=datetime.fromisoformat(d["updated_at"]),
            anchor_price=Decimal(str(d["anchor_price"])),
            anchor_24h_pct=Decimal(str(d["anchor_24h_pct"])),
            peak_price=Decimal(str(d["peak_price"])),
            peak_24h_pct=Decimal(str(d["peak_24h_pct"])),
            latest_price=Decimal(str(d["latest_price"])),
            latest_24h_pct=Decimal(str(d["latest_24h_pct"])),
            current_archetype=d.get("current_archetype"),
            gates_passed=bool(d.get("gates_passed", False)),
            checklist_passed=int(d.get("checklist_passed", 0)),
            failed_checklist_items=tuple(str(x) for x in (d.get("failed_checklist_items") or ())),
            latest_blocker=d.get("latest_blocker"),
            latest_score=Decimal(str(d["latest_score"])) if d.get("latest_score") is not None else None,
            latest_conviction=Decimal(str(d["latest_conviction"])) if d.get("latest_conviction") is not None else None,
            pos_in_range_30d=Decimal(str(d["pos_in_range_30d"])) if d.get("pos_in_range_30d") is not None else None,
            watch_tier=str(d.get("watch_tier", "watching")),
            confirmed_at=datetime.fromisoformat(d["confirmed_at"]) if d.get("confirmed_at") else None,
            confirmed_opportunity=d.get("confirmed_opportunity"),
            invalidation_reason=d.get("invalidation_reason"),
            collapse_streak=int(d.get("collapse_streak", 0)),
            evaluation_count=int(d.get("evaluation_count", 1)),
            policy_version=str(d.get("policy_version", "top-gainer-short-watch-v1")),
        )

    def active_episodes(self) -> list[TopGainerWatchEpisode]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM top_gainer_short_episodes WHERE state IN (?, ?) ORDER BY updated_at DESC",
                (TopGainerWatchState.WATCHING.value, TopGainerWatchState.CONFIRMED_SHORT.value),
            ).fetchall()
        return [self._deserialize_episode(row["payload"]) for row in rows]

    def list_episodes(self, limit: int = 100) -> list[TopGainerWatchEpisode]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM top_gainer_short_episodes ORDER BY updated_at DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [self._deserialize_episode(row["payload"]) for row in rows]

    def latest_cycle(self) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM top_gainer_short_cycles ORDER BY evaluated_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["payload"])  # type: ignore[no-any-return]

    def save_cycle(
        self, cycle_summary: dict[str, Any], episodes: Sequence[TopGainerWatchEpisode]
    ) -> None:
        with self._lock, self._connect() as conn:
            for ep in episodes:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO top_gainer_short_episodes
                        (episode_id, symbol, base_asset, state, enrolled_at, updated_at, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ep.episode_id,
                        ep.symbol,
                        ep.base_asset,
                        ep.state.value,
                        ep.enrolled_at.isoformat(),
                        ep.updated_at.isoformat(),
                        self._serialize_episode(ep),
                    ),
                )
            conn.execute(
                "INSERT OR REPLACE INTO top_gainer_short_cycles (cycle_id, evaluated_at, payload) VALUES (?, ?, ?)",
                (
                    str(cycle_summary["cycle_id"]),
                    str(cycle_summary["evaluated_at"]),
                    json.dumps(cycle_summary, sort_keys=True, allow_nan=False),
                ),
            )
            # Retain only the latest 200 cycle summaries
            conn.execute(
                """
                DELETE FROM top_gainer_short_cycles
                WHERE cycle_id NOT IN (
                    SELECT cycle_id FROM top_gainer_short_cycles ORDER BY evaluated_at DESC, rowid DESC LIMIT 200
                )
                """
            )
