from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from algotrader.application.recovery_episodes import RecoveryEpisodeService
from algotrader.application.recovery_screener import RecoveryScreener
from algotrader.domain.models import Candle, Direction, Quote
from algotrader.domain.recovery_episodes import RecoveryEpisode, advance_episode
from algotrader.infrastructure.recovery_episodes import RecoveryEpisodeStore
from tests.test_capture_evidence import Macro
from tests.test_recovery_screener import AT, RecoveryMarket, derivative


@pytest.fixture
async def candidate(settings):
    market = RecoveryMarket()
    engine = RecoveryScreener(settings, Macro(), evaluated_at=AT, decision_at=AT)
    return (await engine.screen(market, market, max_symbols=1, target_direction=Direction.LONG))[0]


def bars_for(episode, count=1):
    result = []
    for index in range(-4, count):
        opened = AT + timedelta(minutes=15 * index)
        entry = episode.entry
        result.append(Candle(opened, opened + timedelta(minutes=15),
                             entry - Decimal("0.1"), entry + Decimal("0.2"),
                             entry - Decimal("0.2"), entry + Decimal("0.05"),
                             Decimal("10"), Decimal("1000")))
    return result


def quote_for(episode, at, price=None):
    price = episode.entry + Decimal("0.05") if price is None else price
    return Quote(episode.instrument.id, price - Decimal("0.01"), price + Decimal("0.01"),
                 price, Decimal("5000000"), observed_at=at)


def advance(episode, minutes=15, bars=None, quote=None, flow=True):
    at = AT + timedelta(minutes=minutes)
    return advance_episode(episode, at=at, bars=bars if bars is not None else bars_for(episode),
                           quote=quote or quote_for(episode, at),
                           derivatives=replace(derivative(), observed_at=at) if flow else None)


def test_closed_confirmation_freezes_bracket_and_observed_trigger(candidate):
    episode = RecoveryEpisode.enroll(candidate, AT)
    state = advance(episode)
    assert state.status == "triggered"
    assert state.triggered_at == AT + timedelta(minutes=15)
    assert state.confirmation_bar == state.triggered_at
    assert state.trigger_price == state.entry + Decimal("0.05")
    assert state.entry == episode.entry and state.stop == episode.stop
    assert state.first_seen == episode.first_seen and state.qualified_at == AT


@pytest.mark.parametrize("case", ["flow", "stale_flow", "quote", "future_quote", "extension", "volume", "unclosed", "late"])
def test_confirmation_requires_fresh_complete_closed_inputs(candidate, case):
    episode = RecoveryEpisode.enroll(candidate, AT)
    at = AT + timedelta(minutes=15)
    bars = bars_for(episode)
    quote = quote_for(episode, at)
    flow = replace(derivative(), observed_at=at)
    if case == "flow":
        flow = None
    if case == "stale_flow":
        flow = replace(flow, observed_at=AT)
    if case == "quote":
        quote = replace(quote, observed_at=AT)
    if case == "future_quote":
        quote = replace(quote, observed_at=at + timedelta(seconds=6))
    if case == "extension":
        quote = quote_for(episode, at, episode.entry + (episode.entry - episode.stop))
    if case == "volume":
        bars[-1] = replace(bars[-1], quote_volume=Decimal("1"))
    if case == "unclosed":
        bars[-1] = replace(bars[-1], closed=False)
    if case == "late":
        at += timedelta(seconds=121)
        quote = quote_for(episode, at)
        flow = replace(derivative(), observed_at=at)
    state = advance_episode(episode, at=at, bars=bars, quote=quote, derivatives=flow)
    assert state.status == "waiting" and state.triggered_at is None


def test_gap_is_unknown_then_repaired_without_retrospective_trigger(candidate):
    episode = RecoveryEpisode.enroll(candidate, AT)
    gap = advance(episode, minutes=30, bars=bars_for(episode, 2)[1:-1])
    assert not gap.coverage_complete and gap.last_bar_open == episode.last_bar_open
    repaired = advance(gap, minutes=31, bars=bars_for(episode, 2))
    assert repaired.coverage_complete and repaired.status == "triggered"
    assert repaired.triggered_at == AT + timedelta(minutes=31)


