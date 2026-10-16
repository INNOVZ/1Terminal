from __future__ import annotations

import sqlite3
import stat
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

import algotrader.infrastructure.sqlite_state as sqlite_state
from algotrader.domain.models import (
    Direction,
    InstrumentId,
    OrderIntent,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    TrackedSignal,
    TrackedSignalStatus,
    TradeRecord,
    Venue,
)
from algotrader.infrastructure.sqlite_state import LATEST_SCHEMA_VERSION, SqliteStateRepository
from tests.conftest import make_instrument


async def test_repository_closes_every_sqlite_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = 0
    closed = 0
    real_connect = sqlite3.connect

    class TrackedConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def __getattr__(self, name: str) -> object:
            return getattr(self._connection, name)

        def __enter__(self) -> sqlite3.Connection:
            return self._connection.__enter__()

        def __exit__(self, *args: object) -> object:
            return self._connection.__exit__(*args)

        def close(self) -> None:
            nonlocal closed
            closed += 1
            self._connection.close()

    def tracked_connect(*args: object, **kwargs: object) -> TrackedConnection:
        nonlocal opened
        opened += 1
        return TrackedConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(sqlite_state.sqlite3, "connect", tracked_connect)
    repository = SqliteStateRepository(tmp_path / "state.sqlite3")

    await repository.initialize()
    await repository.set_state("probe", {"ready": True})
    assert await repository.get_state("probe") == {"ready": True}

    assert opened > 0
    assert closed == opened


