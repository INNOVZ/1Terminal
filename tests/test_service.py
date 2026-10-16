from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from algotrader.application.service import CycleReason, TradingSystem, VenueContext
from algotrader.config import Settings
from algotrader.domain.errors import SafetyInterlockError, TradeRejected, VenueError
from algotrader.domain.models import (
    AccountSnapshot,
    Candidate,
    Direction,
    ExecutionLifecycle,
    ExecutionStage,
    Instrument,
    InstrumentId,
    MarketKind,
    MomentumState,
    OrderIntent,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    ProtectionOrder,
    ProtectionRole,
    ProtectionSet,
    TradeRecord,
    TradeSignal,
    Venue,
)
from algotrader.infrastructure.sqlite_state import SqliteStateRepository
from tests.conftest import make_instrument


def _trade() -> TradeRecord:
    return TradeRecord(
        signal_id=uuid4(),
        instrument_id=InstrumentId(Venue.BINANCE_USDM, "BTCUSDT"),
        direction=Direction.LONG,
        strategy="test",
        quantity=Decimal("0.01"),
        entry_price=Decimal("100"),
        stop_price=Decimal("95"),
        target_price=Decimal("110"),
        entry_order_id="entry-1",
        protection_order_ids=("stop-1", "target-1"),
        opened_at=datetime(2026, 8, 4, tzinfo=UTC),
    )


def test_reconciliation_identifies_tracked_trade_missing_at_exchange() -> None:
    trade = _trade()
    account = AccountSnapshot(
        venue=Venue.BINANCE_USDM,
        equity=Decimal("100"),
        available_balance=Decimal("100"),
        realized_pnl_today=Decimal("0"),
        peak_equity=Decimal("100"),
    )

    missing = TradingSystem._missing_tracked_trades((trade,), (account,))

    assert missing == (trade,)


def test_reconciliation_accepts_matching_symbol_and_direction() -> None:
    trade = _trade()
    position = Position(
        instrument_id=trade.instrument_id,
        direction=trade.direction,
        quantity=trade.quantity,
        entry_price=trade.entry_price,
        mark_price=Decimal("102"),
        unrealized_pnl=Decimal("0.02"),
    )
    account = AccountSnapshot(
        venue=Venue.BINANCE_USDM,
        equity=Decimal("100"),
        available_balance=Decimal("66"),
        realized_pnl_today=Decimal("0"),
        peak_equity=Decimal("100"),
        positions=(position,),
    )

    assert TradingSystem._missing_tracked_trades((trade,), (account,)) == ()


def test_reconciliation_detects_exchange_position_missing_from_local_state() -> None:
    trade = _trade()
    position = Position(
        instrument_id=trade.instrument_id,
        direction=trade.direction,
        quantity=trade.quantity,
        entry_price=trade.entry_price,
        mark_price=trade.entry_price,
        unrealized_pnl=Decimal("0"),
    )
    account = _empty_account()
    account = replace(account, positions=(position,))

    assert TradingSystem._untracked_exchange_positions((), (account,)) == (position,)


class StaticMarketData:
    venue = Venue.BINANCE_USDM


class StaticExecution:
    venue = Venue.BINANCE_USDM

    def __init__(self, account: AccountSnapshot) -> None:
        self._account = account

    async def account_snapshot(self) -> AccountSnapshot:
        return self._account

    async def query_order(
        self, instrument_id: InstrumentId, client_order_id: str
    ) -> OrderResult | None:
        del instrument_id, client_order_id
        return None


def _empty_account() -> AccountSnapshot:
    return AccountSnapshot(
        venue=Venue.BINANCE_USDM,
        equity=Decimal("100000"),
        available_balance=Decimal("100000"),
        realized_pnl_today=Decimal("0"),
        peak_equity=Decimal("100000"),
    )


class RankedScreener:
    def __init__(
        self,
        candidates: list[Candidate],
        revalidated_candidates: list[Candidate] | None = None,
        strategy_profile: str = "legacy_momentum_v1",
    ) -> None:
        self._candidates = candidates
        self._revalidated_candidates = revalidated_candidates
        self.strategy_profile = strategy_profile

    async def screen(self, market_data: object) -> list[Candidate]:
        del market_data
        return self._candidates

    async def screen_instrument(self, market_data: object, instrument: object) -> list[Candidate]:
        del market_data
        candidates = (
            self._candidates
            if self._revalidated_candidates is None
            else self._revalidated_candidates
        )
        return [candidate for candidate in candidates if candidate.instrument == instrument]


class RejectFirstStrategy:
    def __init__(self) -> None:
        self.attempted: list[str] = []

    def signal(self, candidate: Candidate) -> TradeSignal:
        self.attempted.append(candidate.instrument.id.symbol)
        if candidate.instrument.id.symbol == "FIRSTUSDT":
            raise ValueError("insufficient reward/risk")
        return TradeSignal(
            instrument=candidate.instrument,
            direction=candidate.direction,
            score=candidate.score,
            entry_price=candidate.entry_price,
            signal_bar_close=candidate.signal_bar_close,
            atr=candidate.atr,
            stop_price=Decimal("95"),
            target_price=Decimal("110"),
            strategy="test",
            timeframe="1h/4h",
            momentum_state=candidate.momentum_state,
            rationale=candidate.reasons,
        )


class RecordingCoordinator:
    def __init__(self) -> None:
        self.signal: TradeSignal | None = None

    async def execute(self, signal: TradeSignal) -> object:
        self.signal = signal
        return signal