def test_unknown_preconfirmation_low_invalidates_after_backfill(candidate):
    episode = RecoveryEpisode.enroll(candidate, AT)
    history = bars_for(episode, 2)
    history[-2] = replace(history[-2], low=episode.stop)
    state = advance(episode, minutes=30, bars=history)
    assert state.status == "invalidated" and state.triggered_at is None


def test_midbar_seed_stop_touch_has_unknown_order(candidate):
    at = AT + timedelta(minutes=5)
    episode = RecoveryEpisode.enroll(candidate, at)
    history = bars_for(episode, 2)
    history[-2] = replace(history[-2], low=episode.stop)
    state = advance(episode, minutes=30, bars=history)
    assert state.status == "indeterminate" and not state.coverage_complete
    assert state.reason == "initial_intrabar_order_unknown"


def test_no_new_bar_cannot_retrigger_but_quote_can_stop(candidate):
    episode = advance(RecoveryEpisode.enroll(candidate, AT))
    at = AT + timedelta(minutes=16)
    state = advance(episode, minutes=16, quote=quote_for(episode, at, episode.stop))
    assert state.status == "invalidated" and state.reason == "observed_stop_breach"
    assert state.triggered_at == episode.triggered_at


@pytest.mark.parametrize("outcome", ["target", "stop", "both"])
def test_postconfirmation_bar_outcomes_and_ambiguous_order(candidate, outcome):
    episode = advance(RecoveryEpisode.enroll(candidate, AT))
    history = bars_for(episode, 2)
    history[-1] = replace(history[-1],
                          high=episode.target if outcome in ("target", "both") else history[-1].high,
                          low=episode.stop if outcome in ("stop", "both") else history[-1].low)
    state = advance(episode, minutes=30, bars=history)
    assert state.status == {"target": "target_hit", "stop": "invalidated", "both": "indeterminate"}[outcome]
    assert advance(state, minutes=45, bars=bars_for(episode, 3)) == state


def test_overlap_with_confirmation_cannot_prove_stop_order(candidate):
    episode = advance(RecoveryEpisode.enroll(candidate, AT), minutes=16)
    history = bars_for(episode, 2)
    history[-1] = replace(history[-1], low=episode.stop)
    state = advance(episode, minutes=30, bars=history)
    assert state.status == "indeterminate" and state.reason == "intrabar_order_unknown"


@pytest.mark.parametrize("case", ["duplicate", "unordered", "bad_ohlc", "wrong_interval"])
def test_malformed_history_is_rejected(candidate, case):
    episode = RecoveryEpisode.enroll(candidate, AT)
    bars = bars_for(episode)
    if case == "duplicate":
        bars.append(bars[-1])
    if case == "unordered":
        bars.reverse()
    if case == "bad_ohlc":
        bars[-1] = replace(bars[-1], low=bars[-1].high + 1)
    if case == "wrong_interval":
        bars[-1] = replace(bars[-1], closed_at=bars[-1].closed_at - timedelta(minutes=1))
    with pytest.raises(ValueError):
        advance(episode, bars=bars)


def test_repeated_and_out_of_order_observations(candidate):
    episode = advance(RecoveryEpisode.enroll(candidate, AT))
    assert advance(episode) == episode
    with pytest.raises(ValueError, match="out-of-order"):
        advance(episode, minutes=14)


def test_restart_idempotency_and_optimistic_concurrency(candidate, tmp_path):
    path = tmp_path / "episodes.sqlite"
    first = RecoveryEpisodeStore(path)
    assert first.enroll([candidate], AT) == {"RECUSDT": "enrolled"}
    episode = first.episodes()[0]
    second = RecoveryEpisodeStore(path)
    assert second.enroll([candidate], AT) == {"RECUSDT": "existing_episode"}
    state = advance(episode)
    first.commit(episode, state, {"kind": "test"})
    with pytest.raises(RuntimeError, match="concurrently"):
        second.commit(episode, state, {"kind": "test"})
    assert second.episodes() == (state,)
    assert len(second.evidence()) == 2
    assert path.stat().st_mode & 0o777 == 0o600


