from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from uuid import UUID

from algotrader.domain.errors import ConfigurationError
from algotrader.domain.models import (
    Direction,
    ExecutionLifecycle,
    ExecutionStage,
    InstrumentId,
    ManagementStage,
    OpportunityObservation,
    OrderIntent,
    OrderResult,
    OrderStatus,
    PositionManagementState,
    TrackedSignal,
    TrackedSignalStatus,
    TradeRecord,
    TradeStatus,
    UnresolvedOrderIntent,
    Venue,
)

LATEST_SCHEMA_VERSION = 2


def _json_default(value: object) -> object:
    if isinstance(value, Decimal | UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"cannot serialize {type(value).__name__}")


class SqliteStateRepository:
    """Durable single-node state. Replace with PostgreSQL for active/passive HA."""

    def __init__(
        self,
        path: Path,
        *,
        research_retention_days: int = 730,
        event_retention_days: int = 730,
    ) -> None:
        if research_retention_days <= 0 or event_retention_days <= 0:
            raise ValueError("state retention windows must be positive")
        self._path = path
        self._lock = asyncio.Lock()
        self._research_retention = timedelta(days=research_retention_days)
        self._event_retention = timedelta(days=event_retention_days)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path)
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            self._restrict_state_file_permissions()
            # sqlite3.Connection.__exit__ commits or rolls back but deliberately does not
            # close the handle. Own both scopes here so every repository operation has a
            # deterministic transaction and resource lifetime.
            with connection:
                yield connection
        finally:
            connection.close()

    def _restrict_state_file_permissions(self) -> None:
        """Keep trading state private even when the process umask is permissive."""
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self._path}{suffix}")
            if path.exists():
                os.chmod(path, 0o600)

    async def initialize(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
            current_version = int(row[0]) if row and row[0] is not None else 0
            if current_version > LATEST_SCHEMA_VERSION:
                raise ConfigurationError(
                    f"state schema version {current_version} is newer than supported "
                    f"version {LATEST_SCHEMA_VERSION}"
                )
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS order_intents (
                    client_order_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS order_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_order_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_order_results_client_order_id
                    ON order_results(client_order_id);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);
                CREATE TABLE IF NOT EXISTS execution_leases (
                    name TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trades (
                    signal_id TEXT PRIMARY KEY,
                    venue TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    entry_price TEXT NOT NULL,
                    stop_price TEXT NOT NULL,
                    target_price TEXT NOT NULL,
                    entry_order_id TEXT NOT NULL,
                    protection_order_ids TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    exit_price TEXT,
                    closed_at TEXT,
                    gross_realized_pnl TEXT NOT NULL DEFAULT '0',
                    fees TEXT NOT NULL DEFAULT '0',
                    exit_reason TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
                CREATE INDEX IF NOT EXISTS idx_trades_closed_at ON trades(closed_at);
                CREATE TABLE IF NOT EXISTS tracked_signals (
                    signal_id TEXT PRIMARY KEY,
                    venue TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    signal_bar_closed_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    observations INTEGER NOT NULL,
                    score TEXT NOT NULL,
                    entry_price TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_tracked_signals_active_identity
                    ON tracked_signals(venue, symbol, direction)
                    WHERE status = 'active';
                CREATE INDEX IF NOT EXISTS idx_tracked_signals_status_expiry
                    ON tracked_signals(status, expires_at);
                CREATE TABLE IF NOT EXISTS position_management (
                    signal_id TEXT PRIMARY KEY,
                    venue TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    entry_price TEXT NOT NULL,
                    initial_stop_price TEXT NOT NULL,
                    initial_quantity TEXT NOT NULL,
                    remaining_quantity TEXT NOT NULL,
                    current_stop_price TEXT NOT NULL,
                    target_price TEXT NOT NULL,
                    best_price TEXT NOT NULL,
                    realized_partial_pnl TEXT NOT NULL DEFAULT '0',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_opportunities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    venue TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    strategy_profile TEXT NOT NULL,
                    signal_bar_closed_at TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    eligible INTEGER NOT NULL,
                    score TEXT NOT NULL,
                    entry_price TEXT NOT NULL,
                    features TEXT NOT NULL,
                    rejection_reasons TEXT NOT NULL,
                    UNIQUE(
                        venue, symbol, direction, strategy_profile, signal_bar_closed_at
                    )
                );
                CREATE INDEX IF NOT EXISTS idx_research_opportunities_time
                    ON research_opportunities(signal_bar_closed_at);
                CREATE INDEX IF NOT EXISTS idx_research_opportunities_symbol_time
                    ON research_opportunities(venue, symbol, signal_bar_closed_at);
                """
            )
            if current_version < 2:
                self._migrate_to_version_2(connection)
            connection.execute(
                "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
                (LATEST_SCHEMA_VERSION, datetime.now(UTC).isoformat()),
            )
            connection.execute(
                "INSERT OR IGNORE INTO state(key, value, updated_at) VALUES (?, ?, ?)",
                (
                    "kill_switch",
                    json.dumps({"active": False, "reason": ""}),
                    datetime.now(UTC).isoformat(),
                ),
            )

    @staticmethod
    def _migrate_to_version_2(connection: sqlite3.Connection) -> None:
        """Bring the original operational schema to the research/management schema.

        Fresh databases already contain these columns through the current CREATE statements.
        Existing GCP volumes do not: SQLite's CREATE TABLE IF NOT EXISTS never modifies a table,
        so each additive change must be checked and applied explicitly.
        """

        additions = {
            "trades": (
                ("gross_realized_pnl", "TEXT NOT NULL DEFAULT '0'"),
                ("fees", "TEXT NOT NULL DEFAULT '0'"),
                ("exit_reason", "TEXT NOT NULL DEFAULT ''"),
            ),
            "position_management": (("realized_partial_pnl", "TEXT NOT NULL DEFAULT '0'"),),
        }
        for table, columns in additions.items():
            existing = {
                str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, declaration in columns:
                if name not in existing:
                    # Table, column and declarations are closed, code-owned migration constants.
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    async def record_opportunities(self, observations: Sequence[OpportunityObservation]) -> int:
        if not observations:
            return 0
        rows = [
            (
                item.instrument_id.venue.value,
                item.instrument_id.symbol,
                item.direction.value,
                item.strategy_profile,
                item.signal_bar_closed_at.isoformat(),
                item.observed_at.isoformat(),
                int(item.eligible),
                str(item.score),
                str(item.entry_price),
                json.dumps(item.features, default=_json_default, separators=(",", ":")),
                json.dumps(item.rejection_reasons, separators=(",", ":")),
            )
            for item in observations
        ]
        async with self._lock:
            return await asyncio.to_thread(self._record_opportunities_sync, rows)

    def _record_opportunities_sync(self, rows: Sequence[tuple[object, ...]]) -> int:
        with self._connect() as connection:
            before = connection.total_changes
            connection.executemany(
                "INSERT INTO research_opportunities(venue, symbol, direction, "
                "strategy_profile, signal_bar_closed_at, observed_at, eligible, score, "
                "entry_price, features, rejection_reasons) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(venue, symbol, direction, strategy_profile, signal_bar_closed_at) "
                "DO UPDATE SET observed_at=excluded.observed_at, eligible=excluded.eligible, "
                "score=excluded.score, entry_price=excluded.entry_price, "
                "features=excluded.features, rejection_reasons=excluded.rejection_reasons",
                rows,
            )
            changed = connection.total_changes - before
            newest_observation = max(datetime.fromisoformat(str(row[5])) for row in rows)
            connection.execute(
                "DELETE FROM research_opportunities WHERE signal_bar_closed_at < ?",
                ((newest_observation - self._research_retention).isoformat(),),
            )
            return changed

    async def list_opportunities(
        self, *, since: datetime | None = None
    ) -> tuple[OpportunityObservation, ...]:
        async with self._lock:
            rows = await asyncio.to_thread(
                self._list_opportunities_sync, since.isoformat() if since else None
            )
        observations: list[OpportunityObservation] = []
        for row in rows:
            features = json.loads(str(row[9]))
            reasons = json.loads(str(row[10]))
            if not isinstance(features, dict) or not isinstance(reasons, list):
                raise ValueError("invalid research opportunity payload")
            observations.append(
                OpportunityObservation(
                    instrument_id=InstrumentId(Venue(str(row[0])), str(row[1])),
                    direction=Direction(str(row[2])),
                    strategy_profile=str(row[3]),
                    signal_bar_closed_at=datetime.fromisoformat(str(row[4])),
                    observed_at=datetime.fromisoformat(str(row[5])),
                    eligible=bool(row[6]),
                    score=Decimal(str(row[7])),
                    entry_price=Decimal(str(row[8])),
                    features=features,
                    rejection_reasons=tuple(str(value) for value in reasons),
                )
            )
        return tuple(observations)

    def _list_opportunities_sync(self, since: str | None) -> list[tuple[object, ...]]:
        query = (
            "SELECT venue, symbol, direction, strategy_profile, signal_bar_closed_at, "
            "observed_at, eligible, score, entry_price, features, rejection_reasons "
            "FROM research_opportunities"
        )
        params: tuple[object, ...] = ()
        if since is not None:
            query += " WHERE signal_bar_closed_at >= ?"
            params = (since,)
        query += " ORDER BY signal_bar_closed_at, venue, symbol, direction"
        with self._connect() as connection:
            return [tuple(row) for row in connection.execute(query, params).fetchall()]

    async def is_kill_switch_active(self) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._kill_switch_sync)

    def _kill_switch_sync(self) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM state WHERE key = ?", ("kill_switch",)
            ).fetchone()
        return bool(row and json.loads(row[0]).get("active"))

    async def set_kill_switch(self, active: bool, reason: str) -> None:
        await self.set_state("kill_switch", {"active": active, "reason": reason})

    async def get_state(self, key: str) -> dict[str, object] | None:
        async with self._lock:
            payload = await asyncio.to_thread(self._get_state_sync, key)
        if payload is None:
            return None
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError(f"state value for {key!r} is not an object")
        return decoded

    def _get_state_sync(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row else None

    async def set_state(self, key: str, value: dict[str, object]) -> None:
        payload = json.dumps(value, default=_json_default, separators=(",", ":"))
        now = datetime.now(UTC).isoformat()
        async with self._lock:
            await asyncio.to_thread(self._set_state_sync, key, payload, now)

    def _set_state_sync(self, key: str, payload: str, now: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO state(key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (key, payload, now),
            )

    async def delete_state(self, key: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._delete_state_sync, key)

    def _delete_state_sync(self, key: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM state WHERE key = ?", (key,))

    async def try_acquire_lease(
        self,
        name: str,
        owner_id: str,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        async with self._lock:
            return await asyncio.to_thread(
                self._try_acquire_lease_sync, name, owner_id, now, expires_at
            )

    def _try_acquire_lease_sync(
        self,
        name: str,
        owner_id: str,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO execution_leases(name, owner_id, expires_at, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET owner_id=excluded.owner_id, "
                "expires_at=excluded.expires_at, updated_at=excluded.updated_at "
                "WHERE execution_leases.expires_at <= ? "
                "OR execution_leases.owner_id = excluded.owner_id",
                (
                    name,
                    owner_id,
                    expires_at.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            return cursor.rowcount == 1

    async def release_lease(self, name: str, owner_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._release_lease_sync, name, owner_id)

    def _release_lease_sync(self, name: str, owner_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM execution_leases WHERE name = ? AND owner_id = ?",
                (name, owner_id),
            )

    async def has_order_intent(self, client_order_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._has_order_intent_sync, client_order_id)

    def _has_order_intent_sync(self, client_order_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM order_intents WHERE client_order_id = ?", (client_order_id,)
            ).fetchone()
        return row is not None

    async def record_order_intent(self, intent: OrderIntent) -> None:
        payload = json.dumps(asdict(intent), default=_json_default, separators=(",", ":"))
        async with self._lock:
            await asyncio.to_thread(self._record_intent_sync, intent.client_order_id, payload)

    def _record_intent_sync(self, client_order_id: str, payload: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO order_intents(client_order_id, payload, created_at) VALUES (?, ?, ?)",
                (client_order_id, payload, datetime.now(UTC).isoformat()),
            )

    async def get_order_intent_payload(self, client_order_id: str) -> dict[str, object] | None:
        async with self._lock:
            row = await asyncio.to_thread(self._get_order_intent_sync, client_order_id)
        if row is None:
            return None
        decoded = json.loads(str(row[0]))
        return decoded if isinstance(decoded, dict) else None

    def _get_order_intent_sync(self, client_order_id: str) -> tuple[object, ...] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM order_intents WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
        return tuple(row) if row is not None else None

    async def record_order_result(self, result: OrderResult) -> None:
        payload = json.dumps(asdict(result), default=_json_default, separators=(",", ":"))
        async with self._lock:
            await asyncio.to_thread(self._record_result_sync, result.client_order_id, payload)

    def _record_result_sync(self, client_order_id: str, payload: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO order_results(client_order_id, payload, created_at) VALUES (?, ?, ?)",
                (client_order_id, payload, datetime.now(UTC).isoformat()),
            )

    async def get_latest_order_result(self, client_order_id: str) -> OrderResult | None:
        async with self._lock:
            row = await asyncio.to_thread(self._get_latest_order_result_sync, client_order_id)
        if row is None:
            return None
        decoded = json.loads(str(row[0]))
        if not isinstance(decoded, dict):
            raise ValueError(f"order result {client_order_id!r} must be an object")
        average_price = decoded.get("average_price")
        return OrderResult(
            venue=Venue(str(decoded["venue"])),
            symbol=str(decoded["symbol"]),
            client_order_id=str(decoded["client_order_id"]),
            exchange_order_id=str(decoded["exchange_order_id"]),
            status=OrderStatus(str(decoded["status"])),
            executed_quantity=Decimal(str(decoded["executed_quantity"])),
            average_price=(Decimal(str(average_price)) if average_price is not None else None),
            raw_status=str(decoded["raw_status"]),
        )

    def _get_latest_order_result_sync(self, client_order_id: str) -> tuple[object, ...] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM order_results WHERE client_order_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (client_order_id,),
            ).fetchone()
        return tuple(row) if row is not None else None

    async def list_unresolved_order_intents(self) -> tuple[UnresolvedOrderIntent, ...]:
        async with self._lock:
            rows = await asyncio.to_thread(self._list_unresolved_order_intents_sync)
        unresolved: list[UnresolvedOrderIntent] = []
        for client_order_id, payload, created_at in rows:
            decoded = json.loads(str(payload))
            instrument = decoded.get("instrument") if isinstance(decoded, dict) else None
            instrument_id = instrument.get("id") if isinstance(instrument, dict) else None
            if not isinstance(instrument_id, dict):
                raise ValueError(
                    f"order intent {client_order_id!r} has an invalid instrument identity"
                )
            unresolved.append(
                UnresolvedOrderIntent(
                    instrument_id=InstrumentId(
                        Venue(str(instrument_id["venue"])), str(instrument_id["symbol"])
                    ),
                    client_order_id=str(client_order_id),
                    created_at=datetime.fromisoformat(str(created_at)),
                )
            )
        return tuple(unresolved)

    async def record_execution_lifecycle(self, lifecycle: ExecutionLifecycle) -> None:
        await self.set_state(
            f"execution:{lifecycle.signal_id}",
            {
                "signal_id": str(lifecycle.signal_id),
                "venue": lifecycle.instrument_id.venue.value,
                "symbol": lifecycle.instrument_id.symbol,
                "direction": lifecycle.direction.value,
                "client_order_id": lifecycle.client_order_id,
                "stage": lifecycle.stage.value,
                "updated_at": lifecycle.updated_at.isoformat(),
                "filled_quantity": str(lifecycle.filled_quantity),
                "fill_price": str(lifecycle.fill_price)
                if lifecycle.fill_price is not None
                else None,
                "stop_order_id": lifecycle.stop_order_id,
                "target_order_id": lifecycle.target_order_id,
            },
        )

    async def list_incomplete_execution_lifecycles(
        self,
    ) -> tuple[ExecutionLifecycle, ...]:
        async with self._lock:
            rows = await asyncio.to_thread(self._list_execution_state_rows_sync)
        lifecycles: list[ExecutionLifecycle] = []
        for (payload,) in rows:
            decoded = json.loads(str(payload))
            if not isinstance(decoded, dict):
                raise ValueError("execution lifecycle state must be an object")
            stage = ExecutionStage(str(decoded["stage"]))
            if stage in {ExecutionStage.TRACKED, ExecutionStage.ABORTED}:
                continue
            raw_fill_price = decoded.get("fill_price")
            lifecycles.append(
                ExecutionLifecycle(
                    signal_id=UUID(str(decoded["signal_id"])),
                    instrument_id=InstrumentId(
                        Venue(str(decoded["venue"])), str(decoded["symbol"])
                    ),
                    direction=Direction(str(decoded["direction"])),
                    client_order_id=str(decoded["client_order_id"]),
                    stage=stage,
                    updated_at=datetime.fromisoformat(str(decoded["updated_at"])),
                    filled_quantity=Decimal(str(decoded.get("filled_quantity", "0"))),
                    fill_price=(
                        Decimal(str(raw_fill_price)) if raw_fill_price is not None else None
                    ),
                    stop_order_id=str(decoded.get("stop_order_id", "")),
                    target_order_id=str(decoded.get("target_order_id", "")),
                )
            )
        return tuple(lifecycles)

    def _list_execution_state_rows_sync(self) -> list[tuple[object, ...]]:
        with self._connect() as connection:
            return connection.execute(
                "SELECT value FROM state WHERE key LIKE ? ORDER BY updated_at",
                ("execution:%",),
            ).fetchall()

    def _list_unresolved_order_intents_sync(self) -> list[tuple[object, ...]]:
        with self._connect() as connection:
            return connection.execute(
                "SELECT intent.client_order_id, intent.payload, intent.created_at "
                "FROM order_intents AS intent "
                "WHERE NOT EXISTS ("
                "SELECT 1 FROM order_results AS result "
                "WHERE result.client_order_id = intent.client_order_id"
                ") ORDER BY intent.created_at"
            ).fetchall()

    async def record_trade_open(self, trade: TradeRecord) -> None:
        async with self._lock:
            await asyncio.to_thread(self._record_trade_open_sync, trade)

    def _record_trade_open_sync(self, trade: TradeRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO trades(signal_id, venue, symbol, direction, strategy, quantity, "
                "entry_price, stop_price, target_price, entry_order_id, protection_order_ids, "
                "opened_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(trade.signal_id),
                    trade.instrument_id.venue.value,
                    trade.instrument_id.symbol,
                    trade.direction.value,
                    trade.strategy,
                    str(trade.quantity),
                    str(trade.entry_price),
                    str(trade.stop_price),
                    str(trade.target_price),
                    trade.entry_order_id,
                    json.dumps(trade.protection_order_ids),
                    trade.opened_at.isoformat(),
                    trade.status.value,
                ),
            )

    async def close_trade(
        self,
        signal_id: str,
        *,
        exit_price: Decimal,
        closed_at: datetime,
        gross_realized_pnl: Decimal,
        fees: Decimal,
        exit_reason: str,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._close_trade_sync,
                signal_id,
                exit_price,
                closed_at,
                gross_realized_pnl,
                fees,
                exit_reason,
            )

    def _close_trade_sync(
        self,
        signal_id: str,
        exit_price: Decimal,
        closed_at: datetime,
        gross_realized_pnl: Decimal,
        fees: Decimal,
        exit_reason: str,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE trades SET status = ?, exit_price = ?, closed_at = ?, "
                "gross_realized_pnl = ?, fees = ?, exit_reason = ? "
                "WHERE signal_id = ? AND status = ?",
                (
                    TradeStatus.CLOSED.value,
                    str(exit_price),
                    closed_at.isoformat(),
                    str(gross_realized_pnl),
                    str(fees),
                    exit_reason,
                    signal_id,
                    TradeStatus.OPEN.value,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"open trade {signal_id!r} was not found")

    async def list_open_trades(self) -> tuple[TradeRecord, ...]:
        async with self._lock:
            rows = await asyncio.to_thread(self._list_open_trades_sync)
        return tuple(self._trade_from_row(row) for row in rows)

    def _list_open_trades_sync(self) -> list[tuple[object, ...]]:
        with self._connect() as connection:
            return connection.execute(
                "SELECT signal_id, venue, symbol, direction, strategy, quantity, entry_price, "
                "stop_price, target_price, entry_order_id, protection_order_ids, opened_at, "
                "status, exit_price, closed_at, gross_realized_pnl, fees, exit_reason "
                "FROM trades WHERE status = ? ORDER BY opened_at",
                (TradeStatus.OPEN.value,),
            ).fetchall()

    @staticmethod
    def _trade_from_row(row: tuple[object, ...]) -> TradeRecord:
        return TradeRecord(
            signal_id=UUID(str(row[0])),
            instrument_id=InstrumentId(Venue(str(row[1])), str(row[2])),
            direction=Direction(str(row[3])),
            strategy=str(row[4]),
            quantity=Decimal(str(row[5])),
            entry_price=Decimal(str(row[6])),
            stop_price=Decimal(str(row[7])),
            target_price=Decimal(str(row[8])),
            entry_order_id=str(row[9]),
            protection_order_ids=tuple(json.loads(str(row[10]))),
            opened_at=datetime.fromisoformat(str(row[11])),
            status=TradeStatus(str(row[12])),
            exit_price=Decimal(str(row[13])) if row[13] is not None else None,
            closed_at=datetime.fromisoformat(str(row[14])) if row[14] is not None else None,
            gross_realized_pnl=Decimal(str(row[15])),
            fees=Decimal(str(row[16])),
            exit_reason=str(row[17]),
        )

    _UPSERT_POSITION_MANAGEMENT_SQL = (
        "INSERT INTO position_management("
        "signal_id, venue, symbol, direction, stage, entry_price, initial_stop_price, "
        "initial_quantity, remaining_quantity, current_stop_price, target_price, "
        "best_price, realized_partial_pnl, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(signal_id) DO UPDATE SET "
        "stage = excluded.stage, remaining_quantity = excluded.remaining_quantity, "
        "current_stop_price = excluded.current_stop_price, "
        "target_price = excluded.target_price, best_price = excluded.best_price, "
        "realized_partial_pnl = excluded.realized_partial_pnl, updated_at = excluded.updated_at"
    )
    _SELECT_POSITION_MANAGEMENT_ONE_SQL = (
        "SELECT signal_id, venue, symbol, direction, stage, entry_price, initial_stop_price, "
        "initial_quantity, remaining_quantity, current_stop_price, target_price, "
        "best_price, realized_partial_pnl, updated_at FROM position_management "
        "WHERE signal_id = ?"
    )
    _SELECT_POSITION_MANAGEMENT_ALL_SQL = (
        "SELECT signal_id, venue, symbol, direction, stage, entry_price, initial_stop_price, "
        "initial_quantity, remaining_quantity, current_stop_price, target_price, "
        "best_price, realized_partial_pnl, updated_at FROM position_management "
        "ORDER BY updated_at"
    )

    async def upsert_position_management(self, state: PositionManagementState) -> None:
        async with self._lock:
            await asyncio.to_thread(self._upsert_position_management_sync, state)

    def _upsert_position_management_sync(self, state: PositionManagementState) -> None:
        with self._connect() as connection:
            connection.execute(
                self._UPSERT_POSITION_MANAGEMENT_SQL,
                (
                    str(state.signal_id),
                    state.instrument_id.venue.value,
                    state.instrument_id.symbol,
                    state.direction.value,
                    state.stage.value,
                    str(state.entry_price),
                    str(state.initial_stop_price),
                    str(state.initial_quantity),
                    str(state.remaining_quantity),
                    str(state.current_stop_price),
                    str(state.target_price),
                    str(state.best_price),
                    str(state.realized_partial_pnl),
                    state.updated_at.isoformat(),
                ),
            )

    async def get_position_management(self, signal_id: str) -> PositionManagementState | None:
        async with self._lock:
            row = await asyncio.to_thread(self._get_position_management_sync, signal_id)
        return self._position_management_from_row(row) if row is not None else None

    def _get_position_management_sync(self, signal_id: str) -> tuple[object, ...] | None:
        with self._connect() as connection:
            row = connection.execute(
                self._SELECT_POSITION_MANAGEMENT_ONE_SQL,
                (signal_id,),
            ).fetchone()
        return tuple(row) if row is not None else None

    async def list_position_management(self) -> tuple[PositionManagementState, ...]:
        async with self._lock:
            rows = await asyncio.to_thread(self._list_position_management_sync)
        return tuple(self._position_management_from_row(row) for row in rows)

    def _list_position_management_sync(self) -> list[tuple[object, ...]]:
        with self._connect() as connection:
            return connection.execute(self._SELECT_POSITION_MANAGEMENT_ALL_SQL).fetchall()

    async def delete_position_management(self, signal_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._delete_position_management_sync, signal_id)

    def _delete_position_management_sync(self, signal_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM position_management WHERE signal_id = ?", (signal_id,))

    @staticmethod
    def _position_management_from_row(row: tuple[object, ...]) -> PositionManagementState:
        return PositionManagementState(
            signal_id=UUID(str(row[0])),
            instrument_id=InstrumentId(Venue(str(row[1])), str(row[2])),
            direction=Direction(str(row[3])),
            stage=ManagementStage(str(row[4])),
            entry_price=Decimal(str(row[5])),
            initial_stop_price=Decimal(str(row[6])),
            initial_quantity=Decimal(str(row[7])),
            remaining_quantity=Decimal(str(row[8])),
            current_stop_price=Decimal(str(row[9])),
            target_price=Decimal(str(row[10])),
            best_price=Decimal(str(row[11])),
            realized_partial_pnl=Decimal(str(row[12])),
            updated_at=datetime.fromisoformat(str(row[13])),
        )

    async def realized_pnl_since(self, since: datetime) -> Decimal:
        async with self._lock:
            return await asyncio.to_thread(self._realized_pnl_since_sync, since)

    def _realized_pnl_since_sync(self, since: datetime) -> Decimal:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT gross_realized_pnl, fees FROM trades WHERE status = ? AND closed_at >= ?",
                (TradeStatus.CLOSED.value, since.isoformat()),
            ).fetchall()
        return sum(
            (Decimal(str(gross)) - Decimal(str(fees)) for gross, fees in rows),
            Decimal("0"),
        )

    async def latest_trade_opened_at(self) -> datetime | None:
        async with self._lock:
            value = await asyncio.to_thread(self._latest_trade_opened_at_sync)
        return datetime.fromisoformat(value) if value else None

    def _latest_trade_opened_at_sync(self) -> str | None:
        with self._connect() as connection:
            row = connection.execute("SELECT MAX(opened_at) FROM trades").fetchone()
        return str(row[0]) if row and row[0] is not None else None

    async def record_signal_observation(self, signal: TrackedSignal) -> TrackedSignal:
        async with self._lock:
            row = await asyncio.to_thread(self._record_signal_observation_sync, signal)
        return self._tracked_signal_from_row(row)

    def _record_signal_observation_sync(self, signal: TrackedSignal) -> tuple[object, ...]:
        now = signal.last_seen_at.isoformat()
        with self._connect() as connection:
            connection.execute(
                "UPDATE tracked_signals SET status = ? WHERE status = ? AND expires_at <= ?",
                (
                    TrackedSignalStatus.EXPIRED.value,
                    TrackedSignalStatus.ACTIVE.value,
                    now,
                ),
            )
            existing = connection.execute(
                "SELECT signal_id FROM tracked_signals WHERE venue = ? AND symbol = ? "
                "AND direction = ? AND status = ?",
                (
                    signal.instrument_id.venue.value,
                    signal.instrument_id.symbol,
                    signal.direction.value,
                    TrackedSignalStatus.ACTIVE.value,
                ),
            ).fetchone()
            if existing:
                signal_id = str(existing[0])
                connection.execute(
                    "UPDATE tracked_signals SET last_seen_at = ?, signal_bar_closed_at = ?, "
                    "expires_at = ?, observations = observations + "
                    "CASE WHEN signal_bar_closed_at <> ? THEN 1 ELSE 0 END, "
                    "score = ?, entry_price = ? "
                    "WHERE signal_id = ? AND status = ?",
                    (
                        signal.last_seen_at.isoformat(),
                        signal.signal_bar_closed_at.isoformat(),
                        signal.expires_at.isoformat(),
                        signal.signal_bar_closed_at.isoformat(),
                        str(signal.score),
                        str(signal.entry_price),
                        signal_id,
                        TrackedSignalStatus.ACTIVE.value,
                    ),
                )
            else:
                signal_id = str(signal.signal_id)
                connection.execute(
                    "INSERT INTO tracked_signals(signal_id, venue, symbol, direction, "
                    "first_seen_at, last_seen_at, signal_bar_closed_at, expires_at, observations, "
                    "score, entry_price, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        signal_id,
                        signal.instrument_id.venue.value,
                        signal.instrument_id.symbol,
                        signal.direction.value,
                        signal.first_seen_at.isoformat(),
                        signal.last_seen_at.isoformat(),
                        signal.signal_bar_closed_at.isoformat(),
                        signal.expires_at.isoformat(),
                        signal.observations,
                        str(signal.score),
                        str(signal.entry_price),
                        signal.status.value,
                    ),
                )
            row = connection.execute(
                "SELECT signal_id, venue, symbol, direction, first_seen_at, last_seen_at, "
                "signal_bar_closed_at, expires_at, observations, score, entry_price, status "
                "FROM tracked_signals WHERE signal_id = ?",
                (signal_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("tracked signal disappeared during observation upsert")
        return tuple(row)

    async def expire_signals(self, now: datetime) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._expire_signals_sync, now)

    def _expire_signals_sync(self, now: datetime) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE tracked_signals SET status = ? WHERE status = ? AND expires_at <= ?",
                (
                    TrackedSignalStatus.EXPIRED.value,
                    TrackedSignalStatus.ACTIVE.value,
                    now.isoformat(),
                ),
            )
            return cursor.rowcount

    async def list_active_signals(self, now: datetime) -> tuple[TrackedSignal, ...]:
        async with self._lock:
            rows = await asyncio.to_thread(self._list_active_signals_sync, now)
        return tuple(self._tracked_signal_from_row(row) for row in rows)

    def _list_active_signals_sync(self, now: datetime) -> list[tuple[object, ...]]:
        with self._connect() as connection:
            connection.execute(
                "UPDATE tracked_signals SET status = ? WHERE status = ? AND expires_at <= ?",
                (
                    TrackedSignalStatus.EXPIRED.value,
                    TrackedSignalStatus.ACTIVE.value,
                    now.isoformat(),
                ),
            )
            return connection.execute(
                "SELECT signal_id, venue, symbol, direction, first_seen_at, last_seen_at, "
                "signal_bar_closed_at, expires_at, observations, score, entry_price, status "
                "FROM tracked_signals WHERE status = ? ORDER BY score DESC, first_seen_at",
                (TrackedSignalStatus.ACTIVE.value,),
            ).fetchall()

    async def mark_signal_executed(
        self, instrument_id: InstrumentId, direction: Direction, executed_at: datetime
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._mark_signal_executed_sync, instrument_id, direction, executed_at
            )

    def _mark_signal_executed_sync(
        self, instrument_id: InstrumentId, direction: Direction, executed_at: datetime
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE tracked_signals SET status = ?, last_seen_at = ? "
                "WHERE venue = ? AND symbol = ? AND direction = ? AND status = ?",
                (
                    TrackedSignalStatus.EXECUTED.value,
                    executed_at.isoformat(),
                    instrument_id.venue.value,
                    instrument_id.symbol,
                    direction.value,
                    TrackedSignalStatus.ACTIVE.value,
                ),
            )

    @staticmethod
    def _tracked_signal_from_row(row: tuple[object, ...]) -> TrackedSignal:
        return TrackedSignal(
            signal_id=UUID(str(row[0])),
            instrument_id=InstrumentId(Venue(str(row[1])), str(row[2])),
            direction=Direction(str(row[3])),
            first_seen_at=datetime.fromisoformat(str(row[4])),
            last_seen_at=datetime.fromisoformat(str(row[5])),
            signal_bar_closed_at=datetime.fromisoformat(str(row[6])),
            expires_at=datetime.fromisoformat(str(row[7])),
            observations=int(str(row[8])),
            score=Decimal(str(row[9])),
            entry_price=Decimal(str(row[10])),
            status=TrackedSignalStatus(str(row[11])),
        )

    async def append_event(self, event_type: str, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, default=_json_default, separators=(",", ":"))
        async with self._lock:
            await asyncio.to_thread(self._append_event_sync, event_type, encoded)

    def _append_event_sync(self, event_type: str, payload: str) -> None:
        created_at = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO events(event_type, payload, created_at) VALUES (?, ?, ?)",
                (event_type, payload, created_at.isoformat()),
            )
            connection.execute(
                "DELETE FROM events WHERE created_at < ?",
                ((created_at - self._event_retention).isoformat(),),
            )