def _candidate(symbol: str, score: str) -> Candidate:
    return Candidate(
        instrument=make_instrument(
            venue=Venue.BINANCE_USDM,
            market_kind=MarketKind.FUTURES,
            symbol=symbol,
        ),
        direction=Direction.LONG,
        score=Decimal(score),
        entry_price=Decimal("100"),
        signal_bar_close=Decimal("100"),
        atr=Decimal("1"),
        spread_bps=Decimal("1"),
        quote_volume_24h=Decimal("1000000"),
        momentum_state=MomentumState.GAINING,
        reasons=("trend",),
    )


async def test_cycle_considers_next_candidate_after_bracket_rejection(
    settings: Settings,
) -> None:
    repository = SqliteStateRepository(settings.state_db_path)
    await repository.initialize()
    strategy = RejectFirstStrategy()
    coordinator = RecordingCoordinator()
    context = VenueContext(
        market_data=StaticMarketData(),  # type: ignore[arg-type]
        execution=StaticExecution(_empty_account()),  # type: ignore[arg-type]
        coordinator=coordinator,  # type: ignore[arg-type]
    )
    system = TradingSystem(
        contexts=(context,),
        screener=RankedScreener(  # type: ignore[arg-type]
            [_candidate("FIRSTUSDT", "100"), _candidate("SECONDUSDT", "90")]
        ),
        strategy=strategy,  # type: ignore[arg-type]
        repository=repository,
    )

    result = await system.run_cycle()

    assert result.reason is CycleReason.EXECUTED
    assert result.executed
    assert strategy.attempted == ["FIRSTUSDT", "SECONDUSDT"]
    assert coordinator.signal is not None
    assert coordinator.signal.instrument.id.symbol == "SECONDUSDT"


async def test_discovery_persists_observations_and_expires_stale_signal(
    settings: Settings,
) -> None:
    repository = SqliteStateRepository(settings.state_db_path)
    await repository.initialize()
    now = [datetime(2026, 8, 4, 13, tzinfo=UTC)]
    candidate = replace(
        _candidate("GUSDT", "100"),
        evaluated_at=now[0],
        signal_bar_closed_at=now[0],
    )
    screener = RankedScreener([candidate])
    system = TradingSystem(
        contexts=(
            VenueContext(
                market_data=StaticMarketData(),  # type: ignore[arg-type]
                execution=StaticExecution(_empty_account()),  # type: ignore[arg-type]
                coordinator=None,  # type: ignore[arg-type]
            ),
        ),
        screener=screener,  # type: ignore[arg-type]
        strategy=None,  # type: ignore[arg-type]
        repository=repository,
        signal_expiry_bars=2,
        clock=lambda: now[0],
    )

    await system.discover_signals()
    now[0] += timedelta(hours=1)
    screener._candidates = [
        replace(
            candidate,
            evaluated_at=now[0],
            signal_bar_closed_at=now[0],
        )
    ]
    await system.discover_signals()

    active = await system.active_signals()
    assert len(active) == 1
    assert active[0].observations == 2
    assert active[0].first_seen_at == datetime(2026, 8, 4, 13, tzinfo=UTC)
    assert active[0].expires_at == datetime(2026, 8, 4, 16, tzinfo=UTC)

    screener._candidates = []
    now[0] = datetime(2026, 8, 4, 16, tzinfo=UTC)
    await system.discover_signals()

    assert await system.active_signals() == ()


@pytest.mark.parametrize("profile", ["empirical_tsmom_v5", "layered_tsmom_v6"])
async def test_signal_expiry_uses_the_profiles_decision_bar_duration(
    settings: Settings, profile: str
) -> None:
    repository = SqliteStateRepository(settings.state_db_path)
    await repository.initialize()
    now = datetime(2026, 8, 4, 12, tzinfo=UTC)
    candidate = replace(
        _candidate("GUSDT", "100"),
        strategy_profile=profile,
        evaluated_at=now,
        signal_bar_closed_at=now,
    )
    system = TradingSystem(
        contexts=(),
        screener=RankedScreener([candidate], strategy_profile=profile),  # type: ignore[arg-type]
        strategy=None,  # type: ignore[arg-type]
        repository=repository,
        signal_expiry_bars=2,
        clock=lambda: now,
    )

    tracked = system._with_expiry(candidate)

    assert tracked.expires_at == now + timedelta(hours=8)


async def test_cycle_discovers_signal_but_respects_execution_cooldown(
    settings: Settings,
) -> None:
    repository = SqliteStateRepository(settings.state_db_path)
    await repository.initialize()
    now = datetime(2026, 8, 4, 13, tzinfo=UTC)
    previous = replace(_trade(), opened_at=now - timedelta(hours=1))
    await repository.record_trade_open(previous)
    await repository.close_trade(
        str(previous.signal_id),
        exit_price=Decimal("101"),
        closed_at=now - timedelta(minutes=30),
        gross_realized_pnl=Decimal("0.01"),
        fees=Decimal("0"),
        exit_reason="test",
    )
    candidate = replace(
        _candidate("GUSDT", "100"),
        evaluated_at=now,
        signal_bar_closed_at=now,
    )
    coordinator = RecordingCoordinator()
    system = TradingSystem(
        contexts=(
            VenueContext(
                market_data=StaticMarketData(),  # type: ignore[arg-type]
                execution=StaticExecution(_empty_account()),  # type: ignore[arg-type]
                coordinator=coordinator,  # type: ignore[arg-type]
            ),
        ),
        screener=RankedScreener([candidate]),  # type: ignore[arg-type]
        strategy=RejectFirstStrategy(),  # type: ignore[arg-type]
        repository=repository,
        execution_cooldown_hours=8,
        clock=lambda: now,
    )

    result = await system.run_cycle()

    assert result.reason is CycleReason.COOLDOWN
    assert not result.executed
    assert coordinator.signal is None
    assert len(await system.active_signals()) == 1


