from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.application.recovery_screener import RecoveryScreener
from algotrader.domain.crypto_screener_models import ScreenerMode, SetupLabel
from algotrader.domain.execution_brackets import InvalidExecutionBracket
from algotrader.domain.indicators import resample_candles, snapshot
from algotrader.domain.models import Candle, DerivativesSnapshot, Direction, Quote
from algotrader.domain.recovery import RECOVERY_POLICY_VERSION, assess_recovery, recovery_bracket
from algotrader.domain.scan_observations import ScanDecisionJournal
from tests.conftest import make_instrument
from tests.test_capture_evidence import Macro

AT = datetime(2026, 11, 12, 4, tzinfo=UTC)


def recovery_history():
    candles = []
    for index in range(1920):
        price = Decimal("140")
        if index >= 1800:
            price = max(Decimal("100"), price - Decimal(index - 1800) * Decimal("0.75"))
        end = AT - timedelta(hours=1919 - index)
        volume = Decimal("100")
        candles.append(Candle(end - timedelta(hours=1), end, price, price + Decimal("0.2"),
                              price - Decimal("1"), price, volume, volume * price,
                              taker_buy_volume=volume * Decimal("0.6"),
                              taker_buy_quote_volume=volume * price * Decimal("0.6")))
    for offset in range(6):
        close = Decimal("99.6") + Decimal(offset) / 10
        high = min(Decimal("100.2"), Decimal("99.9") + Decimal(offset) / 10)
        if offset == 5:
            close, high = Decimal("100.5"), Decimal("100.6")
        old = candles[-6 + offset]
        candles[-6 + offset] = replace(old, open=Decimal("99.5") + Decimal(offset) / 10,
                                       high=high, low=Decimal("98.8") + Decimal(offset) / 5,
                                       close=close, volume=Decimal("300"), quote_volume=close * 300,
                                       taker_buy_volume=Decimal("180"), taker_buy_quote_volume=close * 180)
    four = []
    for index in range(0, len(candles), 4):
        group = candles[index:index + 4]
        four.append(Candle(group[0].opened_at, group[-1].closed_at, group[0].open,
                           max(c.high for c in group), min(c.low for c in group), group[-1].close,
                           sum((c.volume for c in group), Decimal("0")),
                           sum((c.quote_volume for c in group), Decimal("0"))))
    return candles[-360:], four


def derivative():
    return DerivativesSnapshot(Decimal("0.00005"), Decimal("0"), Decimal("100000"),
                               Decimal("0.01"), Decimal("1.2"), Decimal("10000"), Decimal("10000"),
                               open_interest_change_4h=Decimal("0.05"),
                               open_interest_change_7d=Decimal("0.05"),
                               taker_buy_sell_ratio_4h=Decimal("1.2"), observed_at=AT)


def assessment(deriv=None, **overrides):
    hourly, _ = recovery_history()
    snap = snapshot(hourly)
    instrument = make_instrument(symbol="RECUSDT")
    values = dict(quote=Quote(instrument.id, Decimal("100.49"), Decimal("100.51"),
                              Decimal("100.5"), Decimal("5000000"), observed_at=AT),
                  atr=snap.atr14, ema20=snap.ema20, ema50_4h=Decimal("130"),
                  poc_30d=Decimal("140"), tick_size=instrument.price_tick, emerging=True,
                  derivatives=deriv, cutoff=AT, decision_at=AT)
    values.update(overrides)
    return assess_recovery(hourly, **values)


def test_recovery_qualifies_below_older_references_with_structural_stop():
    result = assessment(derivative())
    assert result.ready and all(value for _, value in result.gates)
    bracket = result.bracket
    assert bracket.invalidation_stop < Decimal("98.8") < bracket.suggested_entry
    assert bracket.breakout_trigger > Decimal("100.5")
    assert bracket.r_t1 >= 2 and bracket.execution_mode == "CEILING_BREAKOUT"


