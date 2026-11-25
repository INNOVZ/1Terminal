"""End-to-end unit, integration, security, and replay tests for `early-recovery-radar-v1`."""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from algotrader.adapters.external.models import MacroSentimentContext
from algotrader.api import portal as portal_module
from algotrader.application.auto_watchlist_service import AutoWatchlistService
from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.application.early_recovery import (
    recovery_publication_inputs_current,
    score_early_recovery,
)
from algotrader.application.radar_service import reconcile_radar_cohort
from algotrader.application.scan_recording import (
    capture_scan,
    screening_configuration,
    screening_identity,
    supported_screening_configuration,
)
from algotrader.application.scan_replay import replay_scan
from algotrader.config import Settings
from algotrader.domain.auto_watchlist import (
    AutoWatchStatus,
    build_auto_watch_episode,
    classify_radar_eviction,
)
from algotrader.domain.crypto_screener_models import ScreenerMode, SetupLabel
from algotrader.domain.early_recovery import (
    EARLY_RECOVERY_SCREENING_POLICY_VERSION,
    RECOVERY_PROBE_CAP,
    TITLES,
    EarlyRecoveryPolicy,
    evaluate_early_recovery,
)
from algotrader.domain.models import (
    AssetClass,
    Candle,
    DerivativesSnapshot,
    Direction,
    Instrument,
    InstrumentId,
    MarketKind,
    Quote,
    Venue,
)
from algotrader.domain.radar_lifecycle import same_radar_episode
from algotrader.domain.scan_observations import ScanDecisionJournal
from algotrader.domain.screener_policy import (
    SCREENING_POLICY_VERSION,
    publication_rejection_reasons,
    screening_policy_version_for,
)

D = Decimal


def _make_inst(symbol: str, tick: str = "0.0001") -> Instrument:
    base = symbol.removesuffix("USDT").removesuffix("USDC")
    return Instrument(
        id=InstrumentId(Venue.BINANCE_USDM, symbol),
        base_asset=base,
        quote_asset="USDT",
        asset_class=AssetClass.CRYPTO,
        market_kind=MarketKind.FUTURES,
        trading=True,
        price_tick=D(tick),
        quantity_step=D("1"),
        min_quantity=D("1"),
        min_notional=D("5"),
    )


_DEF_FINAL_CLOSE = Decimal("1.0220")
_DEF_FINAL_HIGH = Decimal("1.0240")
_DEF_FINAL_LOW = Decimal("1.0080")
_DEF_RECENT_LOW = Decimal("1.0020")
_DEF_PRIOR_LOW = Decimal("0.9850")
_DEF_HIGH_OVERHEAD = Decimal("1.2800")
_DEF_RECENT_QV = Decimal("240000")
_DEF_PRIOR_QV = Decimal("110000")
_DEF_QUOTE_PX = Decimal("1.0225")
_DEF_QUOTE_VOL = Decimal("3200000")
_DEF_SPREAD_BPS = Decimal("6")
_DEF_CHG_24H = Decimal("2.1")
_DEF_QUOTE_FUNDING = Decimal("-0.00015")
_DEF_DERIV_FUNDING = Decimal("-0.00008")
_DEF_OI_1H = Decimal("0.012")
_DEF_OI_4H = Decimal("0.032")
_DEF_OI_24H = Decimal("0.048")
_DEF_OI_7D = Decimal("-0.065")
_DEF_TAKER_1H = Decimal("1.11")
_DEF_TAKER_4H = Decimal("1.07")