async def test_cycle_refuses_candidate_that_fails_fresh_revalidation(
    settings: Settings,
) -> None:
    repository = SqliteStateRepository(settings.state_db_path)
    await repository.initialize()
    now = datetime(2026, 8, 4, 13, tzinfo=UTC)
    candidate = replace(
        _candidate("GUSDT", "100"),
        evaluated_at=now,
        signal_bar_closed_at=now,
    )
    coordinator = RecordingCoordinator()
    system = TradingSystem(
        contexts=(
            VenueContext(
                market_data=StaticMarketData(),  # type: ignore[arg-type]
                execution=StaticExecution(_empty_account()),  # type: ignore[arg-type]
                coordinator=coordinator,  # type: ignore[arg-type]
            ),
        ),
        screener=RankedScreener(  # type: ignore[arg-type]
            [candidate], revalidated_candidates=[]
        ),
        strategy=RejectFirstStrategy(),  # type: ignore[arg-type]
        repository=repository,
        clock=lambda: now,
    )

    result = await system.run_cycle()

    assert result.reason is CycleReason.REVALIDATION_FAILED
    assert not result.executed
    assert coordinator.signal is None
    assert len(await system.active_signals()) == 1


async def test_cycle_halts_on_order_intent_without_confirmed_result(
    settings: Settings,
) -> None:
    repository = SqliteStateRepository(settings.state_db_path)
    await repository.initialize()
    instrument = make_instrument(
        venue=Venue.BINANCE_USDM,
        market_kind=MarketKind.FUTURES,
        symbol="GUSDT",
    )
    await repository.record_order_intent(
        OrderIntent(
            instrument=instrument,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            client_order_id="atl-crash-window",
            direction=Direction.LONG,
            price=Decimal("0.0036"),
        )
    )
    account = AccountSnapshot(
        venue=Venue.BINANCE_USDM,
        equity=Decimal("100"),
        available_balance=Decimal("100"),
        realized_pnl_today=Decimal("0"),
        peak_equity=Decimal("100"),
    )
    system = TradingSystem(
        contexts=(
            VenueContext(
                market_data=StaticMarketData(),  # type: ignore[arg-type]
                execution=StaticExecution(account),  # type: ignore[arg-type]
                coordinator=None,  # type: ignore[arg-type]
            ),
        ),
        screener=RankedScreener([]),  # type: ignore[arg-type]
        strategy=None,  # type: ignore[arg-type]
        repository=repository,
    )

    result = await system.run_cycle()

    assert result.reason is CycleReason.UNRESOLVED_ORDER
    assert not result.executed
    assert await repository.is_kill_switch_active()


async def test_cycle_halts_on_filled_but_untracked_execution_lifecycle(
    settings: Settings,
) -> None:
    repository = SqliteStateRepository(settings.state_db_path)
    await repository.initialize()
    instrument = make_instrument(
        venue=Venue.BINANCE_USDM,
        market_kind=MarketKind.FUTURES,
        symbol="GUSDT",
    )
    await repository.record_execution_lifecycle(
        ExecutionLifecycle(
            signal_id=uuid4(),
            instrument_id=instrument.id,
            direction=Direction.LONG,
            client_order_id="atl-filled-crash",
            stage=ExecutionStage.ENTRY_FILLED,
            updated_at=datetime.now(UTC),
            filled_quantity=Decimal("1"),
            fill_price=Decimal("0.0036"),
        )
    )
    system = TradingSystem(
        contexts=(
            VenueContext(
                market_data=StaticMarketData(),  # type: ignore[arg-type]
                execution=StaticExecution(_empty_account()),  # type: ignore[arg-type]
                coordinator=None,  # type: ignore[arg-type]
            ),
        ),
        screener=RankedScreener([]),  # type: ignore[arg-type]
        strategy=None,  # type: ignore[arg-type]
        repository=repository,
    )

    result = await system.run_cycle()

    assert result.reason is CycleReason.INCOMPLETE_EXECUTION
    assert await repository.is_kill_switch_active()

    with pytest.raises(SafetyInterlockError, match="cannot be cleared"):
        await system.set_kill_switch(False, "operator requested clear")


async def test_supervised_close_reconciliation_computes_pnl_and_keeps_halt(
    settings: Settings,
) -> None:
    repository = SqliteStateRepository(settings.state_db_path)
    await repository.initialize()
    trade = _trade()
    await repository.record_trade_open(trade)
    await repository.set_kill_switch(True, "position reconciliation required")
    account = AccountSnapshot(
        venue=Venue.BINANCE_USDM,
        equity=Decimal("100"),
        available_balance=Decimal("100"),
        realized_pnl_today=Decimal("0"),
        peak_equity=Decimal("100"),
    )
    context = VenueContext(
        market_data=StaticMarketData(),  # type: ignore[arg-type]
        execution=StaticExecution(account),  # type: ignore[arg-type]
        coordinator=None,  # type: ignore[arg-type]
    )
    system = TradingSystem(
        contexts=(context,),
        screener=None,  # type: ignore[arg-type]
        strategy=None,  # type: ignore[arg-type]
        repository=repository,
    )

    await system.reconcile_trade_close(
        str(trade.signal_id),
        exit_price=Decimal("110"),
        fees=Decimal("0.05"),
        exit_reason="target",
    )

    assert await repository.list_open_trades() == ()
    assert await repository.realized_pnl_since(datetime(2026, 8, 4, tzinfo=UTC)) == Decimal("0.05")
    assert await repository.is_kill_switch_active()


