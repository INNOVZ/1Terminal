"""Tests for Top-Gainer Short Watch tracking and short decision-journal logging.

Verifies that short decision logging, Stage-0 top-gainer pinning, watch-loop state
transitions, SQLite persistence, and portal endpoints work cleanly without altering
long/buy screening or live 5/5 publication.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from algotrader.adapters.external.models import (
    FearGreedReading,
    MacroSentimentContext,
    StablecoinSupplyMetric,
)
from algotrader.api import portal as portal_module
from algotrader.api.app import app
from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.application.top_gainer_short_watch import (
    TopGainerDistributionScreener,
    TopGainerShortWatchService,
)
from algotrader.config import load_settings
from algotrader.domain.models import (
    Candle,
    DerivativesSnapshot,
    Direction,
    MarketKind,
    Quote,
    Venue,
)
from algotrader.domain.scan_observations import ScanDecisionJournal
from algotrader.domain.top_gainer_short_watch import (
    TopGainerWatchEpisode,
    TopGainerWatchPolicy,
    TopGainerWatchState,
    advance_top_gainer_watch_episode,
    select_top_gainer_instruments,
)
from algotrader.infrastructure.top_gainer_short_watch import TopGainerShortWatchStore
from tests.conftest import make_instrument, trending_candles
from tests.test_crypto_screener import FakeCryptoMarketData, FakeDerivativesData


def _macro(at: datetime) -> MacroSentimentContext:
    return MacroSentimentContext(
        fear_greed=FearGreedReading(value=59, classification="Greed", observed_at=at),
        stablecoin_supply=StablecoinSupplyMetric(
            total_circulating_usd=Decimal("160000000000"),
            change_1d_pct=Decimal("0.0"),
            change_7d_pct=Decimal("0.0"),
            change_30d_pct=Decimal("1.0"),
        ),
    )


def _distribution_candles(started: datetime) -> tuple[list[Candle], list[Candle]]:
    near_4h: list[Candle] = []
    for i in range(80):
        if i < 35:
            p = Decimal("3.20") + (Decimal(str(i % 5)) * Decimal("0.01"))
            vol = Decimal("8000")
        elif i < 55:
            p = Decimal("3.25") + (Decimal(str(i - 35)) * Decimal("0.0925"))
            vol = Decimal("28000")
        elif i < 62:
            p = Decimal("5.10") - (Decimal(str(i - 55)) * Decimal("0.05"))
            vol = Decimal("14000")
        elif i < 74:
            p = Decimal("4.75") + (Decimal(str(i - 62)) * Decimal("0.0416"))
            vol = Decimal("7000")
        else:
            p = Decimal("5.25") - (Decimal(str(i - 74)) * Decimal("0.038"))
            vol = Decimal("16000")
        near_4h.append(
            Candle(
                opened_at=started + timedelta(hours=i * 4),
                closed_at=started + timedelta(hours=(i + 1) * 4),
                open=p - Decimal("0.02"),
                high=p + Decimal("0.03"),
                low=p - Decimal("0.03"),
                close=p,
                volume=vol,
                quote_volume=vol * p,
                closed=True,
            )
        )
    near_1h: list[Candle] = []
    for i in range(320):
        t = started + timedelta(hours=i)
        c4h = near_4h[i // 4]
        p = c4h.close + (Decimal(str((i % 4) - 2)) * Decimal("0.005"))
        vol = (c4h.volume / Decimal("4")).quantize(Decimal("1"))
        near_1h.append(
            Candle(
                opened_at=t,
                closed_at=t + timedelta(hours=1),
                open=p - Decimal("0.01"),
                high=p + Decimal("0.015"),
                low=p - Decimal("0.015"),
                close=p,
                volume=vol,
                quote_volume=vol * p,
                closed=True,
            )
        )
    return near_1h, near_4h


def _quote_for(
    symbol: str, price: Decimal, change_24h: Decimal, qv: Decimal, at: datetime
) -> Quote:
    from algotrader.domain.models import InstrumentId

    return Quote(
        instrument_id=InstrumentId(venue=Venue.BINANCE_USDM, symbol=symbol),
        bid=price * Decimal("0.9995"),
        ask=price * Decimal("1.0005"),
        last=price,
        quote_volume_24h=qv,
        observed_at=at,
        price_change_pct_24h=change_24h,
        high_24h=price * Decimal("1.05"),
        low_24h=price * Decimal("0.90"),
    )


def test_select_top_gainer_instruments_pins_active_and_ranks_top_gainers() -> None:
    at = datetime(2026, 11, 16, 4, 0, tzinfo=UTC)
    inst_btc = make_instrument(
        venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="BTCUSDT"
    )
    inst_a = make_instrument(
        venue=Venue.BINANCE_USDM,
        market_kind=MarketKind.FUTURES,
        symbol="ALPHABTC".replace("BTC", "USDT"),
    )
    inst_b = make_instrument(
        venue=Venue.BINANCE_USDM,
        market_kind=MarketKind.FUTURES,
        symbol="BETABTC".replace("BTC", "USDT"),
    )
    inst_c = make_instrument(
        venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="GAMMAUSDT"
    )
    inst_low = make_instrument(
        venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="THINUSDT"
    )

    quotes = {
        "BTCUSDT": _quote_for(
            "BTCUSDT", Decimal("90000"), Decimal("1.5"), Decimal("500000000"), at
        ),
        "ALPHAUSDT": _quote_for(
            "ALPHAUSDT", Decimal("1.2"), Decimal("12.0"), Decimal("1500000"), at
        ),
        "BETAUSDT": _quote_for("BETAUSDT", Decimal("2.4"), Decimal("28.0"), Decimal("900000"), at),
        "GAMMAUSDT": _quote_for(
            "GAMMAUSDT", Decimal("0.8"), Decimal("3.0"), Decimal("2000000"), at
        ),
        "THINUSDT": _quote_for("THINUSDT", Decimal("0.5"), Decimal("45.0"), Decimal("100000"), at),
    }
    policy = TopGainerWatchPolicy(
        top_n_gainers=2, min_24h_gain_pct=Decimal("8.0"), max_active_watches=5
    )
    # GAMMAUSDT is pinned even though its 24h gain cooled to +3%; THINUSDT fails $500k volume floor.
    selected = select_top_gainer_instruments(
        [inst_btc, inst_a, inst_b, inst_c, inst_low],
        quotes,
        policy,
        pinned_symbols=("GAMMAUSDT",),
    )
    assert [i.id.symbol for i in selected] == ["GAMMAUSDT", "BETAUSDT", "ALPHAUSDT"]


@pytest.mark.asyncio
async def test_short_decision_journal_and_top_gainer_watch_lifecycle(tmp_path: Path) -> None:
    started = datetime(2026, 8, 1, tzinfo=UTC)
    now = started + timedelta(hours=320)
    inst_btc = make_instrument(
        venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="BTCUSDT"
    )
    inst_near = make_instrument(
        venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="NEARUSDT"
    )
    inst_flat = make_instrument(
        venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="FLATUSDT"
    )

    near_1h, near_4h = _distribution_candles(started)
    btc_1h = trending_candles(up=True, count=360)
    btc_4h = trending_candles(up=True, count=80)
    flat_1h = trending_candles(up=False, count=320)
    flat_4h = trending_candles(up=False, count=80)

    quotes_map = {
        "BTCUSDT": _quote_for(
            "BTCUSDT", btc_1h[-1].close, Decimal("1.0"), Decimal("800000000"), now
        ),
        "NEARUSDT": _quote_for(
            "NEARUSDT", near_1h[-1].close, Decimal("22.5"), Decimal("4000000"), now
        ),
        "FLATUSDT": _quote_for(
            "FLATUSDT", flat_1h[-1].close, Decimal("14.0"), Decimal("2000000"), now
        ),
    }

    class _QuotedFakeMarket(FakeCryptoMarketData):
        async def quotes(self, instruments):
            return [quotes_map[i.id.symbol] for i in instruments if i.id.symbol in quotes_map]

    market = _QuotedFakeMarket(
        instruments=[inst_btc, inst_near, inst_flat],
        candles_1h_map={"BTCUSDT": btc_1h, "NEARUSDT": near_1h, "FLATUSDT": flat_1h},
        candles_4h_map={"BTCUSDT": btc_4h, "NEARUSDT": near_4h, "FLATUSDT": flat_4h},
    )
    deriv = FakeDerivativesData(
        snapshots={
            "NEARUSDT": DerivativesSnapshot(
                funding_rate=Decimal("0.00022"),
                basis_rate=Decimal("0"),
                open_interest=Decimal("45000000"),
                open_interest_change_1h=Decimal("0.01"),
                open_interest_change_7d=Decimal("0.25"),
                taker_buy_sell_ratio=Decimal("0.84"),
                bid_depth_notional=Decimal("1200000"),
                ask_depth_notional=Decimal("2800000"),
            ),
            "FLATUSDT": DerivativesSnapshot(
                funding_rate=Decimal("0.00010"),
                basis_rate=Decimal("0"),
                open_interest=Decimal("10000000"),
                open_interest_change_1h=Decimal("0.0"),
                open_interest_change_7d=Decimal("0.05"),
                taker_buy_sell_ratio=Decimal("1.0"),
                bid_depth_notional=Decimal("500000"),
                ask_depth_notional=Decimal("500000"),
            ),
        }
    )

    mock_macro = AsyncMock()
    mock_macro.get_context.return_value = _macro(now)

    # 1. Verify short decision-journal logging directly via TopGainerDistributionScreener
    journal = ScanDecisionJournal()
    screener = TopGainerDistributionScreener(
        load_settings(),
        macro_provider=mock_macro,
        evaluated_at=now,
        journal=journal,
    )
    opps = await screener.screen(
        market,
        derivatives_port=deriv,
        min_score=Decimal("40"),
        max_symbols=10,
    )
    assert any(
        o.instrument.id.symbol == "NEARUSDT" and o.direction == Direction.SHORT for o in opps
    )
    opp = next(o for o in opps if o.instrument.id.symbol == "NEARUSDT")
    policy = TopGainerWatchPolicy()
    confirmed = advance_top_gainer_watch_episode(
        None,
        symbol="NEARUSDT",
        base_asset="NEAR",
        quote=quotes_map["NEARUSDT"],
        diagnostic={"gates_passed": True, "checklist_passed": 5},
        opportunity=opp,
        evaluated_at=now,
        policy=policy,
    )
    drift = advance_top_gainer_watch_episode(
        confirmed,
        symbol="NEARUSDT",
        base_asset="NEAR",
        quote=quotes_map["NEARUSDT"],
        diagnostic={"gates_passed": False, "blocker": "current_gates_failed"},
        opportunity=None,
        evaluated_at=now + timedelta(minutes=1),
        policy=policy,
    )
    assert drift.state == TopGainerWatchState.WATCHING
    assert drift.confirmed_opportunity == confirmed.confirmed_opportunity
    stopped = advance_top_gainer_watch_episode(
        drift,
        symbol="NEARUSDT",
        base_asset="NEAR",
        quote=replace(quotes_map["NEARUSDT"], last=opp.suggested_stop * Decimal("2")),
        diagnostic={"gates_passed": False},
        opportunity=None,
        evaluated_at=now + timedelta(minutes=2),
        policy=policy,
    )
    assert stopped.state == TopGainerWatchState.INVALIDATED
    reconfirmed = advance_top_gainer_watch_episode(
        drift,
        symbol="NEARUSDT",
        base_asset="NEAR",
        quote=quotes_map["NEARUSDT"],
        diagnostic={"gates_passed": True, "checklist_passed": 5},
        opportunity=replace(opp, suggested_stop=opp.suggested_stop * Decimal("1.1")),
        evaluated_at=now + timedelta(minutes=2),
        policy=policy,
    )
    assert reconfirmed.confirmed_opportunity == confirmed.confirmed_opportunity
    expired = advance_top_gainer_watch_episode(
        replace(confirmed, state=TopGainerWatchState.WATCHING, confirmed_opportunity=None),
        symbol="NEARUSDT",
        base_asset="NEAR",
        quote=quotes_map["NEARUSDT"],
        diagnostic={"gates_passed": True, "checklist_passed": 5},
        opportunity=opp,
        evaluated_at=now + timedelta(hours=72),
        policy=policy,
    )
    assert expired.state == TopGainerWatchState.EXPIRED
    events = journal.snapshot()
    assert any(e["symbol"] == "NEARUSDT" and e["stage"] == "distribution_candidate" for e in events)
    assert any(e["symbol"] == "FLATUSDT" and e["stage"] == "distribution_rejection" for e in events)

    # 2. Verify bounded watch loop persists WATCHING (FLATUSDT) & CONFIRMED_SHORT (NEARUSDT)
    store = TopGainerShortWatchStore(tmp_path / "short_watch.sqlite3")
    service = TopGainerShortWatchService(store, load_settings(), macro_provider=mock_macro)
    snap1 = await service.run_cycle(market, derivatives_port=deriv, evaluated_at=now)
    assert snap1["counts"]["confirmed_short"] == 1
    assert snap1["counts"]["watching"] == 1
    assert snap1["confirmed_shorts"][0]["symbol"] == "NEARUSDT"
    assert snap1["confirmed_shorts"][0]["checklist_passed"] == 5
    assert snap1["watching"][0]["symbol"] == "FLATUSDT"
    assert snap1["watching"][0]["latest_blocker"] == "no_short_archetype"
    assert snap1["watching"][0]["watch_tier"] == "lower_range_bounce_watch"

    # 3. Verify persistence across new store/service instances + collapse invalidation after 2 cycles
    quotes_map["FLATUSDT"] = _quote_for(
        "FLATUSDT", flat_1h[-1].close, Decimal("-4.0"), Decimal("2000000"), now + timedelta(hours=1)
    )
    service_reloaded = TopGainerShortWatchService(
        TopGainerShortWatchStore(tmp_path / "short_watch.sqlite3"),
        load_settings(),
        macro_provider=mock_macro,
    )
    for sym, quote in quotes_map.items():
        quotes_map[sym] = replace(quote, observed_at=now + timedelta(hours=1))
    await service_reloaded.run_cycle(
        market, derivatives_port=deriv, evaluated_at=now + timedelta(hours=1)
    )
    for sym, quote in quotes_map.items():
        quotes_map[sym] = replace(quote, observed_at=now + timedelta(hours=2))
    snap3 = await service_reloaded.run_cycle(
        market, derivatives_port=deriv, evaluated_at=now + timedelta(hours=2)
    )
    assert snap3["counts"]["watching"] == 0
    assert any(
        e["symbol"] == "FLATUSDT" and e["invalidation_reason"] == "collapsed_below_upper_range"
        for e in snap3["invalidated"]
    )


def test_portal_short_watch_endpoint_isolated_from_live_opportunities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = TopGainerShortWatchStore(tmp_path / "portal_watch.sqlite3")
    service = TopGainerShortWatchService(store, load_settings())
    monkeypatch.setattr(portal_module, "_short_watch_service", service)

    client = TestClient(app)
    resp = client.get("/api/screener/short-watch")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["policy_version"] == TopGainerWatchPolicy().version
    assert payload["counts"] == {
        "watching": 0,
        "confirmed_short": 0,
        "invalidated": 0,
        "expired": 0,
    }


@pytest.mark.asyncio
async def test_exhaustion_short_checklist_parity_without_bearish_divergence(tmp_path: Path) -> None:
    """Non-UTAD exhaustion shorts should qualify 5/5 via climax rejection without 2-pivot divergence."""
    from algotrader.domain.indicators import snapshot as indicator_snapshot

    started = datetime(2026, 8, 1, tzinfo=UTC)
    now = started + timedelta(hours=320)
    inst = make_instrument(
        venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="CLIMAXUSDT"
    )

    # Build monotonic vertical rally + final 4H climax rejection wick (RSI > 72, no 2-pivot bearish div)
    c4h: list[Candle] = []
    for i in range(80):
        if i < 55:
            p = Decimal("2.00") + Decimal(str(i % 4)) * Decimal("0.01")
            hi, lo = p + Decimal("0.02"), p - Decimal("0.02")
            vol = Decimal("5000")
        elif i < 78:
            p = Decimal("2.05") + Decimal(str(i - 55)) * Decimal("0.10")
            hi, lo = p + Decimal("0.03"), p - Decimal("0.03")
            vol = Decimal("25000")
        elif i == 78:
            # Peak climax bar with massive upper wick and lower close
            p = Decimal("4.30")
            hi, lo = Decimal("4.95"), Decimal("4.20")
            vol = Decimal("55000")
        else:
            # Follow-through rollover still well above 72h/30d lows & near 4H EMA20
            p = Decimal("4.12")
            hi, lo = Decimal("4.45"), Decimal("4.08")
            vol = Decimal("30000")
        c4h.append(
            Candle(
                opened_at=started + timedelta(hours=i * 4),
                closed_at=started + timedelta(hours=(i + 1) * 4),
                open=p + Decimal("0.05") if i >= 78 else p - Decimal("0.02"),
                high=hi,
                low=lo,
                close=p,
                volume=vol,
                quote_volume=vol * p,
                closed=True,
            )
        )

    c1h: list[Candle] = []
    for i in range(320):
        t = started + timedelta(hours=i)
        b4 = c4h[i // 4]
        c1h.append(
            Candle(
                opened_at=t,
                closed_at=t + timedelta(hours=1),
                open=b4.open if i % 4 == 0 else b4.close,
                high=b4.high if i % 4 == 2 else b4.close + Decimal("0.01"),
                low=b4.low if i % 4 == 1 else b4.close - Decimal("0.01"),
                close=b4.close,
                volume=b4.volume / Decimal("4"),
                quote_volume=b4.quote_volume / Decimal("4"),
                closed=True,
            )
        )

    snap_1h = indicator_snapshot(c1h)
    quote = _quote_for("CLIMAXUSDT", snap_1h.close, Decimal("18.0"), Decimal("8000000"), now)
    quote = Quote(
        instrument_id=quote.instrument_id,
        bid=quote.bid,
        ask=quote.ask,
        last=quote.last,
        quote_volume_24h=quote.quote_volume_24h,
        observed_at=quote.observed_at,
        price_change_pct_24h=quote.price_change_pct_24h,
        high_24h=Decimal("4.95"),
        low_24h=Decimal("3.80"),
    )
    deriv = DerivativesSnapshot(
        funding_rate=Decimal("0.00020"),
        basis_rate=Decimal("0"),
        open_interest=Decimal("30000000"),
        open_interest_change_1h=Decimal("0.01"),
        open_interest_change_7d=Decimal("0.30"),
        taker_buy_sell_ratio=Decimal("0.82"),
        taker_buy_sell_ratio_4h=Decimal("0.85"),
        taker_buy_sell_ratio_24h=Decimal("0.88"),
        bid_depth_notional=Decimal("1000000"),
        ask_depth_notional=Decimal("2000000"),
    )
    journal = ScanDecisionJournal()
    screener = CryptoOpportunityScreener(
        load_settings(), evaluated_at=now, journal=journal, feature_contract="discovery_v2"
    )
    opp = screener._score_distribution_setup(
        inst=inst,
        quote=quote,
        snap_1h=snap_1h,
        closed_1h=c1h,
        closed_4h=c4h,
        derivatives=deriv,
        macro_context=_macro(now),
    )
    assert opp is not None
    assert opp.setup_label.value == "EXHAUSTION_REVERSAL_SHORT"
    assert len(opp.checklist) == 5
    assert all(passed for _, passed, _ in opp.checklist)
    assert opp.checklist[1][0] == "Divergence / Climax Rejection"


async def test_portal_short_watch_uses_configured_persistent_state_path(tmp_path, monkeypatch):
    import httpx

    from algotrader.api import portal as portal_module
    from algotrader.api.portal_app import create_app
    from algotrader.config import Settings

    working = tmp_path / "app"
    working.mkdir()
    persistent = tmp_path / "state" / "atlas_state.sqlite3"
    settings = Settings(_env_file=None, state_db_path=persistent, screener_cron_enabled=False)
    monkeypatch.chdir(working)
    monkeypatch.setattr(portal_module, "_short_watch_service", None)
    monkeypatch.setattr(portal_module, "load_settings", lambda: settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings)), base_url="http://localhost"
    ) as client:
        response = await client.get("/api/screener/short-watch")
    assert response.status_code == 200
    assert response.json()["counts"]["confirmed_short"] == 0
    assert persistent.is_file()
    assert not (working / "atlas_state.sqlite3").exists()


def test_stale_and_legacy_confirmations_are_not_published(tmp_path):
    now = datetime(2026, 11, 21, tzinfo=UTC)
    policy = TopGainerWatchPolicy()
    ep = TopGainerWatchEpisode(
        episode_id="old",
        symbol="TESTUSDT",
        base_asset="TEST",
        state=TopGainerWatchState.CONFIRMED_SHORT,
        enrolled_at=now,
        updated_at=now,
        anchor_price=Decimal("10"),
        anchor_24h_pct=Decimal("10"),
        peak_price=Decimal("10"),
        peak_24h_pct=Decimal("10"),
        latest_price=Decimal("10"),
        latest_24h_pct=Decimal("10"),
        gates_passed=True,
        checklist_passed=5,
        confirmed_opportunity={"suggested_entry": 10, "suggested_stop": 10.5},
        policy_version=policy.version,
    )
    store = TopGainerShortWatchStore(tmp_path / "watch.sqlite3")
    store.save_cycle({"cycle_id": "initial", "evaluated_at": now.isoformat()}, [ep])
    service = TopGainerShortWatchService(store, load_settings())
    assert service.snapshot(evaluated_at=now)["counts"]["confirmed_short"] == 1
    stale = service.snapshot(evaluated_at=now + timedelta(hours=6))
    assert stale["counts"]["confirmed_short"] == 0
    assert stale["watching"][0]["state"] == "watching"
    store.save_cycle(
        {"cycle_id": "legacy", "evaluated_at": now.isoformat()},
        [replace(ep, policy_version="top-gainer-short-watch-v1")],
    )
    assert service.snapshot(evaluated_at=now)["counts"]["confirmed_short"] == 0


@pytest.mark.parametrize("quote_age_minutes", [None, 10, -10])
async def test_missing_quotes_demote_and_expire_active_watch(
    tmp_path, monkeypatch, quote_age_minutes
):
    now = datetime(2026, 11, 21, tzinfo=UTC)
    store = TopGainerShortWatchStore(tmp_path / "watch.sqlite3")
    ep = TopGainerWatchEpisode(
        episode_id="missing",
        symbol="TESTUSDT",
        base_asset="TEST",
        state=TopGainerWatchState.CONFIRMED_SHORT,
        enrolled_at=now,
        updated_at=now,
        anchor_price=Decimal("10"),
        anchor_24h_pct=Decimal("10"),
        peak_price=Decimal("10"),
        peak_24h_pct=Decimal("10"),
        latest_price=Decimal("10"),
        latest_24h_pct=Decimal("10"),
        gates_passed=True,
        checklist_passed=5,
        confirmed_opportunity={"suggested_entry": 10, "suggested_stop": 10.5},
        policy_version=TopGainerWatchPolicy().version,
    )
    store.save_cycle({"cycle_id": "initial", "evaluated_at": now.isoformat()}, [ep])
    screen = AsyncMock(return_value=[])
    monkeypatch.setattr(TopGainerDistributionScreener, "screen", screen)
    market = AsyncMock()
    market.instruments.return_value = []
    market.quotes.return_value = []
    if quote_age_minutes is not None:
        market.instruments.return_value = [
            make_instrument(
                venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="TESTUSDT"
            )
        ]
        market.quotes.return_value = [
            _quote_for(
                "TESTUSDT",
                Decimal("10"),
                Decimal("10"),
                Decimal("2000000"),
                now + timedelta(hours=1) - timedelta(minutes=quote_age_minutes),
            )
        ]
    new_inst = make_instrument(
        venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="NEWUSDT"
    )
    market.instruments.return_value.append(new_inst)
    market.quotes.return_value.append(
        _quote_for(
            "NEWUSDT", Decimal("10"), Decimal("20"), Decimal("3000000"), now + timedelta(hours=1)
        )
    )
    service = TopGainerShortWatchService(
        store, load_settings(), policy=TopGainerWatchPolicy(top_n_gainers=1, max_active_watches=1)
    )
    result = await service.run_cycle(
        market, evaluated_at=now + timedelta(hours=1), macro_context=_macro(now)
    )
    assert screen.call_args.kwargs["enforce_quality"] is True
    assert result["latest_cycle"]["watched_input_count"] == 0
    assert len(store.active_episodes()) == 1
    assert result["counts"]["confirmed_short"] == 0
    assert result["watching"][0]["latest_blocker"] == "market_data_or_eligibility_unavailable"
    result = await service.run_cycle(
        market, evaluated_at=now + timedelta(hours=73), macro_context=_macro(now)
    )
    assert result["counts"]["expired"] == 1


async def test_failed_cycle_withholds_confirmation_and_records_failure(tmp_path):
    now = datetime(2026, 11, 21, tzinfo=UTC)
    store = TopGainerShortWatchStore(tmp_path / "watch.sqlite3")
    ep = TopGainerWatchEpisode(
        episode_id="failed",
        symbol="TESTUSDT",
        base_asset="TEST",
        state=TopGainerWatchState.CONFIRMED_SHORT,
        enrolled_at=now,
        updated_at=now,
        anchor_price=Decimal("10"),
        anchor_24h_pct=Decimal("10"),
        peak_price=Decimal("10"),
        peak_24h_pct=Decimal("10"),
        latest_price=Decimal("10"),
        latest_24h_pct=Decimal("10"),
        gates_passed=True,
        checklist_passed=5,
        confirmed_opportunity={"suggested_entry": 10, "suggested_stop": 10.5},
        policy_version=TopGainerWatchPolicy().version,
    )
    store.save_cycle({"cycle_id": "initial", "evaluated_at": now.isoformat()}, [ep])
    service = TopGainerShortWatchService(store, load_settings())
    market = AsyncMock()
    market.instruments.side_effect = RuntimeError("upstream unavailable")
    with pytest.raises(RuntimeError, match="upstream unavailable"):
        await service.run_cycle(market, evaluated_at=now)
    snapshot = service.snapshot(evaluated_at=now)
    assert snapshot["counts"]["confirmed_short"] == 0
    assert snapshot["latest_cycle"]["status"] == "failed"
    assert snapshot["latest_cycle"]["error_type"] == "RuntimeError"
    assert snapshot["watching"][0]["latest_blocker"] == "cycle_failed"


def test_portal_binds_shared_macro_provider_after_snapshot_initialization(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from algotrader.config import Settings

    settings = Settings(_env_file=None, state_db_path=tmp_path / "watch.sqlite3")
    monkeypatch.setattr(portal_module, "_short_watch_service", None)
    monkeypatch.setattr(portal_module, "load_settings", lambda: settings)
    service = portal_module._get_short_watch_service()
    assert service.macro_provider is None
    provider = AsyncMock()
    runtime = SimpleNamespace(settings=settings, macro_provider=provider)
    assert portal_module._get_short_watch_service(runtime) is service
    assert service.macro_provider is provider