@pytest.mark.parametrize("changes", [
    {"funding_rate": Decimal("0.000251")}, {"open_interest_change_1h": Decimal("-0.026")},
    {"open_interest_change_4h": Decimal("-0.001")}, {"taker_buy_sell_ratio_4h": Decimal("0.99")},
    {"taker_buy_sell_ratio_4h": None}, {"open_interest_change_4h": None},
    {"degraded_telemetry": True}, {"observed_at": AT - timedelta(seconds=301)},
    {"observed_at": AT + timedelta(seconds=6)}, {"funding_rate": Decimal("NaN")},
])
def test_recovery_flow_failures_remain_unconfirmed(changes):
    result = assessment(replace(derivative(), **changes))
    assert result.ready and result.gates[-1] == ("R5_DERIVATIVES_CONFIRMATION", False)


def test_recovery_unknown_flow_is_preliminary_and_finite_input_is_required():
    assert assessment().gates[-1][1] is False
    with pytest.raises(InvalidExecutionBracket):
        assessment(derivative(), atr=Decimal("NaN"))
    assert assessment(derivative(), emerging=False).ready is False


@pytest.mark.parametrize("local_low,resistance", [(Decimal("80"), Decimal("140")),
                                                  (Decimal("99"), Decimal("101"))])
def test_recovery_rejects_wide_structural_stop_or_insufficient_room(local_low, resistance):
    with pytest.raises(InvalidExecutionBracket):
        recovery_bracket(close=Decimal("100"), pivot=Decimal("99.9"), local_low=local_low,
                         atr=Decimal("1"), tick_size=Decimal("0.01"), older_resistance=(resistance,))


class RecoveryMarket:
    def __init__(self):
        self.instrument = make_instrument(symbol="RECUSDT")
        self.btc = make_instrument(symbol="BTCUSDT")
        self.hourly, self.four = recovery_history()
        self.derivative_calls = 0
        self.fail_derivatives = False

    async def instruments(self):
        return [self.instrument, self.btc]

    async def quotes(self, instruments):
        return [Quote(i.id, Decimal("100.49"), Decimal("100.51"), Decimal("100.5"),
                      Decimal("5000000") if i == self.instrument else Decimal("1000000"), observed_at=AT)
                for i in instruments]

    async def candles(self, instrument, interval, limit):
        if instrument == self.btc:
            return [replace(c, open=Decimal("100"), close=Decimal("100"), high=Decimal("101"),
                            low=Decimal("99")) for c in self.hourly[-limit:]]
        if interval == "1h":
            return self.hourly[-limit:]
        if interval == "4h":
            return self.four[-limit:]
        return resample_candles(self.four, "1w")[-limit:]

    async def derivatives_snapshot(self, instrument):
        self.derivative_calls += 1
        if self.fail_derivatives:
            return None
        return derivative()


async def test_normal_screen_adds_confirmed_recovery_and_preserves_control(settings):
    market = RecoveryMarket()
    core = CryptoOpportunityScreener(settings, Macro(), evaluated_at=AT, feature_contract="discovery_v2")
    args = dict(max_symbols=1, target_direction=Direction.LONG, enforce_quality=True)
    assert await core.screen(market, market, **args) == []
    journal = ScanDecisionJournal()
    challenger = RecoveryScreener(settings, Macro(), evaluated_at=AT, decision_at=AT, journal=journal)
    result = await challenger.screen(market, market, **args)
    assert len(result) == 1
    assert result[0].setup_label is SetupLabel.RECOVERY_RECLAIM_LONG
    assert result[0].research_policy_version == RECOVERY_POLICY_VERSION
    assert result[0].breakout_readiness_status == "RECOVERY_TRIGGER_PENDING"
    assert all(passed for _, passed, _ in result[0].checklist)
    assert any(e["reason"] == "no_long_archetype" for e in journal.snapshot())
    assert any(e["stage"] == "recovery_gates" and e["enriched"] for e in journal.snapshot())
    assert result[0].as_dict()["research_policy_version"] == RECOVERY_POLICY_VERSION


@pytest.mark.parametrize("enforce_quality", [True, False])
async def test_normal_screen_cannot_publish_unconfirmed_recovery(settings, enforce_quality):
    market = RecoveryMarket()
    market.fail_derivatives = True
    challenger = RecoveryScreener(settings, Macro(), evaluated_at=AT, decision_at=AT)
    assert await challenger.screen(market, market, max_symbols=1,
                                    target_direction=Direction.LONG, enforce_quality=enforce_quality) == []
    assert await challenger.screen(market, None, max_symbols=1,
                                    target_direction=Direction.LONG, enforce_quality=enforce_quality) == []