async def test_supervised_close_reconciliation_refuses_an_open_exchange_position(
    settings: Settings,
) -> None:
    repository = SqliteStateRepository(settings.state_db_path)
    await repository.initialize()
    trade = _trade()
    await repository.record_trade_open(trade)
    position = Position(
        instrument_id=trade.instrument_id,
        direction=trade.direction,
        quantity=trade.quantity,
        entry_price=trade.entry_price,
        mark_price=trade.entry_price,
        unrealized_pnl=Decimal("0"),
    )
    account = AccountSnapshot(
        venue=Venue.BINANCE_USDM,
        equity=Decimal("100"),
        available_balance=Decimal("66"),
        realized_pnl_today=Decimal("0"),
        peak_equity=Decimal("100"),
        positions=(position,),
    )
    system = TradingSystem(
        contexts=(
            VenueContext(
                market_data=StaticMarketData(),  # type: ignore[arg-type]
                execution=StaticExecution(account),  # type: ignore[arg-type]
                coordinator=None,  # type: ignore[arg-type]
            ),
        ),
        screener=None,  # type: ignore[arg-type]
        strategy=None,  # type: ignore[arg-type]
        repository=repository,
    )

    with pytest.raises(SafetyInterlockError, match="still open"):
        await system.reconcile_trade_close(
            str(trade.signal_id),
            exit_price=Decimal("110"),
            fees=Decimal("0.05"),
            exit_reason="target",
        )


class NeverFillsCoordinator:
    """Simulates the most common real outcome: a capped IOC limit that does not fill."""

    def __init__(self) -> None:
        self.attempts = 0

    async def execute(self, signal: TradeSignal) -> object:
        del signal
        self.attempts += 1
        raise TradeRejected("entry did not fill: EXPIRED")


class RejectThenFillCoordinator:
    def __init__(self) -> None:
        self.attempts = 0
        self.signal: TradeSignal | None = None

    async def execute(self, signal: TradeSignal) -> object:
        self.attempts += 1
        if self.attempts == 1:
            raise TradeRejected("entry did not fill: EXPIRED")
        self.signal = signal
        return signal


async def test_unfilled_entry_advances_to_the_next_candidate(settings: Settings) -> None:
    repository = SqliteStateRepository(settings.state_db_path)
    await repository.initialize()
    coordinator = RejectThenFillCoordinator()
    strategy = RejectFirstStrategy()
    context = VenueContext(
        market_data=StaticMarketData(),  # type: ignore[arg-type]
        execution=StaticExecution(_empty_account()),  # type: ignore[arg-type]
        coordinator=coordinator,  # type: ignore[arg-type]
    )
    system = TradingSystem(
        contexts=(context,),
        screener=RankedScreener(  # type: ignore[arg-type]
            [_candidate("SECONDUSDT", "100"), _candidate("THIRDUSDT", "90")]
        ),
        strategy=strategy,  # type: ignore[arg-type]
        repository=repository,
    )

    result = await system.run_cycle()

    assert coordinator.attempts == 2, "a no-fill must not end the candidate loop"
    assert result.reason is CycleReason.EXECUTED
    assert not await repository.is_kill_switch_active()


async def test_repeated_no_fills_never_trip_the_kill_switch(settings: Settings) -> None:
    """Regression: three unfilled IOC entries used to halt the system durably.

    ``coordinator.execute`` was unwrapped, so a routine no-fill propagated to the scheduler,
    counted as a consecutive cycle failure, and activated the kill switch on the third --
    requiring manual operator clearing for an outcome that means nothing is wrong.
    """
    repository = SqliteStateRepository(settings.state_db_path)
    await repository.initialize()
    coordinator = NeverFillsCoordinator()
    strategy = RejectFirstStrategy()
    context = VenueContext(
        market_data=StaticMarketData(),  # type: ignore[arg-type]
        execution=StaticExecution(_empty_account()),  # type: ignore[arg-type]
        coordinator=coordinator,  # type: ignore[arg-type]
    )
    system = TradingSystem(
        contexts=(context,),
        screener=RankedScreener(  # type: ignore[arg-type]
            [_candidate("SECONDUSDT", "100")]
        ),
        strategy=strategy,  # type: ignore[arg-type]
        repository=repository,
    )

    for _ in range(3):
        # Must not raise: anything that escapes here is counted as a scheduler failure.
        result = await system.run_cycle()
        assert result.reason is not CycleReason.EXECUTED

    assert coordinator.attempts == 3
    assert not await repository.is_kill_switch_active()


def _spot_candidate(base: str, score: str, direction: Direction = Direction.LONG) -> Candidate:
    return replace(
        _candidate(f"{base}USDT", score),
        instrument=make_instrument(
            venue=Venue.BINANCE_SPOT, market_kind=MarketKind.SPOT, symbol=f"{base}USDT"
        ),
        direction=direction,
    )


def _futures_candidate(base: str, score: str, direction: Direction = Direction.LONG) -> Candidate:
    return replace(
        _candidate(f"{base}USDT", score),
        instrument=make_instrument(
            venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol=f"{base}USDT"
        ),
        direction=direction,
    )


def test_dual_listed_coin_does_not_get_two_long_slots() -> None:
    """Regression: Spot and USD-M both list BTC, so longs got two shots and shorts one."""
    deduped = TradingSystem._deduplicate_across_venues(
        [
            _spot_candidate("BTC", "100"),
            _futures_candidate("BTC", "95"),
            _futures_candidate("BTC", "90", Direction.SHORT),
        ]
    )

    longs = [c for c in deduped if c.direction is Direction.LONG]
    shorts = [c for c in deduped if c.direction is Direction.SHORT]
    assert len(longs) == 1, "one coin, one long opportunity"
    assert len(shorts) == 1, "the short must survive deduplication"


def test_deduplication_prefers_the_short_capable_venue() -> None:
    deduped = TradingSystem._deduplicate_across_venues(
        [_spot_candidate("BTC", "100"), _futures_candidate("BTC", "95")]
    )

    assert len(deduped) == 1
    # Futures wins on lower score: it can express both directions, so keeping it leaves the
    # instrument eligible if the trend inverts.
    assert deduped[0].instrument.market_kind is MarketKind.FUTURES