async def test_initialize_migrates_a_version_one_database(tmp_path: Path) -> None:
    state_path = tmp_path / "state.sqlite3"
    with closing(sqlite3.connect(state_path)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                INSERT INTO schema_version(version, applied_at) VALUES (1, '2026-01-01');
                CREATE TABLE trades (
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
                    closed_at TEXT
                );
                CREATE TABLE position_management (
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
                    updated_at TEXT NOT NULL
                );
            """
        )

    await SqliteStateRepository(state_path).initialize()

    with closing(sqlite3.connect(state_path)) as connection:
        trade_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(trades)").fetchall()
        }
        management_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(position_management)").fetchall()
        }
        version = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
        research_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='research_opportunities'"
        ).fetchone()

    assert {"gross_realized_pnl", "fees", "exit_reason"} <= trade_columns
    assert "realized_partial_pnl" in management_columns
    assert version == (LATEST_SCHEMA_VERSION,)
    assert research_table == ("research_opportunities",)


async def test_order_intent_is_durable_and_unique(tmp_path: Path) -> None:
    repository = SqliteStateRepository(tmp_path / "state.sqlite3")
    await repository.initialize()
    intent = OrderIntent(
        instrument=make_instrument(),
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        client_order_id="atl-stable-id",
        direction=Direction.LONG,
    )
    await repository.record_order_intent(intent)
    assert await repository.has_order_intent(intent.client_order_id)
    with pytest.raises(sqlite3.IntegrityError):
        await repository.record_order_intent(intent)


async def test_unresolved_order_intent_is_cleared_only_by_a_recorded_result(
    tmp_path: Path,
) -> None:
    repository = SqliteStateRepository(tmp_path / "state.sqlite3")
    await repository.initialize()
    intent = OrderIntent(
        instrument=make_instrument(),
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        client_order_id="atl-unresolved",
        direction=Direction.LONG,
        price=Decimal("100"),
    )
    await repository.record_order_intent(intent)

    unresolved = await repository.list_unresolved_order_intents()

    assert len(unresolved) == 1
    assert unresolved[0].instrument_id == intent.instrument.id
    assert unresolved[0].client_order_id == intent.client_order_id

    result = OrderResult(
        venue=intent.instrument.id.venue,
        symbol=intent.instrument.id.symbol,
        client_order_id=intent.client_order_id,
        exchange_order_id="exchange-1",
        status=OrderStatus.EXPIRED,
        executed_quantity=Decimal("0"),
        average_price=None,
        raw_status="EXPIRED",
    )
    await repository.record_order_result(result)

    assert await repository.list_unresolved_order_intents() == ()
    assert await repository.get_latest_order_result(intent.client_order_id) == result


async def test_state_database_permissions_are_owner_only(tmp_path: Path) -> None:
    state_path = tmp_path / "state.sqlite3"
    repository = SqliteStateRepository(state_path)
    await repository.initialize()

    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


async def test_execution_lease_is_atomic_across_repository_instances(tmp_path: Path) -> None:
    state_path = tmp_path / "state.sqlite3"
    first = SqliteStateRepository(state_path)
    second = SqliteStateRepository(state_path)
    await first.initialize()
    await second.initialize()
    now = datetime(2026, 8, 4, 13, tzinfo=UTC)

    assert await first.try_acquire_lease(
        "trading-cycle", "worker-1", now, now + timedelta(minutes=5)
    )
    assert not await second.try_acquire_lease(
        "trading-cycle", "worker-2", now, now + timedelta(minutes=5)
    )
    assert await second.try_acquire_lease(
        "trading-cycle",
        "worker-2",
        now + timedelta(minutes=5),
        now + timedelta(minutes=10),
    )

    await first.release_lease("trading-cycle", "worker-1")
    assert not await first.try_acquire_lease(
        "trading-cycle",
        "worker-3",
        now + timedelta(minutes=6),
        now + timedelta(minutes=11),
    )
    await second.release_lease("trading-cycle", "worker-2")
    assert await first.try_acquire_lease(
        "trading-cycle",
        "worker-3",
        now + timedelta(minutes=6),
        now + timedelta(minutes=11),
    )


async def test_generic_state_round_trip(tmp_path: Path) -> None:
    repository = SqliteStateRepository(tmp_path / "state.sqlite3")
    await repository.initialize()
    await repository.set_state("risk", {"equity": "100.5"})
    assert await repository.get_state("risk") == {"equity": "100.5"}
    await repository.delete_state("risk")
    assert await repository.get_state("risk") is None


async def test_trade_lifecycle_and_realized_pnl_are_durable(tmp_path: Path) -> None:
    repository = SqliteStateRepository(tmp_path / "state.sqlite3")
    await repository.initialize()
    signal_id = uuid4()
    opened_at = datetime(2026, 8, 3, 12, tzinfo=UTC)
    trade = TradeRecord(
        signal_id=signal_id,
        instrument_id=make_instrument().id,
        direction=Direction.LONG,
        strategy="test",
        quantity=Decimal("2"),
        entry_price=Decimal("100"),
        stop_price=Decimal("95"),
        target_price=Decimal("110"),
        entry_order_id="entry-1",
        protection_order_ids=("stop-1", "target-1"),
        opened_at=opened_at,
    )

    await repository.record_trade_open(trade)
    assert await repository.list_open_trades() == (trade,)

    await repository.close_trade(
        str(signal_id),
        exit_price=Decimal("110"),
        closed_at=opened_at + timedelta(hours=4),
        gross_realized_pnl=Decimal("20"),
        fees=Decimal("1.5"),
        exit_reason="target",
    )

    assert await repository.list_open_trades() == ()
    assert await repository.realized_pnl_since(opened_at) == Decimal("18.5")


async def test_signal_observations_are_durable_unique_and_expire(tmp_path: Path) -> None:
    repository = SqliteStateRepository(tmp_path / "state.sqlite3")
    await repository.initialize()
    observed_at = datetime(2026, 8, 4, 13, tzinfo=UTC)
    first = TrackedSignal(
        signal_id=uuid4(),
        instrument_id=InstrumentId(Venue.BINANCE_USDM, "GUSDT"),
        direction=Direction.LONG,
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        signal_bar_closed_at=observed_at,
        expires_at=observed_at + timedelta(hours=2),
        observations=1,
        score=Decimal("100"),
        entry_price=Decimal("0.003655"),
    )

    stored = await repository.record_signal_observation(first)
    duplicate_same_bar = await repository.record_signal_observation(first)
    refreshed = await repository.record_signal_observation(
        TrackedSignal(
            signal_id=uuid4(),
            instrument_id=first.instrument_id,
            direction=first.direction,
            first_seen_at=observed_at + timedelta(hours=1),
            last_seen_at=observed_at + timedelta(hours=1),
            signal_bar_closed_at=observed_at + timedelta(hours=1),
            expires_at=observed_at + timedelta(hours=3),
            observations=1,
            score=Decimal("95"),
            entry_price=Decimal("0.00366"),
        )
    )

    assert refreshed.signal_id == stored.signal_id
    assert duplicate_same_bar.observations == 1
    assert refreshed.first_seen_at == observed_at
    assert refreshed.observations == 2
    assert refreshed.score == Decimal("95")
    assert await repository.list_active_signals(observed_at + timedelta(hours=2)) == (refreshed,)
    assert await repository.expire_signals(observed_at + timedelta(hours=3)) == 1
    assert await repository.list_active_signals(observed_at + timedelta(hours=3)) == ()


async def test_executed_signal_is_removed_from_active_set(tmp_path: Path) -> None:
    repository = SqliteStateRepository(tmp_path / "state.sqlite3")
    await repository.initialize()
    observed_at = datetime(2026, 8, 4, 13, tzinfo=UTC)
    signal = TrackedSignal(
        signal_id=uuid4(),
        instrument_id=InstrumentId(Venue.BINANCE_USDM, "GUSDT"),
        direction=Direction.LONG,
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        signal_bar_closed_at=observed_at,
        expires_at=observed_at + timedelta(hours=2),
        observations=1,
        score=Decimal("100"),
        entry_price=Decimal("0.003655"),
    )
    await repository.record_signal_observation(signal)

    await repository.mark_signal_executed(
        signal.instrument_id, signal.direction, observed_at + timedelta(minutes=1)
    )

    assert await repository.list_active_signals(observed_at + timedelta(minutes=1)) == ()
    with closing(sqlite3.connect(tmp_path / "state.sqlite3")) as connection, connection:
        status = connection.execute(
            "SELECT status FROM tracked_signals WHERE signal_id = ?", (str(signal.signal_id),)
        ).fetchone()
    assert status == (TrackedSignalStatus.EXECUTED.value,)