async def test_recovery_probe_admission_is_independent_of_preliminary_score(settings, monkeypatch):
    market = RecoveryMarket()
    journal = ScanDecisionJournal()
    challenger = RecoveryScreener(settings, Macro(), evaluated_at=AT, decision_at=AT, journal=journal)
    original = challenger._score_accumulation_setup

    def preliminary_below_floor(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, score=Decimal("30")) if result and kwargs.get("derivatives") is None else result

    monkeypatch.setattr(challenger, "_score_accumulation_setup", preliminary_below_floor)
    result = await challenger.screen(market, market, max_symbols=1,
                                     target_direction=Direction.LONG, enforce_quality=True)
    assert len(result) == 1
    assert challenger.scan_counts["stage2_shortlist"] == 0
    assert challenger.scan_counts["stage2_probes"] == 1
    assert market.derivative_calls == 1
    assert any(row["stage"] == "stage2" and row["reason"] == "admitted"
               and Decimal(row["additional_probe_priority"]) == 1000 for row in journal.snapshot())


@pytest.mark.parametrize("scale", [Decimal("1"), Decimal("0.0000000001")])
def test_recovery_bracket_preserves_scale_and_directional_ticks(scale):
    tick = Decimal("0.01") * scale
    result = recovery_bracket(close=Decimal("100") * scale, pivot=Decimal("99.9") * scale,
                              local_low=Decimal("99") * scale, atr=scale, tick_size=tick,
                              older_resistance=(Decimal("130") * scale,))
    assert result.breakout_trigger > Decimal("100") * scale
    assert all(price > 0 and price % tick == 0
               for price in (result.suggested_entry, result.invalidation_stop, *result.targets))


@pytest.mark.parametrize("overrides", [{"cutoff": AT - timedelta(seconds=1)},
                                       {"decision_at": AT - timedelta(seconds=1)}])
def test_recovery_rejects_future_candle_or_decision_boundary(overrides):
    with pytest.raises(InvalidExecutionBracket):
        assessment(derivative(), **overrides)


def test_recovery_rejects_large_rejection_wick():
    hourly, _ = recovery_history()
    snap = snapshot(hourly)
    hourly[-1] = replace(hourly[-1], high=hourly[-1].close + snap.atr14 * 10)
    inst = make_instrument()
    result = assess_recovery(
        hourly, quote=Quote(inst.id, Decimal("100.49"), Decimal("100.51"), Decimal("100.5"),
                            Decimal("5000000"), observed_at=AT),
        atr=snap.atr14, ema20=snap.ema20, ema50_4h=Decimal("130"), poc_30d=Decimal("140"),
        tick_size=inst.price_tick, emerging=True, derivatives=derivative(), cutoff=AT, decision_at=AT,
    )
    assert result.gates[1] == ("R2_CONTROLLED_EXTENSION", False)


async def test_recovery_rejects_eager_enrichment_breakout_mode(settings):
    market = RecoveryMarket()
    challenger = RecoveryScreener(settings, Macro(), evaluated_at=AT, decision_at=AT)
    with pytest.raises(ValueError, match="bounded accumulation"):
        await challenger.screen(market, market, mode=ScreenerMode.BREAKOUT)
    assert market.derivative_calls == 0


@pytest.mark.parametrize("price,failed_gate", [(Decimal("95"), "R4_LIQUIDITY_AND_RISK"),
                                              (Decimal("105"), "R2_CONTROLLED_EXTENSION")])
def test_live_quote_cannot_publish_a_breached_stop_or_chased_recovery(price, failed_gate):
    inst = make_instrument()
    quote = Quote(inst.id, price - Decimal("0.01"), price + Decimal("0.01"), price,
                  Decimal("5000000"), observed_at=AT)
    result = assessment(derivative(), quote=quote)
    assert result.ready is False
    assert dict(result.gates)[failed_gate] is False