def test_deduplication_keeps_distinct_coins() -> None:
    deduped = TradingSystem._deduplicate_across_venues(
        [_futures_candidate("BTC", "100"), _futures_candidate("ETH", "90")]
    )
    assert len(deduped) == 2


def test_deduplication_keeps_the_better_candidate_within_one_venue() -> None:
    deduped = TradingSystem._deduplicate_across_venues(
        [_futures_candidate("BTC", "80"), _futures_candidate("BTC", "95")]
    )
    assert len(deduped) == 1
    assert deduped[0].score == Decimal("95")


class UniverseScreener:
    """Returns a fixed universe, tracking how many full passes were requested."""

    def __init__(self, candidates: list[Candidate]) -> None:
        self._candidates = candidates
        self.passes = 0

    async def screen(
        self, market_data: object, *, include_rejected: bool = False
    ) -> list[Candidate]:
        del market_data
        self.passes += 1
        if include_rejected:
            return self._candidates
        return [c for c in self._candidates if c.score >= Decimal("70")]

    @property
    def minimum_score(self) -> Decimal:
        return Decimal("70")


def _screening_system(screener: UniverseScreener) -> TradingSystem:
    context = VenueContext(
        market_data=StaticMarketData(),  # type: ignore[arg-type]
        execution=StaticExecution(_empty_account()),  # type: ignore[arg-type]
        coordinator=RecordingCoordinator(),  # type: ignore[arg-type]
    )
    return TradingSystem(
        contexts=(context,),
        screener=screener,  # type: ignore[arg-type]
        strategy=RejectFirstStrategy(),  # type: ignore[arg-type]
        repository=None,  # type: ignore[arg-type]
    )


async def test_screen_with_diagnostics_uses_a_single_universe_pass() -> None:
    """A full pass is ~160 venue requests and there is no rate-limit backoff."""
    screener = UniverseScreener(
        [_candidate("AAAUSDT", "90"), replace(_candidate("BBBUSDT", "0"), reasons=())]
    )
    await _screening_system(screener).screen_with_diagnostics()
    assert screener.passes == 1, "must not screen twice to report both results"


async def test_screen_with_diagnostics_agrees_with_plain_screen() -> None:
    """The eligible set must not drift from what the trading cycle would act on."""
    universe = [
        _candidate("AAAUSDT", "90"),
        _candidate("BBBUSDT", "75"),
        replace(_candidate("CCCUSDT", "0"), reasons=("entry blocked: too close to resistance",)),
    ]
    eligible, diagnostics = await _screening_system(
        UniverseScreener(universe)
    ).screen_with_diagnostics()

    assert {c.instrument.id.symbol for c in eligible} == {"AAAUSDT", "BBBUSDT"}
    assert [c.score for c in eligible] == sorted([c.score for c in eligible], reverse=True), (
        "eligible candidates stay ranked"
    )
    long_summary = diagnostics["long"]
    assert isinstance(long_summary, dict)
    assert long_summary["evaluated"] == 3
    assert long_summary["blocking_reasons"] == {"too close to resistance": 1}


async def test_both_screen_paths_stamp_expiry_identically() -> None:
    """Regression: the diagnostics path skipped `_with_expiry`, so `expires_at` was null.

    An operator reading `atlas screen` must see the same objects the cycle would act on; a
    candidate with no expiry looks valid indefinitely.
    """
    universe = [_candidate("AAAUSDT", "90")]
    system = _screening_system(UniverseScreener(list(universe)))
    plain = await system.screen()
    eligible, _ = await _screening_system(
        UniverseScreener(list(universe))
    ).screen_with_diagnostics()

    assert plain[0].expires_at is not None
    assert eligible[0].expires_at is not None
    assert plain[0].signal_bar_closed_at == eligible[0].signal_bar_closed_at


class LifecycleExecution(StaticExecution):
    """Execution port whose venue view of an order can be scripted."""

    def __init__(self, account: AccountSnapshot, result: OrderResult | None) -> None:
        super().__init__(account)
        self._result = result

    async def query_order(
        self, instrument_id: InstrumentId, client_order_id: str
    ) -> OrderResult | None:
        del instrument_id, client_order_id
        return self._result


def _order_result(status: OrderStatus, quantity: str = "0") -> OrderResult:
    return OrderResult(
        venue=Venue.BINANCE_USDM,
        symbol="BTCUSDT",
        client_order_id="atl-stuck",
        exchange_order_id="x-1",
        status=status,
        executed_quantity=Decimal(quantity),
        average_price=None if quantity == "0" else Decimal("100"),
        raw_status=status.value,
    )


def _lifecycle(stage: ExecutionStage, filled: str = "0") -> ExecutionLifecycle:
    return ExecutionLifecycle(
        signal_id=uuid4(),
        instrument_id=InstrumentId(Venue.BINANCE_USDM, "BTCUSDT"),
        direction=Direction.LONG,
        client_order_id="atl-stuck",
        stage=stage,
        updated_at=datetime(2026, 8, 10, tzinfo=UTC),
        filled_quantity=Decimal(filled),
    )


def _system_with(result: OrderResult | None) -> TradingSystem:
    context = VenueContext(
        market_data=StaticMarketData(),  # type: ignore[arg-type]
        execution=LifecycleExecution(_empty_account(), result),  # type: ignore[arg-type]
        coordinator=RecordingCoordinator(),  # type: ignore[arg-type]
    )
    return TradingSystem(
        contexts=(context,),
        screener=UniverseScreener([]),  # type: ignore[arg-type]
        strategy=RejectFirstStrategy(),  # type: ignore[arg-type]
        repository=None,  # type: ignore[arg-type]
    )