def test_capacity_and_active_rescan_do_not_reset_anchor(candidate, tmp_path):
    store = RecoveryEpisodeStore(tmp_path / "episodes.sqlite", capacity=1)
    store.enroll([candidate], AT)
    updated = replace(candidate, evaluated_at=AT + timedelta(minutes=5),
                      suggested_entry=candidate.suggested_entry + 1)
    assert store.enroll([updated], AT + timedelta(minutes=5)) == {"RECUSDT": "existing_episode"}
    assert store.episodes()[0].entry == candidate.suggested_entry
    other = replace(candidate, instrument=replace(candidate.instrument,
                    id=replace(candidate.instrument.id, symbol="OTHERUSDT")))
    assert store.enroll([other], AT) == {"OTHERUSDT": "pool_capacity"}


class FifteenMarket:
    def __init__(self, episode):
        self.episode = episode
        self.requests = []
        self.fail = False

    async def candles(self, instrument, interval, limit):
        self.requests.append((interval, limit))
        if self.fail:
            raise TimeoutError()
        return bars_for(self.episode)[-limit:]

    async def quotes(self, instruments):
        return [quote_for(self.episode, AT + timedelta(minutes=15))]

    async def derivatives_snapshot(self, instrument):
        return replace(derivative(), observed_at=AT + timedelta(minutes=15))


async def test_service_restart_refresh_and_bounded_fetch(candidate, tmp_path):
    path = tmp_path / "episodes.sqlite"
    store = RecoveryEpisodeStore(path)
    store.enroll([candidate], AT)
    market = FifteenMarket(store.episodes()[0])
    service = RecoveryEpisodeService(RecoveryEpisodeStore(path))
    result = await service.refresh(market, market, at=AT + timedelta(minutes=15))
    assert result[0].status == "triggered"
    assert market.requests == [("15m", 6)]
    assert await service.refresh(market, market, at=AT + timedelta(minutes=15)) == result
    assert len(market.requests) == 1 and len(store.evidence()) == 2


async def test_service_failure_persists_unknown_without_advancing_watermark(candidate, tmp_path):
    store = RecoveryEpisodeStore(tmp_path / "episodes.sqlite")
    store.enroll([candidate], AT)
    market = FifteenMarket(store.episodes()[0])
    market.fail = True
    state, = await RecoveryEpisodeService(store).refresh(market, market, at=AT + timedelta(minutes=15))
    assert state.reason == "history_gap" and not state.coverage_complete
    assert state.triggered_at is None
    assert store.evidence()[-1]["evidence"]["errors"] == {"candles": "TimeoutError"}


async def test_scan_entry_point_enrolls_only_recovery(settings, tmp_path):
    market = RecoveryMarket()
    store = RecoveryEpisodeStore(tmp_path / "episodes.sqlite")
    candidates, admission = await RecoveryEpisodeService(store).scan_and_enroll(
        RecoveryScreener(settings, Macro(), evaluated_at=AT, decision_at=AT),
        market, market, at=AT, max_symbols=1, target_direction=Direction.LONG)
    assert len(candidates) == 1 and admission == {"RECUSDT": "enrolled"}
    assert store.episodes()[0].source_at == AT


@pytest.mark.parametrize("hours", [24, 25])
def test_untriggered_confirmation_expires_without_using_later_quotes(candidate, hours):
    episode = RecoveryEpisode.enroll(candidate, AT)
    at = AT + timedelta(hours=hours)
    state = advance_episode(episode, at=at, bars=bars_for(episode, 96),
                            quote=quote_for(episode, at), derivatives=None)
    assert state.status == "expired" and state.ended_at == AT + timedelta(hours=24)
    assert state.triggered_at is None
    if hours == 25:
        assert state.last_price_at is None


def test_unrepairable_gap_ends_unknown_at_horizon(candidate):
    episode = RecoveryEpisode.enroll(candidate, AT)
    state = advance(episode, minutes=24 * 60, bars=[])
    assert state.status == "indeterminate" and not state.coverage_complete
    assert state.reason == "horizon_with_history_gap"