def _build_recovery_history(
    now: datetime,
    *,
    n_1h: int = 240,
    n_4h: int = 180,
    final_close: Decimal = _DEF_FINAL_CLOSE,
    final_high: Decimal = _DEF_FINAL_HIGH,
    final_low: Decimal = _DEF_FINAL_LOW,
    recent_low_floor: Decimal = _DEF_RECENT_LOW,
    prior_low_floor: Decimal = _DEF_PRIOR_LOW,
    high_overhead: Decimal = _DEF_HIGH_OVERHEAD,
    recent_quote_vol: Decimal = _DEF_RECENT_QV,
    prior_quote_vol: Decimal = _DEF_PRIOR_QV,
) -> tuple[list[Candle], list[Candle], list[Candle]]:
    """Construct contiguous closed 1H, 4H, and BTC 1H series representing a sub-EMA50 pivot reclaim."""
    h_end = now.replace(minute=0, second=0, microsecond=0)
    candles_1h: list[Candle] = []
    for idx in range(n_1h):
        opened = h_end - timedelta(hours=n_1h - idx)
        closed = opened + timedelta(hours=1)
        rel = idx - n_1h  # -n_1h .. -1
        if rel < -60:
            # Earlier higher price regime (~1.18..1.28) so 4H EMA50 & 30d POC sit well above ~1.02
            base_px = D("1.1800") + D(str((idx % 7) * 0.012))
            hi = min(high_overhead, base_px + D("0.0250"))
            lo = base_px - D("0.0150")
            op = base_px + D("0.0020")
            cl = base_px
            qv = prior_quote_vol
        elif rel < -28:
            # Controlled pullback from 1.15 down into a quiet 1.0000 base
            step = D(str((rel + 60) / 32.0))
            cl = D("1.1500") - step * D("0.1500")
            op = cl + D("0.0030")
            hi = op + D("0.0050")
            lo = cl - D("0.0040")
            qv = prior_quote_vol
        elif rel < -4:
            # Quiet 24-hour bottom base around 0.9980..1.0010 so 1H EMA20 settles at ~1.001
            op = D("1.0000")
            cl = D("0.9990")
            hi = D("1.0060")
            lo = prior_low_floor if rel == -7 else D("0.9940")
            qv = prior_quote_vol
        elif rel < -1:
            # Last 3 bars before breakout: higher low (1.0020 > 0.9850) and tight 3-bar pivot high at 1.0140
            op = D("1.0050")
            cl = D("1.0100")
            hi = D("1.0140")
            lo = recent_low_floor
            qv = recent_quote_vol
        else:
            # Final closed 1h bar: green reclaim above 1.0140 pivot & 1H EMA20 with controlled wick
            op = D("1.0100")
            cl = final_close
            hi = final_high
            lo = max(recent_low_floor, final_low)
            qv = recent_quote_vol * D("1.35")

        vol = (qv / max(cl, D("0.01"))).quantize(D("0.01"))
        candles_1h.append(
            Candle(
                opened_at=opened,
                closed_at=closed,
                open=op,
                high=hi,
                low=lo,
                close=cl,
                volume=vol,
                quote_volume=qv,
                taker_buy_volume=(vol * D("0.56")).quantize(D("0.01")),
                taker_buy_quote_volume=(qv * D("0.56")).quantize(D("0.01")),
                closed=True,
            )
        )

    # Contiguous 4H candles ending at largest 4H boundary <= h_end
    q_end_hour = (h_end.hour // 4) * 4
    q_end = h_end.replace(hour=q_end_hour)
    candles_4h: list[Candle] = []
    for idx in range(n_4h):
        opened = q_end - timedelta(hours=4 * (n_4h - idx))
        closed = opened + timedelta(hours=4)
        rel = idx - n_4h
        if rel < -12:
            base_px = D("1.1900") + D(str((idx % 5) * 0.015))
            op = base_px
            cl = base_px + D("0.0050")
            hi = high_overhead if idx == n_4h - 40 else base_px + D("0.0250")
            lo = base_px - D("0.0180")
        elif rel < -2:
            step = D(str((rel + 12) / 10.0))
            op = D("1.1500") - step * D("0.1300")
            cl = op - D("0.0100")
            hi = op + D("0.0080")
            lo = cl - D("0.0080")
        else:
            op = D("1.0050")
            cl = final_close if rel == -1 else D("1.0080")
            hi = max(cl, D("1.0240"))
            lo = prior_low_floor if rel == -2 else recent_low_floor
        qv = D("600000")
        vol = (qv / max(cl, D("0.01"))).quantize(D("0.01"))
        candles_4h.append(
            Candle(
                opened_at=opened,
                closed_at=closed,
                open=op,
                high=hi,
                low=lo,
                close=cl,
                volume=vol,
                quote_volume=qv,
                closed=True,
            )
        )

    # Flat BTC 1H benchmark so token +2.0% 6h move gives ~+2.0pp relative strength vs BTC
    btc_1h: list[Candle] = []
    for idx in range(n_1h):
        opened = h_end - timedelta(hours=n_1h - idx)
        closed = opened + timedelta(hours=1)
        btc_1h.append(
            Candle(
                opened_at=opened,
                closed_at=closed,
                open=D("90000"),
                high=D("90150"),
                low=D("89850"),
                close=D("90000"),
                volume=D("100"),
                quote_volume=D("9000000"),
                closed=True,
            )
        )
    return candles_1h, candles_4h, btc_1h


def _make_quote(
    symbol: str,
    now: datetime,
    *,
    price: Decimal = _DEF_QUOTE_PX,
    quote_vol: Decimal = _DEF_QUOTE_VOL,
    spread_bps: Decimal = _DEF_SPREAD_BPS,
    chg_24h: Decimal = _DEF_CHG_24H,
    funding: Decimal = _DEF_QUOTE_FUNDING,
    observed_at: datetime | None = None,
) -> Quote:
    obs = observed_at or now
    half = price * spread_bps / D("20000")
    return Quote(
        instrument_id=InstrumentId(Venue.BINANCE_USDM, symbol),
        bid=price - half,
        ask=price + half,
        last=price,
        quote_volume_24h=quote_vol,
        observed_at=obs,
        high_24h=price * D("1.02"),
        low_24h=price * D("0.97"),
        price_change_pct_24h=chg_24h,
        funding_rate=funding,
        basis_pct=D("0.0010"),
        exchange_observed_at=obs,
        received_at=obs,
    )


def _make_derivs(
    now: datetime,
    *,
    funding: Decimal = _DEF_DERIV_FUNDING,
    oi_1h: Decimal = _DEF_OI_1H,
    oi_4h: Decimal = _DEF_OI_4H,
    oi_24h: Decimal = _DEF_OI_24H,
    oi_7d: Decimal | None = _DEF_OI_7D,
    taker_1h: Decimal | None = _DEF_TAKER_1H,
    taker_4h: Decimal | None = _DEF_TAKER_4H,
    degraded: bool = False,
    observed_at: datetime | None = None,
) -> DerivativesSnapshot:
    return DerivativesSnapshot(
        funding_rate=funding,
        basis_rate=D("0.0005"),
        open_interest=D("5000000"),
        open_interest_change_1h=oi_1h,
        taker_buy_sell_ratio=taker_1h,
        bid_depth_notional=D("100000"),
        ask_depth_notional=D("100000"),
        open_interest_change_4h=oi_4h,
        open_interest_change_24h=oi_24h,
        open_interest_change_7d=oi_7d,
        taker_buy_sell_ratio_4h=taker_4h,
        taker_buy_sell_ratio_24h=D("1.03"),
        degraded_telemetry=degraded,
        observed_at=observed_at or now,
    )


class _FakeMarket:
    venue = Venue.BINANCE_USDM

    def __init__(
        self,
        instruments: list[Instrument],
        quotes: list[Quote],
        candles_by_sym: dict[str, tuple[list[Candle], list[Candle]]],
        derivs_by_sym: dict[str, DerivativesSnapshot],
        macro_at: datetime,
    ) -> None:
        self._instruments = instruments
        self._quotes = quotes
        self._candles = candles_by_sym
        self._derivs = derivs_by_sym
        self._macro_at = macro_at
        self.deriv_calls: list[str] = []

    async def instruments(self) -> list[Instrument]:
        return list(self._instruments)

    async def quotes(self, instruments: Any) -> list[Quote]:
        wanted = {i.id.symbol for i in instruments}
        return [q for q in self._quotes if q.instrument_id.symbol in wanted]

    async def candles(self, instrument: Instrument, interval: str, limit: int) -> list[Candle]:
        sym = instrument.id.symbol
        if interval == "1h":
            return list(self._candles[sym][0][-limit:])
        if interval == "4h":
            return list(self._candles[sym][1][-limit:])
        return []

    async def derivatives_snapshot(self, instrument: Instrument) -> DerivativesSnapshot:
        sym = instrument.id.symbol
        self.deriv_calls.append(sym)
        if sym not in self._derivs:
            raise TimeoutError("derivatives missing")
        return self._derivs[sym]

    async def get_context(self) -> MacroSentimentContext:
        return MacroSentimentContext(observed_at=self._macro_at)


@pytest.mark.asyncio
async def test_flag_off_exact_parity_and_identity() -> None:
    now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    default_settings = Settings(_env_file=None)
    assert default_settings.early_recovery_enabled is False
    assert screening_policy_version_for(default_settings) == SCREENING_POLICY_VERSION
    assert screening_identity(default_settings)["policy"] == SCREENING_POLICY_VERSION

    cfg_off = screening_configuration(default_settings)
    assert "early_recovery_enabled" not in cfg_off
    assert supported_screening_configuration(cfg_off) is True

    enabled_settings = Settings(_env_file=None, early_recovery_enabled=True)
    assert screening_policy_version_for(enabled_settings) == EARLY_RECOVERY_SCREENING_POLICY_VERSION
    cfg_on = screening_configuration(enabled_settings)
    assert cfg_on["early_recovery_enabled"] is True
    assert supported_screening_configuration(cfg_on, allow_early_recovery=False) is False
    assert supported_screening_configuration(cfg_on, allow_early_recovery=True) is True

    c1h, c4h, btc_1h = _build_recovery_history(now)
    btc_inst = _make_inst("BTCUSDT", "0.1")
    rec_inst = _make_inst("RECVUSDT")
    mkt = _FakeMarket(
        [btc_inst, rec_inst],
        [_make_quote("BTCUSDT", now, price=D("90000"), funding=D("0")), _make_quote("RECVUSDT", now)],
        {"BTCUSDT": (btc_1h, c4h), "RECVUSDT": (c1h, c4h)},
        {"RECVUSDT": _make_derivs(now)},
        now,
    )

    j_off = ScanDecisionJournal()
    scr_off = CryptoOpportunityScreener(
        default_settings, mkt, evaluated_at=now, journal=j_off, feature_contract="discovery_v2"
    )
    res_off = await scr_off.screen(mkt, mkt, mode=ScreenerMode.ALL, enforce_quality=True)
    assert res_off == []
    assert "recovery_probe_admitted" not in scr_off.scan_counts
    assert all(ev.get("stage") != "early_recovery_gates" for ev in j_off.snapshot())


def test_stage1_unknown_r5_and_stage2_telemetry_gates() -> None:
    now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    c1h, c4h, btc_1h = _build_recovery_history(now)
    inst = _make_inst("RECVUSDT")
    quote = _make_quote("RECVUSDT", now)
    notes: list[tuple[str, str, str]] = []

    opp_s1, eval_s1 = score_early_recovery(
        inst,
        quote,
        c1h,
        c4h,
        btc_1h,
        now=now,
        derivatives=None,
        is_btc_cascade=False,
        macro_multiplier=D("1.0"),
        macro_narrative="Neutral",
        beta=D("1.0"),
        residual_24h=D("0.02"),
        is_discovery_tier=True,
        note=lambda s, st, r, **_kw: notes.append((s, st, r)),
    )
    assert eval_s1.gates == (True, True, True, True, None)
    assert eval_s1.structurally_ready is True
    assert eval_s1.confirmed is False
    assert opp_s1 is not None
    assert opp_s1.checklist_verification is None
    assert publication_rejection_reasons(opp_s1) == ("recovery_requires_five_current_gates",)
    assert recovery_publication_inputs_current(opp_s1.recovery_evidence, now) is False

    # Stale, future-skewed, or degraded Stage-2 telemetry must remain unknown (None) and fail closed
    for bad_derivs in (
        _make_derivs(now, degraded=True),
        _make_derivs(now, observed_at=now - timedelta(minutes=10)),
        _make_derivs(now, observed_at=now + timedelta(seconds=30)),
        replace(_make_derivs(now), open_interest_change_4h=None),
    ):
        opp_bad, eval_bad = score_early_recovery(
            inst,
            quote,
            c1h,
            c4h,
            btc_1h,
            now=now,
            derivatives=bad_derivs,
            is_btc_cascade=False,
            macro_multiplier=D("1.0"),
            macro_narrative="Neutral",
            beta=D("1.0"),
            residual_24h=D("0.02"),
            is_discovery_tier=True,
            note=lambda *_a, **_kw: None,
        )
        assert eval_bad.gates[4] is None
        assert eval_bad.confirmed is False
        assert opp_bad is None

    # Fresh Stage-2 confirmation passes all 5 named recovery gates without fabricating legacy conviction_factors
    opp_s2, eval_s2 = score_early_recovery(
        inst,
        quote,
        c1h,
        c4h,
        btc_1h,
        now=now,
        derivatives=_make_derivs(now),
        is_btc_cascade=False,
        macro_multiplier=D("1.0"),
        macro_narrative="Neutral",
        beta=D("1.0"),
        residual_24h=D("0.02"),
        is_discovery_tier=True,
        note=lambda *_a, **_kw: None,
    )
    assert eval_s2.confirmed is True
    assert opp_s2 is not None
    assert opp_s2.checklist_verification is None
    assert tuple(c[0] for c in opp_s2.checklist) == TITLES
    assert all(c[1] is True for c in opp_s2.checklist)
    assert publication_rejection_reasons(opp_s2) == ()
    assert recovery_publication_inputs_current(opp_s2.recovery_evidence, now) is True
    payload = opp_s2.as_dict()
    assert payload["conviction_factors"] == {}
    assert "no calibrated" in str(payload["score_semantics"]).lower()


def test_fakeout_and_vulnerability_rejections() -> None:
    now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    c1h, c4h, btc_1h = _build_recovery_history(now)
    q_ok = _make_quote("RECVUSDT", now)
    d_ok = _make_derivs(now)

    # 1. Short-covering-only bounce (negative/flat OI despite green reclaim bar) -> R5 fails
    ev_sc = evaluate_early_recovery(
        c1h,
        c4h,
        btc_1h,
        now=now,
        quote=q_ok,
        tick=D("0.0001"),
        derivatives=_make_derivs(now, oi_24h=D("-0.01"), oi_4h=D("0.002")),
    )
    assert ev_sc.structurally_ready is True
    assert ev_sc.gates[4] is False
    assert ev_sc.confirmed is False

    # 2. Acute 1h OI flush (< -2.5%) -> R5 fails
    ev_liq = evaluate_early_recovery(
        c1h,
        c4h,
        btc_1h,
        now=now,
        quote=q_ok,
        tick=D("0.0001"),
        derivatives=_make_derivs(now, oi_1h=D("-0.031")),
    )
    assert ev_liq.gates[4] is False

    # 3. Crowded positive funding (> +1.5 bps) -> R5 fails
    ev_fund = evaluate_early_recovery(
        c1h,
        c4h,
        btc_1h,
        now=now,
        quote=q_ok,
        tick=D("0.0001"),
        derivatives=_make_derivs(now, funding=D("0.00022")),
    )
    assert ev_fund.gates[4] is False

    # 4. Upper-wick bull trap (> 0.45 wick ratio on significant bar) -> R2 fails
    c1h_wick, c4h_w, btc_w = _build_recovery_history(
        now, final_close=D("1.0160"), final_high=D("1.0450"), final_low=D("1.0080")
    )
    ev_wick = evaluate_early_recovery(
        c1h_wick,
        c4h_w,
        btc_w,
        now=now,
        quote=_make_quote("RECVUSDT", now, price=D("1.0165")),
        tick=D("0.0001"),
        derivatives=d_ok,
    )
    assert ev_wick.gates[1] is False
    assert ev_wick.plan is None

    # 5. Severe markdown collapse (7d <= -35% near 48h low) -> R1 fails
    c1h_knife = list(c1h)
    c1h_knife[-168] = replace(
        c1h_knife[-168], open=D("2.1000"), high=D("2.1200"), low=D("2.0500"), close=D("2.0800")
    )
    for k in range(-48, -1):
        c1h_knife[k] = replace(
            c1h_knife[k], low=D("1.0100"), open=D("1.0120"), close=D("1.0130"), high=D("1.0140")
        )
    ev_knife = evaluate_early_recovery(
        c1h_knife,
        c4h,
        btc_1h,
        now=now,
        quote=q_ok,
        tick=D("0.0001"),
        derivatives=d_ok,
    )
    assert ev_knife.reason == "severe_markdown_collapse"
    assert ev_knife.structurally_ready is False

    # 6. Stale quote, future quote, wide spread, low volume, and BTC cascade each fail closed
    assert (
        evaluate_early_recovery(
            c1h,
            c4h,
            btc_1h,
            now=now,
            quote=_make_quote("RECVUSDT", now, observed_at=now - timedelta(minutes=10)),
            tick=D("0.0001"),
            derivatives=d_ok,
        ).reason
        == "quote_or_tick_unavailable"
    )
    assert (
        evaluate_early_recovery(
            c1h,
            c4h,
            btc_1h,
            now=now,
            quote=_make_quote("RECVUSDT", now, observed_at=now + timedelta(seconds=20)),
            tick=D("0.0001"),
            derivatives=d_ok,
        ).reason
        == "quote_or_tick_unavailable"
    )
    assert (
        evaluate_early_recovery(
            c1h,
            c4h,
            btc_1h,
            now=now,
            quote=_make_quote("RECVUSDT", now, spread_bps=D("35")),
            tick=D("0.0001"),
            derivatives=d_ok,
        ).gates[3]
        is False
    )
    assert (
        evaluate_early_recovery(
            c1h,
            c4h,
            btc_1h,
            now=now,
            quote=_make_quote("RECVUSDT", now, quote_vol=D("800000")),
            tick=D("0.0001"),
            derivatives=d_ok,
        ).gates[3]
        is False
    )
    assert (
        evaluate_early_recovery(
            c1h,
            c4h,
            btc_1h,
            now=now,
            quote=q_ok,
            tick=D("0.0001"),
            derivatives=d_ok,
            is_btc_cascade=True,
        ).reason
        == "btc_velocity_cascade_veto"
    )


def test_bracket_tick_rounding_and_overhead_resistance_room() -> None:
    now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    c1h, c4h, btc_1h = _build_recovery_history(now)
    tick = D("0.0005")
    ev = evaluate_early_recovery(
        c1h,
        c4h,
        btc_1h,
        now=now,
        quote=_make_quote("RECVUSDT", now),
        tick=tick,
        derivatives=_make_derivs(now),
    )
    assert ev.confirmed is True
    assert ev.plan is not None
    p = ev.plan
    assert D("0") < p.stop < c1h[-1].close < p.entry < p.t1 < p.t2 < p.t3
    assert p.t1 <= p.first_resistance
    for level in (p.entry, p.stop, p.t1, p.t2, p.t3):
        assert (level / tick) == (level / tick).to_integral_value()

    # If overhead structural resistance (EMA50_4h / POC_30d) sits too close (< 1.5R), R4 must reject!
    c1h_tight, c4h_tight, btc_tight = _build_recovery_history(now, high_overhead=D("1.0450"))
    # Compress earlier 4H bars to ~1.032 so EMA50_4h & POC_30d sit right on top of entry (~1.023)
    c4h_capped = [
        replace(
            c,
            open=min(c.open, D("1.0330")),
            high=min(c.high, D("1.0400")),
            low=min(c.low, D("1.0250")) if i < len(c4h_tight) - 2 else c.low,
            close=min(c.close, D("1.0320")) if i < len(c4h_tight) - 1 else c.close,
        )
        for i, c in enumerate(c4h_tight)
    ]
    ev_capped = evaluate_early_recovery(
        c1h_tight,
        c4h_capped,
        btc_tight,
        now=now,
        quote=_make_quote("RECVUSDT", now),
        tick=D("0.0001"),
        derivatives=_make_derivs(now),
    )
    assert ev_capped.gates[3] is False
    assert ev_capped.plan is None


def test_policy_ablation_family_v1_vs_inherited_long() -> None:
    now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    c1h, c4h, btc_1h = _build_recovery_history(now)
    inst = _make_inst("RECVUSDT")
    quote = _make_quote("RECVUSDT", now)
    # Path A with 24h+4h OI expansion, 1h OI = -0.5% (>= -2.5%), 4h taker = 1.03, 1h taker = 1.09, legacy 7d OI = -6.5%
    derivs = _make_derivs(
        now,
        oi_7d=D("-0.065"),
        oi_24h=D("0.035"),
        oi_4h=D("0.018"),
        oi_1h=D("-0.005"),
        taker_4h=D("1.03"),
        taker_1h=D("1.09"),
    )

    opp_fam, _ = score_early_recovery(
        inst,
        quote,
        c1h,
        c4h,
        btc_1h,
        now=now,
        derivatives=derivs,
        is_btc_cascade=False,
        macro_multiplier=D("1.0"),
        macro_narrative="Neutral",
        beta=D("1.0"),
        residual_24h=D("0.02"),
        is_discovery_tier=True,
        note=lambda *_a, **_kw: None,
        policy=EarlyRecoveryPolicy(family_policy_mode="family_v1"),
    )
    assert opp_fam is not None
    assert publication_rejection_reasons(opp_fam) == ()

    opp_inh, _ = score_early_recovery(
        inst,
        quote,
        c1h,
        c4h,
        btc_1h,
        now=now,
        derivatives=derivs,
        is_btc_cascade=False,
        macro_multiplier=D("1.0"),
        macro_narrative="Neutral",
        beta=D("1.0"),
        residual_24h=D("0.02"),
        is_discovery_tier=True,
        note=lambda *_a, **_kw: None,
        policy=EarlyRecoveryPolicy(family_policy_mode="inherited_long"),
    )
    assert opp_inh is not None
    inh_reasons = publication_rejection_reasons(opp_inh)
    assert "bleeding_7d_oi_without_fresh_inflow" in inh_reasons
    assert "insufficient_taker_buyer_absorption" in inh_reasons


@pytest.mark.asyncio
async def test_core_publication_rejection_falls_back_to_recovery_with_single_deriv_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    c1h, c4h, btc_1h = _build_recovery_history(now)
    btc_inst = _make_inst("BTCUSDT", "0.1")
    rec_inst = _make_inst("FALLBACKUSDT")
    mkt = _FakeMarket(
        [btc_inst, rec_inst],
        [_make_quote("BTCUSDT", now, price=D("90000"), funding=D("0")), _make_quote("FALLBACKUSDT", now)],
        {"BTCUSDT": (btc_1h, c4h), "FALLBACKUSDT": (c1h, c4h)},
        {"FALLBACKUSDT": _make_derivs(now)},
        now,
    )

    settings = Settings(_env_file=None, early_recovery_enabled=True)
    journal = ScanDecisionJournal()
    screener = CryptoOpportunityScreener(
        settings, mkt, evaluated_at=now, journal=journal, feature_contract="discovery_v2"
    )

    # Simulate a core candidate that survives Stage 1 (score 75) so it enters stage2_shortlist,
    # but gets WAIT_FOR_RETEST_ONLY in Stage 2 so core fails publication_rejection_reasons!
    orig_score_rec, _ = score_early_recovery(
        rec_inst,
        _make_quote("FALLBACKUSDT", now),
        c1h,
        c4h,
        btc_1h,
        now=now,
        derivatives=_make_derivs(now),
        is_btc_cascade=False,
        macro_multiplier=D("1.0"),
        macro_narrative="Neutral",
        beta=D("1.0"),
        residual_24h=D("0.02"),
        is_discovery_tier=False,
        note=lambda *_a, **_kw: None,
    )
    assert orig_score_rec is not None

    def _fake_core_accum(**kwargs: Any) -> Any:
        if kwargs["inst"].id.symbol != "FALLBACKUSDT":
            return None
        return replace(
            orig_score_rec,
            setup_label=SetupLabel.VCP_COMPRESSION_LONG,
            execution_mode="WAIT_FOR_RETEST_ONLY" if kwargs["derivatives"] is not None else "RETEST_ACCUMULATION",
            recovery_evidence=None,
        )

    monkeypatch.setattr(screener, "_score_accumulation_setup", _fake_core_accum)
    res = await screener.screen(mkt, mkt, mode=ScreenerMode.ALL, enforce_quality=True)

    assert len(res) == 1
    assert res[0].instrument.id.symbol == "FALLBACKUSDT"
    assert res[0].setup_label is SetupLabel.RECOVERY_RECLAIM_LONG
    # At most ONE derivatives call per symbol!
    assert mkt.deriv_calls.count("FALLBACKUSDT") == 1
    dedup_reasons = [
        ev["reason"] for ev in journal.snapshot() if ev.get("stage") == "deduplication"
    ]
    assert "selected_recovery_after_core_rejection" in dedup_reasons


@pytest.mark.asyncio
async def test_long_and_short_coexistence_under_mode_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    c1h, c4h, btc_1h = _build_recovery_history(now)
    btc_inst = _make_inst("BTCUSDT", "0.1")
    rec_inst = _make_inst("RECVUSDT")
    short_inst = _make_inst("SHRTUSDT")
    mkt = _FakeMarket(
        [btc_inst, rec_inst, short_inst],
        [
            _make_quote("BTCUSDT", now, price=D("90000"), funding=D("0")),
            _make_quote("RECVUSDT", now),
            _make_quote("SHRTUSDT", now, price=D("2.0000"), funding=D("0.00030")),
        ],
        {"BTCUSDT": (btc_1h, c4h), "RECVUSDT": (c1h, c4h), "SHRTUSDT": (c1h, c4h)},
        {"RECVUSDT": _make_derivs(now), "SHRTUSDT": _make_derivs(now, oi_7d=D("0.15"))},
        now,
    )

    settings = Settings(_env_file=None, early_recovery_enabled=True)
    screener = CryptoOpportunityScreener(
        settings, mkt, evaluated_at=now, feature_contract="discovery_v2"
    )
    sample_rec, _ = score_early_recovery(
        rec_inst,
        _make_quote("RECVUSDT", now),
        c1h,
        c4h,
        btc_1h,
        now=now,
        derivatives=_make_derivs(now),
        is_btc_cascade=False,
        macro_multiplier=D("1.0"),
        macro_narrative="Neutral",
        beta=D("1.0"),
        residual_24h=D("0.02"),
        is_discovery_tier=False,
        note=lambda *_a, **_kw: None,
    )
    assert sample_rec is not None

    def _fake_dist(**kwargs: Any) -> Any:
        if kwargs["inst"].id.symbol != "SHRTUSDT":
            return None
        return replace(
            sample_rec,
            instrument=short_inst,
            direction=Direction.SHORT,
            setup_label=SetupLabel.DISTRIBUTION_SHORT,
            current_price=D("2.0000"),
            suggested_entry=D("1.9900"),
            suggested_stop=D("2.0500"),
            suggested_targets=(D("1.8800"), D("1.8200"), D("1.7500")),
            risk_reward_ratio=D("2.83"),
            oi_change_7d=D("0.15"),
            recovery_evidence=None,
        )

    monkeypatch.setattr(screener, "_score_distribution_setup", _fake_dist)
    res = await screener.screen(mkt, mkt, mode=ScreenerMode.ALL, enforce_quality=True)
    by_sym = {o.instrument.id.symbol: o for o in res}
    assert set(by_sym) == {"RECVUSDT", "SHRTUSDT"}
    assert by_sym["RECVUSDT"].setup_label is SetupLabel.RECOVERY_RECLAIM_LONG
    assert by_sym["SHRTUSDT"].setup_label is SetupLabel.DISTRIBUTION_SHORT


@pytest.mark.asyncio
async def test_stage2_probe_cap_shortlist_preservation_and_permutation_invariance() -> None:
    now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    c1h, c4h, btc_1h = _build_recovery_history(
        now, recent_quote_vol=D("145000"), prior_quote_vol=D("110000")
    )
    c1h_flat = [replace(c, open=D("1.2000"), high=D("1.2050"), low=D("1.1950"), close=D("1.2000")) for c in c1h]
    c4h_flat = [replace(c, open=D("1.2000"), high=D("1.2050"), low=D("1.1950"), close=D("1.2000")) for c in c4h]

    btc_inst = _make_inst("BTCUSDT", "0.1")
    rec_insts = [_make_inst(f"REC{i:02d}USDT") for i in range(12)]
    base_probe_insts = [_make_inst(f"PRB{i:02d}USDT") for i in range(32)]

    quotes = [_make_quote("BTCUSDT", now, price=D("90000"), funding=D("0"))]
    candles_map: dict[str, tuple[list[Candle], list[Candle]]] = {"BTCUSDT": (btc_1h, c4h)}
    derivs_map: dict[str, DerivativesSnapshot] = {}

    for idx, inst in enumerate(rec_insts):
        sym = inst.id.symbol
        # Neutral funding so recovery symbols enter Stage 2 purely via the <=8 recovery lane
        q_rec = replace(
            _make_quote(sym, now, quote_vol=D(str(2_000_000 + idx * 100_000)), funding=D("0")),
            basis_pct=D("0"),
        )
        quotes.append(q_rec)
        candles_map[sym] = (c1h, c4h)
        derivs_map[sym] = _make_derivs(now)

    for idx, inst in enumerate(base_probe_insts):
        sym = inst.id.symbol
        quotes.append(
            _make_quote(
                sym,
                now,
                price=D("1.2000"),
                quote_vol=D(str(3_000_000 + idx * 50_000)),
                funding=D("-0.00040"),
            )
        )
        candles_map[sym] = (c1h_flat, c4h_flat)
        derivs_map[sym] = _make_derivs(now, oi_24h=D("-0.02"), oi_4h=D("-0.01"))

    settings = Settings(_env_file=None, early_recovery_enabled=True, stage2_capacity_rollover_enabled=False)
    all_insts = [btc_inst, *rec_insts, *base_probe_insts]

    published_orders: list[list[str]] = []
    admitted_rec_sets: list[list[str]] = []
    for seed in (1, 42, 99):
        rng = random.Random(seed)
        shuffled = list(all_insts)
        rng.shuffle(shuffled)
        mkt = _FakeMarket(shuffled, quotes, candles_map, derivs_map, now)
        journal = ScanDecisionJournal()
        scr = CryptoOpportunityScreener(
            settings, mkt, evaluated_at=now, journal=journal, feature_contract="discovery_v2"
        )
        res = await scr.screen(mkt, mkt, mode=ScreenerMode.ALL, enforce_quality=True)
        assert scr.scan_counts["recovery_stage1_ready"] == 12
        assert scr.scan_counts["recovery_probe_admitted"] == RECOVERY_PROBE_CAP == 8
        assert scr.scan_counts["recovery_probe_cap_overflow"] == 4
        assert scr.scan_counts["stage2_shortlist"] + scr.scan_counts["stage2_probes"] <= 75
        assert scr.scan_counts["recovery_displaced_probes"] > 0
        # Every symbol enriched at most once
        assert len(mkt.deriv_calls) == len(set(mkt.deriv_calls))

        rec_adm = sorted(
            ev["symbol"]
            for ev in journal.snapshot()
            if ev.get("stage") == "recovery_allocation" and ev.get("reason") == "admitted_within_cap"
        )
        admitted_rec_sets.append(rec_adm)
        published_orders.append([o.instrument.id.symbol for o in res])

    assert admitted_rec_sets[0] == admitted_rec_sets[1] == admitted_rec_sets[2]
    assert published_orders[0] == published_orders[1] == published_orders[2]


@pytest.mark.asyncio
async def test_lifecycle_retention_watchlist_and_rollback_filtering(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    c1h, c4h, btc_1h = _build_recovery_history(now)
    inst = _make_inst("RECVUSDT")
    btc_inst = _make_inst("BTCUSDT", "0.1")
    quote = _make_quote("RECVUSDT", now)
    opp, _ = score_early_recovery(
        inst,
        quote,
        c1h,
        c4h,
        btc_1h,
        now=now,
        derivatives=_make_derivs(now),
        is_btc_cascade=False,
        macro_multiplier=D("1.0"),
        macro_narrative="Neutral",
        beta=D("1.0"),
        residual_24h=D("0.02"),
        is_discovery_tier=True,
        note=lambda *_a, **_kw: None,
    )
    assert opp is not None
    cand = opp.as_dict()
    cand["first_seen_at"] = now.isoformat()
    orig_entry = cand["suggested_entry"]
    orig_stop = cand["suggested_stop"]
    orig_targets = list(cand["suggested_targets"])

    # 1. Retention 1h later while still below 4H EMA50 & 30d VAL: must retain 5/5 with frozen geometry!
    t1 = now + timedelta(hours=1)
    next_bar = Candle(
        opened_at=now,
        closed_at=t1,
        open=D("1.0220"),
        high=D("1.0230"),
        low=D("1.0160"),
        close=D("1.0210"),
        volume=D("230000"),
        quote_volume=D("235000"),
        closed=True,
    )
    btc_next = Candle(
        opened_at=now,
        closed_at=t1,
        open=D("90000"),
        high=D("90100"),
        low=D("89900"),
        close=D("90000"),
        volume=D("100"),
        quote_volume=D("9000000"),
        closed=True,
    )
    mkt_t1 = _FakeMarket(
        [btc_inst, inst],
        [_make_quote("BTCUSDT", t1, price=D("90000")), _make_quote("RECVUSDT", t1, price=D("1.0210"))],
        {"BTCUSDT": ([*btc_1h, btc_next], c4h), "RECVUSDT": ([*c1h, next_bar], c4h)},
        {"RECVUSDT": _make_derivs(t1)},
        t1,
    )
    events_ok: list[dict[str, Any]] = []
    retained = await reconcile_radar_cohort(
        [cand],
        set(),
        mkt_t1,
        now=t1,
        events=events_ok,
        early_recovery_enabled=True,
    )
    assert len(retained) == 1
    r_card = retained[0]
    assert r_card["gates_passed"] == 5
    assert r_card["radar_data_status"] == "current"
    assert r_card["suggested_entry"] == orig_entry
    assert r_card["suggested_stop"] == orig_stop
    assert r_card["suggested_targets"] == orig_targets
    assert [c["title"] for c in r_card["checklist"]] == list(TITLES)
    assert same_radar_episode(cand, r_card) is True

    # 2. Soft R5 flow dip (`taker = 0.86`, R1..R4 intact) -> evicted from 5/5 Radar & promoted to 48h WATCHLIST!
    mkt_soft = _FakeMarket(
        [btc_inst, inst],
        [_make_quote("BTCUSDT", t1, price=D("90000")), _make_quote("RECVUSDT", t1, price=D("1.0210"))],
        {"BTCUSDT": ([*btc_1h, btc_next], c4h), "RECVUSDT": ([*c1h, next_bar], c4h)},
        {"RECVUSDT": _make_derivs(t1, taker_1h=D("0.86"), taker_4h=D("0.85"))},
        t1,
    )
    events_soft: list[dict[str, Any]] = []
    ret_soft = await reconcile_radar_cohort(
        [cand],
        set(),
        mkt_soft,
        now=t1,
        events=events_soft,
        early_recovery_enabled=True,
    )
    assert len(ret_soft) == 1
    assert ret_soft[0]["failed_gates"] == ["Positioning Confirmation"]

    ep = build_auto_watch_episode(r_card, published_at=now, scan_id="scan-1")
    assert ep is not None
    eviction_evt = {
        "symbol": "RECVUSDT",
        "at": t1.isoformat(),
        "state": "INVALIDATED",
        "retained": False,
        "reason": "radar_gates_degraded",
        "failed_gates": ["Positioning Confirmation"],
        "candidate": ret_soft[0],
    }
    status_soft, _, summary_soft, _ = classify_radar_eviction(ep, event=eviction_evt, now=t1)
    assert status_soft is AutoWatchStatus.WATCHLIST
    assert summary_soft is not None and "Soft flow dip" in summary_soft

    # Severe seller dump (`taker = 0.72`) must HARD_INVALIDATE, never promote to Watchlist
    bad_cand = dict(ret_soft[0], radar_metrics={**ret_soft[0]["radar_metrics"], "taker_buy_sell_ratio": "0.7200"})
    status_dump, reason_dump, _, _ = classify_radar_eviction(
        ep, event={**eviction_evt, "candidate": bad_cand}, now=t1
    )
    assert status_dump is AutoWatchStatus.HARD_INVALIDATED
    assert "severe_seller_dump" in reason_dump

    # 3. Rollback (`early_recovery_enabled=False`) withholds recovery cards from Watchlist & Portal cache projections
    svc = AutoWatchlistService(tmp_path / "state.sqlite3")
    svc.sync_from_scan(published_opportunities=[r_card], radar_events=[], now=now, scan_id="s1")
    svc.sync_from_scan(published_opportunities=[], radar_events=[eviction_evt], now=t1, scan_id="s2")
    assert len(svc.build_response(now=t1, early_recovery_enabled=True)["watchlist"]) == 1
    assert len(svc.build_response(now=t1, early_recovery_enabled=False)["watchlist"]) == 0
    # Stored SQLite episode remains preserved
    assert len(svc.list_episodes(now=t1)) == 1

    monkeypatch.setattr(
        portal_module,
        "_screener_cache",
        {
            "timestamp": t1.isoformat(),
            "radar_lifecycle_version": portal_module.RADAR_PUBLICATION_VERSION,
            "screening_policy_version": SCREENING_POLICY_VERSION,
            "data_status": "current",
            "opportunities": [r_card],
            "qualifying_count": 1,
        },
    )
    st_off, proj_off = portal_module._project_public_screener_state(
        Settings(_env_file=None, early_recovery_enabled=False), now=t1
    )
    assert st_off == "current"
    assert proj_off["opportunities"] == []
    assert proj_off["qualifying_count"] == 0


@pytest.mark.asyncio
async def test_capture_and_replay_roundtrip_with_early_recovery_enabled() -> None:
    now = datetime.now(UTC)
    c1h, c4h, btc_1h = _build_recovery_history(now)
    btc_inst = _make_inst("BTCUSDT", "0.1")
    rec_inst = _make_inst("RECVUSDT")
    mkt = _FakeMarket(
        [btc_inst, rec_inst],
        [_make_quote("BTCUSDT", now, price=D("90000"), funding=D("0")), _make_quote("RECVUSDT", now)],
        {"BTCUSDT": (btc_1h, c4h), "RECVUSDT": (c1h, c4h)},
        {"RECVUSDT": _make_derivs(now)},
        now,
    )
    settings = Settings(_env_file=None, early_recovery_enabled=True)
    captured = await capture_scan(
        mkt,
        mkt,
        mkt,
        settings,
        {"mode": ScreenerMode.ALL, "min_score": D("50"), "max_symbols": 320, "enforce_quality": True},
        feature_contract="discovery_v2",
    )
    assert captured.failure is None
    assert captured.record.status == "complete"
    assert captured.record.identity["policy"] == EARLY_RECOVERY_SCREENING_POLICY_VERSION
    assert len(captured.opportunities) == 1
    assert captured.opportunities[0].setup_label is SetupLabel.RECOVERY_RECLAIM_LONG

    replayed = await replay_scan(captured.record)
    assert replayed.matches is True