def test_unfilled_entry_intent_is_safely_abandonable() -> None:
    """A terminal zero-fill means no inventory exists, so nothing can be orphaned."""
    view = {"status": "EXPIRED", "executed_quantity": "0"}
    assert TradingSystem._lifecycle_is_safely_dead(
        _lifecycle(ExecutionStage.INTENT_PERSISTED), view
    )


def test_a_filled_entry_is_never_auto_abandoned() -> None:
    """Past ENTRY_FILLED there is inventory; abandoning it would orphan a live position."""
    view = {"status": "FILLED", "executed_quantity": "1"}
    for stage in (
        ExecutionStage.ENTRY_FILLED,
        ExecutionStage.STOP_CONFIRMED,
        ExecutionStage.TARGET_CONFIRMED,
    ):
        assert not TradingSystem._lifecycle_is_safely_dead(_lifecycle(stage, "1"), view)


def test_partially_filled_entry_intent_is_never_abandoned() -> None:
    view = {"status": "CANCELED", "executed_quantity": "0.5"}
    assert not TradingSystem._lifecycle_is_safely_dead(
        _lifecycle(ExecutionStage.INTENT_PERSISTED), view
    )


def test_unreadable_venue_status_is_never_abandoned() -> None:
    """An unknown status is the exact condition the kill switch exists for."""
    for view in (
        {"status": "not_found_at_exchange"},
        {"status": "query_failed", "detail": "timeout"},
        {"status": "NEW", "executed_quantity": "0"},
    ):
        assert not TradingSystem._lifecycle_is_safely_dead(
            _lifecycle(ExecutionStage.INTENT_PERSISTED), view
        )


class UnreadableAccount(StaticExecution):
    """Mirrors a stale Spot Testnet baseline: account state cannot be valued."""

    async def account_snapshot(self) -> AccountSnapshot:
        raise VenueError("Spot Testnet balances fell below the captured baseline for AAVE...")


def _unreadable_system(repository: SqliteStateRepository) -> TradingSystem:
    context = VenueContext(
        market_data=StaticMarketData(),  # type: ignore[arg-type]
        execution=UnreadableAccount(_empty_account()),  # type: ignore[arg-type]
        coordinator=RecordingCoordinator(),  # type: ignore[arg-type]
    )
    return TradingSystem(
        contexts=(context,),
        screener=UniverseScreener([]),  # type: ignore[arg-type]
        strategy=RejectFirstStrategy(),  # type: ignore[arg-type]
        repository=repository,
    )


async def test_unreadable_account_stops_the_cycle_without_crashing(
    settings: Settings,
) -> None:
    """Regression: a stale testnet baseline propagated out of run_cycle.

    That counted as a scheduler failure, so three cycles later the durable kill switch fired
    with a message about scheduler failures rather than about the account.
    """
    repository = SqliteStateRepository(settings.state_db_path)
    await repository.initialize()
    system = _unreadable_system(repository)

    assert not await system._tracked_positions_are_consistent()
    for _ in range(3):
        result = await system.run_cycle()  # must not raise
        assert result.reason is not CycleReason.EXECUTED


async def test_reconciliation_report_survives_an_unreadable_venue(
    settings: Settings,
) -> None:
    """A diagnostic that dies on the failure it explains is worse than none."""
    repository = SqliteStateRepository(settings.state_db_path)
    await repository.initialize()

    report = await _unreadable_system(repository).reconciliation_report()

    errors = report["venue_errors"]
    assert isinstance(errors, list) and len(errors) == 1
    assert errors[0]["error"] == "VenueError"
    assert "baseline" in errors[0]["detail"]
    # The rest of the report must still be present, not swallowed by the one failure.
    assert "incomplete_execution_lifecycles" in report
    assert "kill_switch" in report


class OpenPositionExecution(StaticExecution):
    def __init__(self, account: AccountSnapshot, position: Position | None) -> None:
        super().__init__(replace(account, positions=(position,) if position else ()))

    async def query_order(
        self, instrument_id: InstrumentId, client_order_id: str
    ) -> OrderResult | None:
        del instrument_id, client_order_id
        return _order_result(OrderStatus.FILLED, "12083")


async def _system_with_stuck_lifecycle(
    settings: Settings, *, position: Position | None = None
) -> tuple[TradingSystem, str]:
    repository = SqliteStateRepository(settings.state_db_path)
    await repository.initialize()
    lifecycle = _lifecycle(ExecutionStage.INTENT_PERSISTED)
    await repository.record_execution_lifecycle(lifecycle)
    context = VenueContext(
        market_data=StaticMarketData(),  # type: ignore[arg-type]
        execution=OpenPositionExecution(_empty_account(), position),  # type: ignore[arg-type]
        coordinator=RecordingCoordinator(),  # type: ignore[arg-type]
    )
    system = TradingSystem(
        contexts=(context,),
        screener=UniverseScreener([]),  # type: ignore[arg-type]
        strategy=RejectFirstStrategy(),  # type: ignore[arg-type]
        repository=repository,
    )
    return system, str(lifecycle.signal_id)


async def test_operator_can_retire_a_lifecycle_whose_position_is_gone(
    settings: Settings,
) -> None:
    """The orphaned-fill case: filled at the venue, never recorded, now closed."""
    system, signal_id = await _system_with_stuck_lifecycle(settings)

    result = await system.abandon_execution_lifecycle(signal_id, reason="verified flat on venue")

    assert result["abandoned"] is True
    # The fill predates any trade record, so its PnL is not in the ledger.
    assert result["pnl_booked"] is False
    assert not await system._repository.list_incomplete_execution_lifecycles()