@pytest.mark.parametrize("case", ["stale", "gates", "policy"])
def test_enrollment_rejects_unqualified_inputs(candidate, case):
    at = AT
    if case == "stale":
        at += timedelta(seconds=601)
    if case == "gates":
        candidate = replace(candidate, checklist=candidate.checklist[:-1])
    if case == "policy":
        candidate = replace(candidate, research_policy_version="future-policy")
    with pytest.raises(ValueError):
        RecoveryEpisode.enroll(candidate, at)


async def test_replay_all_episodes_and_detect_modified_transition(candidate, tmp_path):
    from algotrader.infrastructure.recovery_episodes import read_recovery_snapshot
    from scripts.research_recovery_timeline import replay
    path = tmp_path / "episodes.sqlite"
    store = RecoveryEpisodeStore(path)
    store.enroll([candidate], AT)
    service = RecoveryEpisodeService(store)
    market = FifteenMarket(store.episodes()[0])
    await service.refresh(market, market, at=AT + timedelta(minutes=15))
    episodes, records = read_recovery_snapshot(path)
    result = replay(episodes, records)
    assert result["episodes"] == result["triggered"] == 1
    assert result["outcomes"][0]["sampled_return_pct"] == 0
    assert result["performance_claims_permitted"] is False
    altered = list(records)
    altered[-1] = {**records[-1], "state": {**records[-1]["state"], "reason": "tampered"}}
    with pytest.raises(ValueError, match="replay mismatch"):
        replay(episodes, altered)
    with pytest.raises(ValueError, match="differs from replayed"):
        replay((), records)


def test_read_only_report_never_creates_database(tmp_path):
    import sqlite3

    from algotrader.infrastructure.recovery_episodes import read_recovery_snapshot
    path = tmp_path / "missing.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        read_recovery_snapshot(path)
    assert not path.exists()


def test_frozen_bracket_cannot_be_replaced_in_store(candidate, tmp_path):
    store = RecoveryEpisodeStore(tmp_path / "episodes.sqlite")
    store.enroll([candidate], AT)
    episode = store.episodes()[0]
    changed = replace(advance(episode), entry=episode.entry + Decimal("0.01"))
    with pytest.raises(ValueError, match="immutable"):
        store.commit(episode, changed, {"kind": "test"})
    assert store.episodes() == (episode,)
    assert len(store.evidence()) == 1


async def test_refresh_entry_point_rejects_missing_database_without_network(tmp_path):
    from scripts.research_recovery_refresh import refresh
    with pytest.raises(ValueError, match="existing enrolled"):
        await refresh(tmp_path / "missing.sqlite")
    assert not (tmp_path / "missing.sqlite").exists()


async def test_invalid_dates_persist_as_unknown_and_replay(candidate, tmp_path):
    from scripts.research_recovery_timeline import replay
    store = RecoveryEpisodeStore(tmp_path / "episodes.sqlite")
    store.enroll([candidate], AT)
    market = FifteenMarket(store.episodes()[0])

    async def malformed(instrument, interval, limit):
        history = bars_for(store.episodes()[0])
        history[-1] = replace(history[-1], closed_at=history[-1].closed_at.replace(tzinfo=None))
        return history

    market.candles = malformed
    state, = await RecoveryEpisodeService(store).refresh(market, market, at=AT + timedelta(minutes=15))
    assert state.reason == "history_gap" and not state.coverage_complete
    assert store.evidence()[-1]["evidence"]["errors"] == {"candles": "ValueError"}
    assert replay(store.episodes(), store.evidence())["triggered"] == 0


def test_midbar_discovery_requires_its_partial_interval_before_confirmation(candidate):
    episode = RecoveryEpisode.enroll(candidate, AT + timedelta(minutes=5))
    history = bars_for(episode, 2)
    missing = history[:-2] + history[-1:]
    unknown = advance(episode, minutes=30, bars=missing)
    assert unknown.reason == "initial_interval_unobserved" and not unknown.coverage_complete
    repaired = advance(unknown, minutes=31, bars=history)
    assert repaired.status == "triggered" and repaired.initial_interval_verified
    assert repaired.triggered_at == AT + timedelta(minutes=31)
