"""Persistent disjoint 5/5 Radar -> 48h Potential Watchlist -> Resolved Ledger service.

Stores Radar episodes in SQLite (`ATLAS_STATE_DB_PATH`) across restarts with:
- Zero duplication between live 5/5 Radar (`ON_RADAR`) and 48h Potential Watchlist (`WATCHLIST`).
- Certified measurement metadata (`measurement_json`) preserving coverage & barrier certification.
- Singleflight concurrency coalescing and 30s force-refresh cooldown (with `internal_scheduler=True`
  override for post-scan evaluation).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Any

from algotrader.domain.auto_watchlist import (
    AUTO_WATCH_WINDOW_HOURS,
    MEASUREMENT_SCHEMA_VERSION,
    AutoWatchEpisode,
    AutoWatchStatus,
    RadarPresence,
    build_auto_watch_episode,
    classify_radar_eviction,
    compute_performance_summary,
    directional_return_pct,
    evaluate_auto_watch_episode,
)
from algotrader.domain.models import Candle, Instrument
from algotrader.domain.ports import MarketDataPort


def _extract_candidate_episode_id(candidate: Mapping[str, Any]) -> str | None:
    """Derive the canonical episode_id matching `build_auto_watch_episode` without requiring 5/5 gates."""
    radar_signal = candidate.get("radar_signal")
    if isinstance(radar_signal, Mapping):
        ep_id = str(radar_signal.get("episode_id") or "").strip()
        if ep_id:
            return ep_id
    symbol = str(candidate.get("symbol", "")).upper().strip()
    if not symbol:
        return None
    direction = str(candidate.get("direction", "long")).lower().strip()
    setup_label = str(candidate.get("setup_label", "UNKNOWN")).strip() or "UNKNOWN"
    origin_raw = candidate.get("first_seen_at") or candidate.get("evaluated_at")
    if not isinstance(origin_raw, str) or not origin_raw:
        return None
    try:
        origin_dt = datetime.fromisoformat(origin_raw)
        if origin_dt.tzinfo is None or origin_dt.utcoffset() is None:
            return None
        origin_iso = origin_dt.astimezone(UTC).isoformat()
    except ValueError:
        return None
    raw_key = f"{symbol}|{direction}|{setup_label}|{origin_iso}"
    return hashlib.sha256(raw_key.encode()).hexdigest()[:32]


def _extract_event_episode_id(event: Mapping[str, Any]) -> str | None:
    cand = event.get("candidate")
    if isinstance(cand, Mapping):
        return _extract_candidate_episode_id(cand)
    return None


class AutoWatchlistService:
    """Manages disjoint ON_RADAR -> WATCHLIST (48h) -> RESOLVED episodes in SQLite."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        window_hours: int = AUTO_WATCH_WINDOW_HOURS,
    ) -> None:
        raw_path = db_path or os.environ.get("ATLAS_STATE_DB_PATH", "atlas_state.sqlite3")
        self._db_path = Path(raw_path)
        if str(self._db_path.parent) not in (".", ""):
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._window_hours = max(1, int(window_hours))
        self._lock = Lock()
        self._refresh_lock = asyncio.Lock()
        self._last_refresh_at: datetime | None = None
        self._ensure_table()

    @contextlib.contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _ensure_table(self) -> None:
        sql = """
            CREATE TABLE IF NOT EXISTS radar_auto_watchlist (
                episode_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                setup_label TEXT NOT NULL,
                execution_mode TEXT NOT NULL,
                score TEXT NOT NULL,
                tick_size TEXT NOT NULL,
                published_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                signal_price TEXT NOT NULL,
                entry_price TEXT NOT NULL,
                trigger_price TEXT,
                stop_price TEXT NOT NULL,
                target_1 TEXT NOT NULL,
                target_2 TEXT,
                target_3 TEXT,
                poc_price TEXT,
                vah_price TEXT,
                val_price TEXT,
                status TEXT NOT NULL,
                radar_presence TEXT NOT NULL,
                radar_status_label TEXT NOT NULL,
                eviction_reason TEXT,
                soft_gate_summary TEXT,
                checklist_json TEXT NOT NULL DEFAULT '[]',
                measurement_json TEXT,
                last_price TEXT NOT NULL,
                last_checked_at TEXT,
                current_return_pct TEXT,
                peak_mfe_pct TEXT,
                peak_mfe_at TEXT,
                worst_mae_pct TEXT,
                worst_mae_at TEXT,
                retest_touched INTEGER NOT NULL DEFAULT 0,
                breakout_touched INTEGER NOT NULL DEFAULT 0,
                was_promoted_to_watchlist INTEGER NOT NULL DEFAULT 0,
                graduation_count INTEGER NOT NULL DEFAULT 0,
                resolved_at TEXT,
                resolved_price TEXT,
                resolved_return_pct TEXT,
                resolution_reason TEXT,
                source_scan_id TEXT,
                policy_version TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_radar_auto_watchlist_status_pub
                ON radar_auto_watchlist (status, published_at DESC);
            CREATE INDEX IF NOT EXISTS idx_radar_auto_watchlist_symbol
                ON radar_auto_watchlist (symbol, published_at DESC);
        """
        with self._lock, self._get_connection() as conn:
            conn.executescript(sql)
            cols = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(radar_auto_watchlist)").fetchall()
            }
            if "soft_gate_summary" not in cols:
                conn.execute("ALTER TABLE radar_auto_watchlist ADD COLUMN soft_gate_summary TEXT")
            if "checklist_json" not in cols:
                conn.execute(
                    "ALTER TABLE radar_auto_watchlist ADD COLUMN checklist_json TEXT NOT NULL DEFAULT '[]'"
                )
            if "measurement_json" not in cols:
                conn.execute("ALTER TABLE radar_auto_watchlist ADD COLUMN measurement_json TEXT")
            if "was_promoted_to_watchlist" not in cols:
                conn.execute(
                    "ALTER TABLE radar_auto_watchlist ADD COLUMN was_promoted_to_watchlist INTEGER NOT NULL DEFAULT 0"
                )
            if "graduation_count" not in cols:
                conn.execute(
                    "ALTER TABLE radar_auto_watchlist ADD COLUMN graduation_count INTEGER NOT NULL DEFAULT 0"
                )
            if "policy_version" not in cols:
                conn.execute("ALTER TABLE radar_auto_watchlist ADD COLUMN policy_version TEXT")
            conn.execute(
                """
                UPDATE radar_auto_watchlist
                SET status = CASE
                    WHEN radar_presence = 'ON_RADAR' THEN 'ON_RADAR'
                    ELSE 'WATCHLIST'
                END
                WHERE status = 'ACTIVE'
                """
            )

    @staticmethod
    def _row_to_episode(row: sqlite3.Row) -> AutoWatchEpisode:
        def _opt_dec(val: Any) -> Decimal | None:
            return Decimal(str(val)) if val is not None and str(val) != "" else None

        def _opt_dt(val: Any) -> datetime | None:
            return datetime.fromisoformat(str(val)).astimezone(UTC) if val else None

        raw_status = str(row["status"])
        if raw_status == "ACTIVE":
            status = (
                AutoWatchStatus.ON_RADAR
                if str(row["radar_presence"]) == RadarPresence.ON_RADAR.value
                else AutoWatchStatus.WATCHLIST
            )
        else:
            status = AutoWatchStatus(raw_status)

        keys = set(row.keys())
        checklist_raw = row["checklist_json"] if "checklist_json" in keys else "[]"
        try:
            parsed_cl = json.loads(checklist_raw or "[]")
            checklist = tuple(parsed_cl) if isinstance(parsed_cl, list) else ()
        except Exception:
            checklist = ()

        meas_raw = row["measurement_json"] if "measurement_json" in keys else None
        if meas_raw:
            try:
                meas = json.loads(meas_raw)
            except Exception:
                meas = None
        else:
            meas = None

        if isinstance(meas, dict) and meas.get("version") == MEASUREMENT_SCHEMA_VERSION:
            has_obs = bool(meas.get("has_observations", False))
            cov_complete = bool(meas.get("coverage_complete", False))
            cov_gaps = int(meas.get("coverage_gaps", 0))
            barrier_cert = bool(meas.get("barrier_order_certified", False))
            endpoint_obs_at = _opt_dt(meas.get("endpoint_observed_at"))
            last_obs_px = _opt_dec(meas.get("last_observed_price"))
            meas_status = str(meas.get("measurement_status") or "pending")
        else:
            # Legacy row without verified measurement metadata
            has_obs = False
            cov_complete = False
            cov_gaps = 0
            barrier_cert = False
            endpoint_obs_at = None
            last_obs_px = None
            meas_status = (
                "legacy_unverified"
                if status not in (AutoWatchStatus.ON_RADAR, AutoWatchStatus.WATCHLIST)
                else "pending"
            )

        return AutoWatchEpisode(
            episode_id=str(row["episode_id"]),
            symbol=str(row["symbol"]),
            direction=str(row["direction"]),
            setup_label=str(row["setup_label"]),
            execution_mode=str(row["execution_mode"]),
            score=Decimal(str(row["score"])),
            tick_size=Decimal(str(row["tick_size"])),
            published_at=datetime.fromisoformat(str(row["published_at"])).astimezone(UTC),
            expires_at=datetime.fromisoformat(str(row["expires_at"])).astimezone(UTC),
            signal_price=Decimal(str(row["signal_price"])),
            entry_price=Decimal(str(row["entry_price"])),
            trigger_price=_opt_dec(row["trigger_price"]),
            stop_price=Decimal(str(row["stop_price"])),
            target_1=Decimal(str(row["target_1"])),
            target_2=_opt_dec(row["target_2"]),
            target_3=_opt_dec(row["target_3"]),
            poc_price=_opt_dec(row["poc_price"]),
            vah_price=_opt_dec(row["vah_price"]),
            val_price=_opt_dec(row["val_price"]),
            status=status,
            radar_presence=RadarPresence(str(row["radar_presence"])),
            radar_status_label=str(row["radar_status_label"]),
            eviction_reason=row["eviction_reason"],
            soft_gate_summary=row["soft_gate_summary"] if "soft_gate_summary" in keys else None,
            checklist=checklist,
            last_price=Decimal(str(row["last_price"])),
            last_observed_price=last_obs_px,
            last_checked_at=_opt_dt(row["last_checked_at"]),
            current_return_pct=_opt_dec(row["current_return_pct"]),
            peak_mfe_pct=_opt_dec(row["peak_mfe_pct"]),
            peak_mfe_at=_opt_dt(row["peak_mfe_at"]),
            worst_mae_pct=_opt_dec(row["worst_mae_pct"]),
            worst_mae_at=_opt_dt(row["worst_mae_at"]),
            retest_touched=bool(row["retest_touched"]),
            breakout_touched=bool(row["breakout_touched"]),
            was_promoted_to_watchlist=bool(
                row["was_promoted_to_watchlist"] if "was_promoted_to_watchlist" in keys else 0
            ),
            graduation_count=int(
                row["graduation_count"] if "graduation_count" in keys and row["graduation_count"] is not None else 0
            ),
            has_observations=has_obs,
            coverage_complete=cov_complete,
            coverage_gaps=cov_gaps,
            barrier_order_certified=barrier_cert,
            endpoint_observed_at=endpoint_obs_at,
            measurement_status=meas_status,
            resolved_at=_opt_dt(row["resolved_at"]),
            resolved_price=_opt_dec(row["resolved_price"]),
            resolved_return_pct=_opt_dec(row["resolved_return_pct"]),
            resolution_reason=row["resolution_reason"],
            source_scan_id=row["source_scan_id"],
            policy_version=(
                str(row["policy_version"])
                if "policy_version" in keys and row["policy_version"] is not None
                else None
            ),
        )

    def _upsert_episode_locked(self, conn: sqlite3.Connection, ep: AutoWatchEpisode) -> None:
        conn.execute(
            """
            INSERT INTO radar_auto_watchlist (
                episode_id, symbol, direction, setup_label, execution_mode, score, tick_size,
                published_at, expires_at, signal_price, entry_price, trigger_price, stop_price,
                target_1, target_2, target_3, poc_price, vah_price, val_price,
                status, radar_presence, radar_status_label, eviction_reason,
                soft_gate_summary, checklist_json, measurement_json,
                last_price, last_checked_at, current_return_pct,
                peak_mfe_pct, peak_mfe_at, worst_mae_pct, worst_mae_at,
                retest_touched, breakout_touched, was_promoted_to_watchlist, graduation_count,
                resolved_at, resolved_price, resolved_return_pct, resolution_reason,
                source_scan_id, policy_version
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?
            )
            ON CONFLICT(episode_id) DO UPDATE SET
                status = excluded.status,
                radar_presence = excluded.radar_presence,
                radar_status_label = excluded.radar_status_label,
                eviction_reason = excluded.eviction_reason,
                soft_gate_summary = excluded.soft_gate_summary,
                checklist_json = excluded.checklist_json,
                measurement_json = excluded.measurement_json,
                last_price = excluded.last_price,
                last_checked_at = excluded.last_checked_at,
                current_return_pct = excluded.current_return_pct,
                peak_mfe_pct = excluded.peak_mfe_pct,
                peak_mfe_at = excluded.peak_mfe_at,
                worst_mae_pct = excluded.worst_mae_pct,
                worst_mae_at = excluded.worst_mae_at,
                retest_touched = excluded.retest_touched,
                breakout_touched = excluded.breakout_touched,
                was_promoted_to_watchlist = excluded.was_promoted_to_watchlist,
                graduation_count = excluded.graduation_count,
                resolved_at = excluded.resolved_at,
                resolved_price = excluded.resolved_price,
                resolved_return_pct = excluded.resolved_return_pct,
                resolution_reason = excluded.resolution_reason,
                policy_version = COALESCE(excluded.policy_version, radar_auto_watchlist.policy_version)
            """,
            (
                ep.episode_id,
                ep.symbol,
                ep.direction,
                ep.setup_label,
                ep.execution_mode,
                str(ep.score),
                str(ep.tick_size),
                ep.published_at.isoformat(),
                ep.expires_at.isoformat(),
                str(ep.signal_price),
                str(ep.entry_price),
                str(ep.trigger_price) if ep.trigger_price is not None else None,
                str(ep.stop_price),
                str(ep.target_1),
                str(ep.target_2) if ep.target_2 is not None else None,
                str(ep.target_3) if ep.target_3 is not None else None,
                str(ep.poc_price) if ep.poc_price is not None else None,
                str(ep.vah_price) if ep.vah_price is not None else None,
                str(ep.val_price) if ep.val_price is not None else None,
                ep.status.value,
                ep.radar_presence.value,
                ep.radar_status_label,
                ep.eviction_reason,
                ep.soft_gate_summary,
                json.dumps(list(ep.checklist)),
                json.dumps(ep.measurement_dict()),
                str(ep.last_price),
                ep.last_checked_at.isoformat() if ep.last_checked_at else None,
                str(ep.current_return_pct) if ep.current_return_pct is not None else None,
                str(ep.peak_mfe_pct) if ep.peak_mfe_pct is not None else None,
                ep.peak_mfe_at.isoformat() if ep.peak_mfe_at else None,
                str(ep.worst_mae_pct) if ep.worst_mae_pct is not None else None,
                ep.worst_mae_at.isoformat() if ep.worst_mae_at else None,
                int(ep.retest_touched),
                int(ep.breakout_touched),
                int(ep.was_promoted_to_watchlist),
                int(ep.graduation_count),
                ep.resolved_at.isoformat() if ep.resolved_at else None,
                str(ep.resolved_price) if ep.resolved_price is not None else None,
                str(ep.resolved_return_pct) if ep.resolved_return_pct is not None else None,
                ep.resolution_reason,
                ep.source_scan_id,
                ep.policy_version,
            ),
        )

    def retire_prior_policy_episodes(
        self,
        active_policy_version: str,
        *,
        policy_activated_at: datetime | None = None,
        now: datetime | None = None,
    ) -> int:
        """Retire lingering ON_RADAR / WATCHLIST episodes created under an older screening policy.

        Preserves every historical row in `resolved` (`screening_policy_changed_requires_fresh_qualification`)
        without triggering the 12h market-failure cooldown, so the active Watchlist contains only
        episodes qualified under `active_policy_version`.
        """
        clean_ver = active_policy_version.strip()
        if not clean_ver:
            return 0
        ref_now = (now or datetime.now(UTC)).astimezone(UTC)
        retired = 0
        with self._lock, self._get_connection() as conn:
            if policy_activated_at is not None:
                cut_iso = policy_activated_at.astimezone(UTC).isoformat()
                conn.execute(
                    """
                    UPDATE radar_auto_watchlist
                    SET policy_version = ?
                    WHERE policy_version IS NULL AND published_at >= ?
                    """,
                    (clean_ver, cut_iso),
                )
            rows = conn.execute(
                """
                SELECT * FROM radar_auto_watchlist
                WHERE status IN ('ON_RADAR', 'WATCHLIST')
                """
            ).fetchall()
            for row in rows:
                ep = self._row_to_episode(row)
                if ep.policy_version == clean_ver:
                    continue
                updated = self._apply_eviction_to_episode(
                    ep,
                    eviction_event={
                        "symbol": ep.symbol,
                        "state": "POLICY_CHANGED",
                        "retained": False,
                        "reason": "screening_policy_changed_requires_fresh_qualification",
                    },
                    ref_now=ref_now,
                )
                self._upsert_episode_locked(conn, updated)
                retired += 1
        return retired

    def _deduplicate_watchlist_locked(self, conn: sqlite3.Connection, ref_now: datetime) -> None:
        """Keep at most one active WATCHLIST episode per `(symbol, direction)` (newest wins)."""
        rows = conn.execute(
            """
            SELECT * FROM radar_auto_watchlist
            WHERE status = 'WATCHLIST'
            ORDER BY published_at DESC
            """
        ).fetchall()
        seen_pairs: set[tuple[str, str]] = set()
        for row in rows:
            ep = self._row_to_episode(row)
            key = (ep.symbol.upper().strip(), ep.direction.lower().strip())
            if key not in seen_pairs:
                seen_pairs.add(key)
                continue
            superseded = self._apply_eviction_to_episode(
                ep,
                eviction_event={
                    "symbol": ep.symbol,
                    "state": "SUPERSEDED",
                    "retained": False,
                    "reason": "replaced_by_new_thesis",
                },
                ref_now=ref_now,
            )
            self._upsert_episode_locked(conn, superseded)

    def blocked_from_radar_symbols(
        self,
        *,
        now: datetime | None = None,
        cooldown_hours: float = 12.0,
    ) -> dict[tuple[str, str], str]:
        """Return `(symbol, direction)` pairs locked out of re-entering ON_RADAR.

        Prevents the 1h Radar <-> Watchlist ping-pong loop:
        1. Any episode currently in `WATCHLIST` with `was_promoted_to_watchlist=True` (already
           demoted from Radar) or `graduation_count >= 1` stays locked in Watchlist.
        2. Any resolved episode (except `replaced_by_new_thesis`) within `cooldown_hours`
           cannot statically recycle onto ON_RADAR.
        Pre-ignition coils (`status == WATCHLIST`, `was_promoted_to_watchlist == False`,
        `graduation_count == 0`) remain eligible for their 1-time kinetic graduation.
        """
        ref_now = (now or datetime.now(UTC)).astimezone(UTC)
        blocked: dict[tuple[str, str], str] = {}
        with self._lock, self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM radar_auto_watchlist
                ORDER BY published_at DESC
                """
            ).fetchall()
        for row in rows:
            ep = self._row_to_episode(row)
            key = (ep.symbol.upper().strip(), ep.direction.lower().strip())
            if key in blocked:
                continue
            if ep.status is AutoWatchStatus.WATCHLIST:
                if ep.was_promoted_to_watchlist or ep.graduation_count >= 1:
                    blocked[key] = "demoted_watchlist_locked"
            elif ep.is_resolved and ep.eviction_reason not in (
                "replaced_by_new_thesis",
                "screening_policy_changed_requires_fresh_qualification",
            ):
                res_dt = ep.resolved_at or ep.last_checked_at
                if res_dt is not None:
                    age_sec = (ref_now - res_dt).total_seconds()
                    if 0.0 <= age_sec < cooldown_hours * 3600.0:
                        blocked[key] = f"resolved_cooldown:{ep.status.value}"
        return blocked

    def enroll_pre_ignition_coil(
        self,
        candidate: Mapping[str, Any],
        *,
        published_at: datetime | None = None,
        scan_id: str | None = None,
    ) -> AutoWatchEpisode | None:
        """Stage a verified 5/5 pre-ignition coil directly in `WATCHLIST` (`graduation_count=0`)."""
        base_ep = build_auto_watch_episode(
            candidate,
            published_at=published_at,
            scan_id=scan_id,
            window_hours=self._window_hours,
        )
        if base_ep is None:
            return None
        coil_ep = replace(
            base_ep,
            status=AutoWatchStatus.WATCHLIST,
            radar_presence=RadarPresence.EVICTED_FROM_RADAR,
            radar_status_label="PRE-IGNITION COIL",
            eviction_reason="awaiting_kinetic_ignition",
            soft_gate_summary=str(candidate.get("kinetic_reason") or "5/5 base coiled; awaiting kinetic trigger"),
            was_promoted_to_watchlist=False,
            graduation_count=0,
        )
        with self._lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM radar_auto_watchlist WHERE episode_id = ?",
                (coil_ep.episode_id,),
            ).fetchone()
            if row is not None:
                return self._row_to_episode(row)
            existing_live = conn.execute(
                """
                SELECT * FROM radar_auto_watchlist
                WHERE symbol = ? AND direction = ? AND status IN ('ON_RADAR', 'WATCHLIST')
                ORDER BY published_at DESC LIMIT 1
                """,
                (coil_ep.symbol, coil_ep.direction),
            ).fetchone()
            if existing_live is not None:
                return self._row_to_episode(existing_live)
            self._upsert_episode_locked(conn, coil_ep)
            return coil_ep

    def enroll_candidate(
        self,
        candidate: Mapping[str, Any],
        *,
        published_at: datetime | None = None,
        scan_id: str | None = None,
    ) -> AutoWatchEpisode | None:
        """Idempotently register a verified 5/5 Radar episode in ON_RADAR state (or graduate a pre-ignition coil once)."""
        ep = build_auto_watch_episode(
            candidate,
            published_at=published_at,
            scan_id=scan_id,
            window_hours=self._window_hours,
        )
        if ep is None:
            return None
        ref_now = (published_at or ep.published_at).astimezone(UTC)
        with self._lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM radar_auto_watchlist WHERE episode_id = ?",
                (ep.episode_id,),
            ).fetchone()
            if row is not None:
                existing_exact = self._row_to_episode(row)
                if existing_exact.status is AutoWatchStatus.WATCHLIST and (
                    existing_exact.was_promoted_to_watchlist or existing_exact.graduation_count >= 1
                ):
                    return None
                return existing_exact

            # Check for any live episode on the same (symbol, direction):
            # - If already demoted to WATCHLIST (`was_promoted_to_watchlist=True`), block ping-pong re-entry!
            # - If a pre-ignition WATCHLIST coil (`was_promoted_to_watchlist=False`, `graduation_count==0`),
            #   graduate it IN-PLACE using the same `episode_id`!
            live_sym_row = conn.execute(
                """
                SELECT * FROM radar_auto_watchlist
                WHERE symbol = ? AND direction = ? AND status IN ('ON_RADAR', 'WATCHLIST')
                ORDER BY published_at DESC LIMIT 1
                """,
                (ep.symbol, ep.direction),
            ).fetchone()
            if live_sym_row is not None:
                live_ep = self._row_to_episode(live_sym_row)
                if live_ep.status is AutoWatchStatus.WATCHLIST:
                    if live_ep.was_promoted_to_watchlist or live_ep.graduation_count >= 1:
                        return None
                    graduated = replace(
                        live_ep,
                        status=AutoWatchStatus.ON_RADAR,
                        radar_presence=RadarPresence.ON_RADAR,
                        radar_status_label=str(candidate.get("radar_status_label") or "KINETIC BREAKOUT"),
                        eviction_reason=None,
                        soft_gate_summary=None,
                        checklist=ep.checklist or live_ep.checklist,
                        graduation_count=live_ep.graduation_count + 1,
                        last_price=ep.signal_price,
                        last_checked_at=ref_now,
                    )
                    self._upsert_episode_locked(conn, graduated)
                    return graduated
                if (
                    live_ep.setup_label == ep.setup_label
                    and abs((ep.published_at - live_ep.published_at).total_seconds()) < 6 * 3600
                ):
                    return live_ep
            self._upsert_episode_locked(conn, ep)
            return ep

    def _apply_eviction_to_episode(
        self,
        ep: AutoWatchEpisode,
        *,
        eviction_event: Mapping[str, Any] | None,
        ref_now: datetime,
    ) -> AutoWatchEpisode:
        next_status, reason, soft_summary, updated_cl = classify_radar_eviction(
            ep, event=eviction_event, now=ref_now
        )
        if next_status is AutoWatchStatus.WATCHLIST:
            return replace(
                ep,
                status=AutoWatchStatus.WATCHLIST,
                radar_presence=RadarPresence.EVICTED_FROM_RADAR,
                radar_status_label="4/5 WATCHLIST",
                eviction_reason=reason,
                soft_gate_summary=soft_summary,
                checklist=updated_cl,
                was_promoted_to_watchlist=True,
                last_checked_at=ref_now,
            )

        raw_cand = eviction_event.get("candidate") if isinstance(eviction_event, Mapping) else None
        cand: Mapping[str, Any] = raw_cand if isinstance(raw_cand, Mapping) else {}
        raw_px = cand.get("current_price")
        resolved_px: Decimal | None = ep.last_observed_price
        if next_status is AutoWatchStatus.STOP_BREACHED:
            resolved_px = ep.stop_price
        elif next_status is AutoWatchStatus.TARGET_REACHED:
            resolved_px = ep.target_1
        elif raw_px is not None:
            with contextlib.suppress(Exception):
                d = Decimal(str(raw_px))
                if d.is_finite() and d > 0:
                    resolved_px = d

        ret = (
            directional_return_pct(
                direction=ep.direction,
                reference_price=ep.signal_price,
                price=resolved_px,
            )
            if resolved_px is not None and resolved_px > 0
            else None
        )
        has_obs = ep.has_observations or (resolved_px is not None and resolved_px > 0)
        peak_mfe = (
            (ret if ep.peak_mfe_pct is None else max(ep.peak_mfe_pct, ret))
            if ret is not None
            else ep.peak_mfe_pct
        )
        worst_mae = (
            (ret if ep.worst_mae_pct is None else min(ep.worst_mae_pct, ret))
            if ret is not None
            else ep.worst_mae_pct
        )
        is_barrier = next_status in (AutoWatchStatus.STOP_BREACHED, AutoWatchStatus.TARGET_REACHED)
        return replace(
            ep,
            status=next_status,
            radar_presence=RadarPresence.EVICTED_FROM_RADAR,
            radar_status_label=next_status.value.replace("_", " "),
            eviction_reason=reason,
            soft_gate_summary=None,
            checklist=updated_cl,
            last_price=resolved_px if resolved_px is not None else ep.last_price,
            last_observed_price=resolved_px,
            last_checked_at=ref_now,
            current_return_pct=ret,
            peak_mfe_pct=peak_mfe,
            worst_mae_pct=worst_mae,
            has_observations=has_obs,
            barrier_order_certified=ep.barrier_order_certified if is_barrier else False,
            endpoint_observed_at=ref_now if resolved_px is not None else ep.endpoint_observed_at,
            measurement_status="measured" if (not is_barrier and ret is not None) else ("measured" if ep.barrier_order_certified else "eviction_recorded"),
            resolved_at=ref_now,
            resolved_price=resolved_px,
            resolved_return_pct=ret,
            resolution_reason=reason,
        )

    def sync_from_scan(
        self,
        *,
        published_opportunities: Sequence[Mapping[str, Any]],
        radar_events: Sequence[Mapping[str, Any]],
        now: datetime | None = None,
        scan_id: str | None = None,
    ) -> int:
        """Maintain strict disjointness between live 5/5 Radar and 48h Potential Watchlist."""
        ref_now = (now or datetime.now(UTC)).astimezone(UTC)
        enrolled_count = 0

        active_radar_by_symbol: dict[str, Mapping[str, Any]] = {}
        active_radar_by_episode_id: dict[str, Mapping[str, Any]] = {}
        for opp in published_opportunities:
            sym = str(opp.get("symbol", "")).upper().strip()
            if sym:
                active_radar_by_symbol[sym] = opp
            opp_ep_id = _extract_candidate_episode_id(opp)
            if opp_ep_id:
                active_radar_by_episode_id[opp_ep_id] = opp

        evictions_by_symbol: dict[str, Mapping[str, Any]] = {}
        evictions_by_episode_id: dict[str, Mapping[str, Any]] = {}
        for ev in radar_events:
            if isinstance(ev, Mapping) and ev.get("retained") is False:
                sym = str(ev.get("symbol", "")).upper().strip()
                if sym:
                    evictions_by_symbol[sym] = ev
                ev_ep_id = _extract_event_episode_id(ev)
                if ev_ep_id:
                    evictions_by_episode_id[ev_ep_id] = ev

        # Apply explicit episode-matched evictions (e.g. SUPERSEDED old thesis) BEFORE enrolling
        # newly published candidates so a <6h thesis replacement is never swallowed by dedup.
        if evictions_by_episode_id:
            with self._lock, self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM radar_auto_watchlist WHERE status IN ('ON_RADAR', 'WATCHLIST')"
                ).fetchall()
                for row in rows:
                    ep = self._row_to_episode(row)
                    ev_for_ep = evictions_by_episode_id.get(ep.episode_id)
                    if ev_for_ep is not None and ep.episode_id not in active_radar_by_episode_id:
                        updated = self._apply_eviction_to_episode(
                            ep, eviction_event=ev_for_ep, ref_now=ref_now
                        )
                        self._upsert_episode_locked(conn, updated)

        for opp in published_opportunities:
            enrolled_ep = self.enroll_candidate(opp, published_at=ref_now, scan_id=scan_id)
            if enrolled_ep is not None:
                enrolled_count += 1
                active_radar_by_episode_id[enrolled_ep.episode_id] = opp

        with self._lock, self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM radar_auto_watchlist WHERE status IN ('ON_RADAR', 'WATCHLIST')"
            ).fetchall()
            for row in rows:
                ep = self._row_to_episode(row)
                ev_for_ep = evictions_by_episode_id.get(ep.episode_id)
                live_for_ep = active_radar_by_episode_id.get(ep.episode_id)
                live_for_sym = active_radar_by_symbol.get(ep.symbol)
                live_sym_ep_id = (
                    _extract_candidate_episode_id(live_for_sym)
                    if live_for_sym is not None
                    else None
                )

                # If this exact episode was explicitly evicted (including SUPERSEDED by a newer
                # thesis on the same symbol), apply its eviction instead of keeping it ON_RADAR.
                if ev_for_ep is not None and live_for_ep is None:
                    updated = self._apply_eviction_to_episode(
                        ep, eviction_event=ev_for_ep, ref_now=ref_now
                    )
                    self._upsert_episode_locked(conn, updated)
                    continue

                # Monotonic lock: a demoted WATCHLIST episode NEVER flips back to ON_RADAR.
                if ep.status is AutoWatchStatus.WATCHLIST and (
                    ep.was_promoted_to_watchlist or ep.graduation_count >= 1
                ):
                    continue

                # Same episode is live on 5/5 Radar (or live opportunity matches this symbol without
                # a distinct newer episode_id).
                is_same_live_episode = live_for_ep is not None or (
                    live_for_sym is not None
                    and (live_sym_ep_id is None or live_sym_ep_id == ep.episode_id)
                )
                if is_same_live_episode:
                    live_opp = live_for_ep or live_for_sym
                    assert live_opp is not None
                    raw_cl = live_opp.get("checklist")
                    cl = (
                        tuple(
                            {
                                "title": str(i.get("title", "")),
                                "passed": bool(i.get("passed") is True),
                                "status": str(
                                    i.get("status")
                                    or ("passed" if i.get("passed") is True else "failed")
                                ),
                                "detail": str(i.get("detail", "")),
                            }
                            for i in raw_cl
                            if isinstance(i, Mapping)
                        )
                        if isinstance(raw_cl, Sequence) and not isinstance(raw_cl, (str, bytes))
                        else ep.checklist
                    )
                    updated = replace(
                        ep,
                        status=AutoWatchStatus.ON_RADAR,
                        radar_presence=RadarPresence.ON_RADAR,
                        radar_status_label=str(
                            live_opp.get("radar_status_label") or ep.radar_status_label
                        ),
                        eviction_reason=None,
                        soft_gate_summary=None,
                        checklist=cl or ep.checklist,
                        last_checked_at=ref_now,
                    )
                    if updated != ep:
                        self._upsert_episode_locked(conn, updated)
                elif ep.status is AutoWatchStatus.ON_RADAR:
                    ev_obj: Mapping[str, Any] | None = evictions_by_episode_id.get(ep.episode_id)
                    if ev_obj is None:
                        if live_for_sym is not None and live_sym_ep_id != ep.episode_id:
                            # Replaced on live Radar by a distinct newer episode_id
                            sym_ev = evictions_by_symbol.get(ep.symbol)
                            ev_obj = sym_ev or {
                                "symbol": ep.symbol,
                                "state": "SUPERSEDED",
                                "retained": False,
                                "reason": "replaced_by_new_thesis",
                            }
                        else:
                            ev_obj = evictions_by_symbol.get(ep.symbol)
                    updated = self._apply_eviction_to_episode(
                        ep, eviction_event=ev_obj, ref_now=ref_now
                    )
                    self._upsert_episode_locked(conn, updated)
            self._deduplicate_watchlist_locked(conn, ref_now)
        return enrolled_count

    def _heal_premature_same_scan_superseded_locked(self, conn: sqlite3.Connection) -> int:
        """Delete corrupted rows killed by the legacy same-scan SUPERSEDED symbol-matching bug.

        When an older thesis was superseded in the same scan as a brand-new FRESH_COIL episode,
        the legacy symbol-only backfill marked the brand-new episode HARD_INVALIDATED with
        `replaced_by_new_thesis` within <= 60s of its own `published_at`. Removing those rows
        before chronological replay lets `backfill_from_radar_events_dir` reconstruct their true
        lifecycle (including soft promotion to WATCHLIST).
        """
        rows = conn.execute(
            """
            SELECT episode_id, published_at, resolved_at
            FROM radar_auto_watchlist
            WHERE status = 'HARD_INVALIDATED'
              AND eviction_reason = 'replaced_by_new_thesis'
              AND resolved_at IS NOT NULL
            """
        ).fetchall()
        corrupted_ids: list[tuple[str]] = []
        for row in rows:
            try:
                pub_dt = datetime.fromisoformat(str(row["published_at"])).astimezone(UTC)
                res_dt = datetime.fromisoformat(str(row["resolved_at"])).astimezone(UTC)
                if (res_dt - pub_dt).total_seconds() <= 60.0:
                    corrupted_ids.append((str(row["episode_id"]),))
            except Exception:
                continue
        if corrupted_ids:
            conn.executemany(
                "DELETE FROM radar_auto_watchlist WHERE episode_id = ?",
                corrupted_ids,
            )
        return len(corrupted_ids)

    def backfill_from_radar_events_dir(
        self,
        events_dir: Path | str = Path("data/radar_events"),
        *,
        lookback_days: int = 14,
        live_radar_symbols: Sequence[str] = (),
        now: datetime | None = None,
    ) -> int:
        """Chronologically replay recent radar_events JSON files into ON_RADAR / WATCHLIST / RESOLVED."""
        directory = Path(events_dir)
        if not directory.is_dir():
            return 0
        ref_now = (now or datetime.now(UTC)).astimezone(UTC)
        cutoff = ref_now - timedelta(days=max(1, lookback_days))

        with self._lock, self._get_connection() as conn:
            self._heal_premature_same_scan_superseded_locked(conn)

        indexed: list[tuple[datetime, Path]] = []
        for path in directory.glob("*.json"):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
                if mtime < cutoff - timedelta(days=1):
                    continue
                doc = json.loads(path.read_text(encoding="utf-8"))
                if doc.get("schema") != "atlas-radar-events-v2":
                    continue
                at_raw = doc.get("at")
                at_dt = (
                    datetime.fromisoformat(at_raw).astimezone(UTC)
                    if isinstance(at_raw, str)
                    else mtime
                )
                if at_dt >= cutoff:
                    indexed.append((at_dt, path))
            except Exception:
                continue

        enrolled = 0
        live_set = {s.upper().strip() for s in live_radar_symbols if s}
        for file_at, path in sorted(indexed, key=lambda item: item[0]):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                scan_id = str(doc.get("scan_id") or "") or None
                events = doc.get("events") or []

                # 1. Process evictions FIRST (matched by episode_id, falling back to symbol
                #    only when episode_id is absent) so a same-scan SUPERSEDED / INVALIDATED
                #    event for an OLD thesis can NEVER kill a brand-new FRESH_COIL episode.
                evictions_by_ep_id: dict[str, Mapping[str, Any]] = {}
                evictions_by_sym_fallback: dict[str, Mapping[str, Any]] = {}
                fresh_ep_ids_in_file: set[str] = set()
                for ev in events:
                    if not isinstance(ev, Mapping):
                        continue
                    if ev.get("state") == "FRESH_COIL" and ev.get("retained") is True:
                        cand = ev.get("candidate")
                        if isinstance(cand, Mapping):
                            cid = _extract_candidate_episode_id(cand)
                            if cid:
                                fresh_ep_ids_in_file.add(cid)
                    elif ev.get("retained") is False:
                        ev_ep_id = _extract_event_episode_id(ev)
                        if ev_ep_id:
                            evictions_by_ep_id[ev_ep_id] = ev
                        else:
                            sym = str(ev.get("symbol", "")).upper().strip()
                            if sym:
                                evictions_by_sym_fallback[sym] = ev

                has_policy_reset = any(
                    isinstance(ev, Mapping) and ev.get("state") == "POLICY_CHANGED"
                    for ev in events
                )
                if has_policy_reset:
                    with self._lock, self._get_connection() as conn:
                        prior_rows = conn.execute(
                            "SELECT * FROM radar_auto_watchlist WHERE status IN ('ON_RADAR', 'WATCHLIST')"
                        ).fetchall()
                        for prow in prior_rows:
                            pep = self._row_to_episode(prow)
                            if pep.published_at <= file_at + timedelta(seconds=60):
                                retired_ep = self._apply_eviction_to_episode(
                                    pep,
                                    eviction_event={
                                        "symbol": pep.symbol,
                                        "state": "POLICY_CHANGED",
                                        "retained": False,
                                        "reason": "screening_policy_changed_requires_fresh_qualification",
                                    },
                                    ref_now=file_at,
                                )
                                self._upsert_episode_locked(conn, retired_ep)

                if evictions_by_ep_id or evictions_by_sym_fallback:
                    with self._lock, self._get_connection() as conn:
                        rows = conn.execute(
                            "SELECT * FROM radar_auto_watchlist WHERE status = 'ON_RADAR'"
                        ).fetchall()
                        for row in rows:
                            ep = self._row_to_episode(row)
                            if ep.episode_id in fresh_ep_ids_in_file:
                                continue
                            ev_obj = evictions_by_ep_id.get(ep.episode_id)
                            if ev_obj is None:
                                fallback = evictions_by_sym_fallback.get(ep.symbol)
                                if fallback is not None and ep.published_at <= file_at + timedelta(seconds=60):
                                    ev_obj = fallback
                            if ev_obj is None:
                                continue
                            updated = self._apply_eviction_to_episode(
                                ep, eviction_event=ev_obj, ref_now=file_at
                            )
                            self._upsert_episode_locked(conn, updated)

                # 2. Enroll brand-new FRESH_COIL episodes AFTER prior-episode evictions.
                for ev in events:
                    if not isinstance(ev, Mapping):
                        continue
                    if ev.get("state") != "FRESH_COIL" or ev.get("retained") is not True:
                        continue
                    cand = ev.get("candidate")
                    if not isinstance(cand, Mapping):
                        continue
                    ev_at_raw = ev.get("at") or doc.get("at")
                    ev_at = (
                        datetime.fromisoformat(ev_at_raw).astimezone(UTC)
                        if isinstance(ev_at_raw, str)
                        else file_at
                    )
                    new_ep = self.enroll_candidate(cand, published_at=ev_at, scan_id=scan_id)
                    if new_ep is not None:
                        enrolled += 1
                        # Ensure no older ON_RADAR thesis on the same symbol stays orphaned
                        with self._lock, self._get_connection() as conn:
                            older_rows = conn.execute(
                                """
                                SELECT * FROM radar_auto_watchlist
                                WHERE symbol = ? AND status = 'ON_RADAR' AND episode_id != ?
                                """,
                                (new_ep.symbol, new_ep.episode_id),
                            ).fetchall()
                            for older_row in older_rows:
                                older_ep = self._row_to_episode(older_row)
                                if older_ep.published_at < new_ep.published_at:
                                    superseded = self._apply_eviction_to_episode(
                                        older_ep,
                                        eviction_event={
                                            "symbol": older_ep.symbol,
                                            "state": "SUPERSEDED",
                                            "retained": False,
                                            "reason": "replaced_by_new_thesis",
                                        },
                                        ref_now=ev_at,
                                    )
                                    self._upsert_episode_locked(conn, superseded)
            except Exception:
                continue

        if live_set:
            with self._lock, self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM radar_auto_watchlist WHERE status = 'WATCHLIST' ORDER BY published_at DESC"
                ).fetchall()
                promoted_syms: set[str] = set()
                for row in rows:
                    ep = self._row_to_episode(row)
                    if ep.was_promoted_to_watchlist or ep.graduation_count >= 1:
                        continue
                    if ep.symbol in live_set and ep.symbol not in promoted_syms:
                        promoted_syms.add(ep.symbol)
                        self._upsert_episode_locked(
                            conn,
                            replace(
                                ep,
                                status=AutoWatchStatus.ON_RADAR,
                                radar_presence=RadarPresence.ON_RADAR,
                                eviction_reason=None,
                                soft_gate_summary=None,
                            ),
                        )
        return enrolled

    def refresh_due(
        self,
        *,
        now: datetime | None = None,
        cooldown_seconds: float = 30.0,
    ) -> bool:
        """Return True when the 30s singleflight refresh cooldown has elapsed."""
        if self._last_refresh_at is None:
            return True
        ref_now = (now or datetime.now(UTC)).astimezone(UTC)
        return (ref_now - self._last_refresh_at).total_seconds() >= cooldown_seconds

    def list_episodes(
        self,
        *,
        days: int = 14,
        now: datetime | None = None,
    ) -> list[AutoWatchEpisode]:
        ref_now = (now or datetime.now(UTC)).astimezone(UTC)
        cutoff = (ref_now - timedelta(days=max(1, days))).isoformat()
        with self._lock, self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM radar_auto_watchlist
                WHERE status IN ('ON_RADAR', 'WATCHLIST') OR published_at >= ?
                ORDER BY published_at DESC
                """,
                (cutoff,),
            ).fetchall()
        return [self._row_to_episode(r) for r in rows]

    async def refresh_and_evaluate(
        self,
        market_data: MarketDataPort,
        instruments: Sequence[Instrument],
        *,
        force_refresh: bool = False,
        internal_scheduler: bool = False,
        now: datetime | None = None,
    ) -> list[AutoWatchEpisode]:
        """Evaluate all ON_RADAR and WATCHLIST episodes against live Binance quotes & 1h klines.

        Respects the 30s singleflight cooldown for external/operator force refreshes;
        only `internal_scheduler=True` (post-scan hook) may bypass cooldown.
        """
        ref_now = (now or datetime.now(UTC)).astimezone(UTC)
        if not internal_scheduler and not self.refresh_due(now=ref_now):
            return self.list_episodes(now=ref_now)

        async with self._refresh_lock:
            ref_now = (now or datetime.now(UTC)).astimezone(UTC)
            if not internal_scheduler and not self.refresh_due(now=ref_now):
                return self.list_episodes(now=ref_now)

            all_eps = self.list_episodes(now=ref_now)
            live_eps = [e for e in all_eps if e.is_live_tracked]
            if not live_eps:
                self._last_refresh_at = ref_now
                return all_eps

            inst_map = {inst.id.symbol: inst for inst in instruments}
            active_symbols = sorted({e.symbol for e in live_eps})

            # 1. Fetch fresh live bid/ask midpoints with explicit observed timestamps
            live_quotes: dict[str, tuple[Decimal, datetime]] = {}
            target_insts = [inst_map[s] for s in active_symbols if s in inst_map]
            if target_insts:
                with contextlib.suppress(Exception):
                    quotes = await asyncio.wait_for(market_data.quotes(target_insts), timeout=6.0)
                    for q in quotes:
                        if q.is_valid_book and q.is_fresh_at(ref_now, max_age_seconds=300.0):
                            obs_dt = q.exchange_observed_at or q.observed_at
                            live_quotes[q.instrument_id.symbol] = (q.mid, obs_dt)

            # 2. Fetch 1h candles covering each symbol's earliest active published_at
            earliest_by_symbol: dict[str, datetime] = {}
            for ep in live_eps:
                prev = earliest_by_symbol.get(ep.symbol)
                if prev is None or ep.published_at < prev:
                    earliest_by_symbol[ep.symbol] = ep.published_at

            sem = asyncio.Semaphore(8)
            candles_by_symbol: dict[str, Sequence[Candle]] = {}

            async def _fetch_candles(sym: str) -> None:
                inst = inst_map.get(sym)
                if inst is None:
                    return
                earliest_pub = earliest_by_symbol.get(sym, ref_now - timedelta(hours=72))
                age_hours = max(0, int((ref_now - earliest_pub).total_seconds() // 3600))
                needed_bars = min(500, max(72, age_hours + 6))
                async with sem:
                    with contextlib.suppress(Exception):
                        bars = await asyncio.wait_for(
                            market_data.candles(inst, "1h", needed_bars),
                            timeout=6.0,
                        )
                        candles_by_symbol[sym] = bars

            await asyncio.gather(*(_fetch_candles(s) for s in active_symbols))

            # 3. Evaluate & persist updated/resolved episodes atomically
            with self._lock, self._get_connection() as conn:
                for ep in live_eps:
                    q_pair = live_quotes.get(ep.symbol)
                    updated = evaluate_auto_watch_episode(
                        ep,
                        candles=candles_by_symbol.get(ep.symbol, ()),
                        live_price=q_pair[0] if q_pair else None,
                        live_observed_at=q_pair[1] if q_pair else None,
                        now=ref_now,
                    )
                    self._upsert_episode_locked(conn, updated)

            self._last_refresh_at = ref_now
            return self.list_episodes(now=ref_now)

    def build_response(
        self,
        *,
        days: int = 14,
        live_radar_symbols: Sequence[str] = (),
        now: datetime | None = None,
        early_recovery_enabled: bool = True,
    ) -> dict[str, Any]:
        """Serialize disjoint 48h Potential Watchlist, On-Radar cohort, Resolved Outcomes, and Summary."""
        ref_now = (now or datetime.now(UTC)).astimezone(UTC)
        episodes = self.list_episodes(days=days, now=ref_now)
        if not early_recovery_enabled:
            episodes = [e for e in episodes if e.setup_label != "RECOVERY_RECLAIM_LONG"]
        live_set = {s.upper().strip() for s in live_radar_symbols if s}

        # Strict disjointness + 1-active-card-per-(symbol, direction) deduplication
        watchlist: list[AutoWatchEpisode] = []
        seen_watchlist_pairs: set[tuple[str, str]] = set()
        for e in episodes:
            if e.status is AutoWatchStatus.WATCHLIST and e.symbol not in live_set:
                pair = (e.symbol.upper().strip(), e.direction.lower().strip())
                if pair not in seen_watchlist_pairs:
                    seen_watchlist_pairs.add(pair)
                    watchlist.append(e)
        on_radar = [
            e
            for e in episodes
            if e.status is AutoWatchStatus.ON_RADAR or (e.is_live_tracked and e.symbol in live_set)
        ]
        resolved = [e for e in episodes if e.is_resolved]
        resolved.sort(key=lambda e: e.resolved_at or e.published_at, reverse=True)
        summary = compute_performance_summary(episodes)
        watchlist_dicts = [e.as_dict(now=ref_now) for e in watchlist]
        return {
            "window_hours": self._window_hours,
            "lookback_days": days,
            "generated_at": ref_now.isoformat(),
            "watchlist": watchlist_dicts,
            "active": watchlist_dicts,
            "on_radar": [e.as_dict(now=ref_now) for e in on_radar],
            "resolved": [e.as_dict(now=ref_now) for e in resolved],
            "summary": summary["overall"],
            "on_radar_cohort": summary["on_radar_cohort"],
            "evicted_from_radar_cohort": summary["evicted_from_radar_cohort"],
            "daily_breakdown": summary["daily_breakdown"],
            "archetype_breakdown": summary["archetype_breakdown"],
            "semantics": summary["semantics"],
        }