async def test_abandon_is_refused_while_the_position_is_still_open(
    settings: Settings,
) -> None:
    """An operator must not be able to erase a lifecycle protecting live inventory."""
    position = Position(
        instrument_id=InstrumentId(Venue.BINANCE_USDM, "BTCUSDT"),
        direction=Direction.LONG,
        quantity=Decimal("12083"),
        entry_price=Decimal("0.01164"),
        mark_price=Decimal("0.01164"),
        unrealized_pnl=Decimal("0"),
    )
    system, signal_id = await _system_with_stuck_lifecycle(settings, position=position)

    with pytest.raises(SafetyInterlockError, match="still has an open position"):
        await system.abandon_execution_lifecycle(signal_id, reason="trying to force it")


async def test_abandon_is_refused_for_an_unknown_lifecycle(settings: Settings) -> None:
    system, _ = await _system_with_stuck_lifecycle(settings)
    with pytest.raises(SafetyInterlockError, match="was not found"):
        await system.abandon_execution_lifecycle(str(uuid4()), reason="does not exist at all")


class SpotVenueBroken:
    """A second venue whose account cannot be valued, e.g. a stale balance baseline."""

    venue = Venue.BINANCE_SPOT

    async def account_snapshot(self) -> AccountSnapshot:
        raise VenueError("Spot Testnet balances fell below the captured baseline for AAVE...")

    async def query_order(
        self, instrument_id: InstrumentId, client_order_id: str
    ) -> OrderResult | None:
        del instrument_id, client_order_id
        return None


class SpotMarketData:
    venue = Venue.BINANCE_SPOT


async def test_abandon_ignores_an_unrelated_venue_being_unreadable(
    settings: Settings,
) -> None:
    """Regression: a broken Spot baseline deadlocked recovery of a USD-M lifecycle.

    Neither side could be cleared until the other was, so the system stayed halted.
    Readability is only required for the venue that actually holds the instrument.
    """
    repository = SqliteStateRepository(settings.state_db_path)
    await repository.initialize()
    lifecycle = _lifecycle(ExecutionStage.INTENT_PERSISTED)  # binance_usdm
    await repository.record_execution_lifecycle(lifecycle)

    system = TradingSystem(
        contexts=(
            VenueContext(
                market_data=StaticMarketData(),  # type: ignore[arg-type]
                execution=OpenPositionExecution(_empty_account(), None),  # type: ignore[arg-type]
                coordinator=RecordingCoordinator(),  # type: ignore[arg-type]
            ),
            VenueContext(
                market_data=SpotMarketData(),  # type: ignore[arg-type]
                execution=SpotVenueBroken(),  # type: ignore[arg-type]
                coordinator=RecordingCoordinator(),  # type: ignore[arg-type]
            ),
        ),
        screener=UniverseScreener([]),  # type: ignore[arg-type]
        strategy=RejectFirstStrategy(),  # type: ignore[arg-type]
        repository=repository,
    )

    result = await system.abandon_execution_lifecycle(
        str(lifecycle.signal_id), reason="usdm position confirmed closed at venue"
    )
    assert result["abandoned"] is True


async def test_abandon_still_refuses_when_the_owning_venue_is_unreadable(
    settings: Settings,
) -> None:
    """Scoping the check must not weaken it for the venue that matters."""
    repository = SqliteStateRepository(settings.state_db_path)
    await repository.initialize()
    lifecycle = _lifecycle(ExecutionStage.INTENT_PERSISTED)
    await repository.record_execution_lifecycle(lifecycle)

    system = TradingSystem(
        contexts=(
            VenueContext(
                market_data=StaticMarketData(),  # type: ignore[arg-type]
                execution=UnreadableAccount(_empty_account()),  # type: ignore[arg-type]
                coordinator=RecordingCoordinator(),  # type: ignore[arg-type]
            ),
        ),
        screener=UniverseScreener([]),  # type: ignore[arg-type]
        strategy=RejectFirstStrategy(),  # type: ignore[arg-type]
        repository=repository,
    )

    with pytest.raises(SafetyInterlockError, match="cannot be read"):
        await system.abandon_execution_lifecycle(
            str(lifecycle.signal_id), reason="attempting without verification"
        )


class AdoptableExecution(StaticExecution):
    """A venue holding an open position, able to accept protective orders."""

    def __init__(self, account: AccountSnapshot) -> None:
        super().__init__(account)
        self.protected: tuple[TradeSignal, Decimal] | None = None

    async def query_order(
        self, instrument_id: InstrumentId, client_order_id: str
    ) -> OrderResult | None:
        del instrument_id, client_order_id
        # The venue still knows the fill price even when the position row omits it.
        return replace(_order_result(OrderStatus.FILLED, "0.032"), average_price=Decimal("204.35"))

    async def place_protection(self, signal: TradeSignal, quantity: Decimal) -> ProtectionSet:
        self.protected = (signal, quantity)
        stop = ProtectionOrder(
            ProtectionRole.STOP,
            _order_result(OrderStatus.NEW),
            signal.stop_price,
            quantity,
        )
        target = ProtectionOrder(
            ProtectionRole.TARGET,
            _order_result(OrderStatus.NEW),
            signal.target_price,
            quantity,
        )
        return ProtectionSet(stop=stop, target=target)


async def _adoptable(settings: Settings) -> tuple[TradingSystem, str, AdoptableExecution]:
    repository = SqliteStateRepository(settings.state_db_path)
    await repository.initialize()
    lifecycle = _lifecycle(ExecutionStage.INTENT_PERSISTED)
    await repository.record_execution_lifecycle(lifecycle)
    await repository.record_order_intent(
        OrderIntent(
            instrument=make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES),
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("0.032"),
            client_order_id=lifecycle.client_order_id,
            direction=Direction.LONG,
            metadata={"stop_price": "201.20", "target_price": "210.62"},
        )
    )
    position = Position(
        instrument_id=lifecycle.instrument_id,
        direction=Direction.LONG,
        quantity=Decimal("0.032"),
        entry_price=Decimal("204.35"),
        mark_price=Decimal("204.35"),
        unrealized_pnl=Decimal("0"),
    )
    execution = AdoptableExecution(replace(_empty_account(), positions=(position,)))

    class Instruments:
        venue = Venue.BINANCE_USDM

        async def instruments(self) -> list[Instrument]:
            return [
                replace(
                    make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES),
                    id=lifecycle.instrument_id,
                )
            ]

    system = TradingSystem(
        contexts=(
            VenueContext(
                market_data=Instruments(),  # type: ignore[arg-type]
                execution=execution,  # type: ignore[arg-type]
                coordinator=RecordingCoordinator(),  # type: ignore[arg-type]
            ),
        ),
        screener=UniverseScreener([]),  # type: ignore[arg-type]
        strategy=RejectFirstStrategy(),  # type: ignore[arg-type]
        repository=repository,
    )
    return system, str(lifecycle.signal_id), execution


async def test_orphaned_position_is_protected_with_its_intended_bracket(
    settings: Settings,
) -> None:
    """The protective prices decided before the order was sent are still the right ones."""
    system, signal_id, execution = await _adoptable(settings)

    result = await system.adopt_orphaned_position(signal_id, reason="orphan from parse defect")

    assert result["adopted"] is True
    assert execution.protected is not None
    signal, quantity = execution.protected
    assert signal.stop_price == Decimal("201.20")
    assert signal.target_price == Decimal("210.62")
    # Protection covers the quantity that actually filled, not the quantity requested.
    assert quantity == Decimal("0.032")


async def test_adopted_position_becomes_a_tracked_managed_trade(
    settings: Settings,
) -> None:
    system, signal_id, _ = await _adoptable(settings)
    await system.adopt_orphaned_position(signal_id, reason="orphan from parse defect")

    open_trades = await system._repository.list_open_trades()
    assert len(open_trades) == 1
    assert open_trades[0].quantity == Decimal("0.032")
    # No longer blocking the cycle.
    assert not await system._repository.list_incomplete_execution_lifecycles()


async def test_adopt_refuses_when_no_position_exists(settings: Settings) -> None:
    """Nothing to protect means this is an abandon, not an adopt."""
    repository = SqliteStateRepository(settings.state_db_path)
    await repository.initialize()
    lifecycle = _lifecycle(ExecutionStage.INTENT_PERSISTED)
    await repository.record_execution_lifecycle(lifecycle)
    await repository.record_order_intent(
        OrderIntent(
            instrument=make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES),
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("0.032"),
            client_order_id=lifecycle.client_order_id,
            direction=Direction.LONG,
            metadata={"stop_price": "201.20", "target_price": "210.62"},
        )
    )
    system = TradingSystem(
        contexts=(
            VenueContext(
                market_data=StaticMarketData(),  # type: ignore[arg-type]
                execution=OpenPositionExecution(_empty_account(), None),  # type: ignore[arg-type]
                coordinator=RecordingCoordinator(),  # type: ignore[arg-type]
            ),
        ),
        screener=UniverseScreener([]),  # type: ignore[arg-type]
        strategy=RejectFirstStrategy(),  # type: ignore[arg-type]
        repository=repository,
    )
    with pytest.raises(SafetyInterlockError, match="use abandon-lifecycle instead"):
        await system.adopt_orphaned_position(
            str(lifecycle.signal_id), reason="nothing to adopt here"
        )


async def test_adopt_recovers_entry_price_when_the_venue_reports_zero(
    settings: Settings,
) -> None:
    """Testnet can report a live position with no entry price; the order still knows it."""
    system, signal_id, execution = await _adoptable(settings)
    zeroed = replace(
        execution._account.positions[0], entry_price=Decimal("0"), mark_price=Decimal("0")
    )
    execution._account = replace(execution._account, positions=(zeroed,))

    result = await system.adopt_orphaned_position(signal_id, reason="entry price recovery")

    # _order_result reports 12083 @ 100 for the queried order.
    assert Decimal(str(result["entry_price"])) > 0


async def test_adopt_names_the_unusable_value_instead_of_a_generic_error(
    settings: Settings,
) -> None:
    """`normalize_bracket_prices` reports only that *something* was non-positive."""
    system, signal_id, execution = await _adoptable(settings)
    payload = await system._repository.get_order_intent_payload(
        (await system._repository.list_incomplete_execution_lifecycles())[0].client_order_id
    )
    assert payload is not None

    # Force an unusable tick size on the instrument the venue advertises.
    original = system._contexts[0].market_data.instruments

    async def broken_instruments() -> list[Instrument]:
        return [replace(item, price_tick=Decimal("0")) for item in await original()]

    system._contexts[0].market_data.instruments = broken_instruments  # type: ignore[method-assign]

    with pytest.raises(SafetyInterlockError, match="instrument price tick is not positive"):
        await system.adopt_orphaned_position(signal_id, reason="should name the bad value")


async def test_adopt_refuses_a_bracket_that_no_longer_brackets_the_fill(
    settings: Settings,
) -> None:
    """A stop above the fill would close the position the instant it is placed."""
    system, signal_id, execution = await _adoptable(settings)
    far_below = replace(execution._account.positions[0], entry_price=Decimal("150"))
    execution._account = replace(execution._account, positions=(far_below,))

    with pytest.raises(SafetyInterlockError, match="no longer brackets the fill"):
        await system.adopt_orphaned_position(signal_id, reason="geometry is now invalid")
