from __future__ import annotations

from decimal import Decimal
from typing import Any

from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.domain.crypto_screener_models import (
    CryptoMarketRegime,
    CryptoOpportunity,
    CryptoScreenerScoreBreakdown,
    ScreenerMode,
    SetupLabel,
)
from algotrader.domain.models import DerivativesSnapshot, Direction, MarketKind, Quote, Venue
from tests.conftest import make_instrument


async def test_oi_led_candidate_can_enter_stage2_without_stage1_setup(settings: Any, monkeypatch: Any) -> None:
    instrument = make_instrument(
        venue=Venue.BINANCE_USDM,
        market_kind=MarketKind.FUTURES,
        symbol="FUELUSDT",
    )

    class Market:
        async def instruments(self) -> list[Any]:
            return [instrument]

        async def quotes(self, _: Any) -> list[Quote]:
            return [
                Quote(
                    instrument_id=instrument.id,
                    bid=Decimal("0.999"),
                    ask=Decimal("1.001"),
                    last=Decimal("1"),
                    quote_volume_24h=Decimal("5000000"),
                    funding_rate=Decimal("-0.0002"),
                    basis_pct=Decimal("0.001"),
                )
            ]

    class Derivatives:
        async def derivatives_snapshot(self, _: Any) -> DerivativesSnapshot:
            return DerivativesSnapshot(
                funding_rate=Decimal("-0.0002"),
                basis_rate=Decimal("0.001"),
                open_interest=Decimal("5000000"),
                open_interest_change_1h=Decimal("0.03"),
                open_interest_change_4h=Decimal("0.04"),
                open_interest_change_7d=Decimal("0.15"),
                taker_buy_sell_ratio=Decimal("1.2"),
                bid_depth_notional=Decimal("100000"),
                ask_depth_notional=Decimal("100000"),
            )

    score = Decimal("80")
    opportunity = CryptoOpportunity(
        instrument=instrument,
        direction=Direction.LONG,
        regime=CryptoMarketRegime.VOLATILITY_SQUEEZE,
        setup_label=SetupLabel.SHORT_SQUEEZE_ABSORPTION_LONG,
        score=score,
        score_breakdown=CryptoScreenerScoreBreakdown(
            trend_score=score,
            momentum_score=score,
            derivatives_score=score,
            alpha_score=score,
            volume_score=score,
            structure_score=score,
            macro_multiplier=Decimal("1"),
            final_composite_score=score,
        ),
        current_price=Decimal("1"),
        atr14=Decimal("0.05"),
        suggested_entry=Decimal("1"),
        suggested_stop=Decimal("0.95"),
        suggested_targets=(Decimal("1.15"),),
        risk_reward_ratio=Decimal("3"),
        btc_beta=Decimal("1"),
        residual_return_24h=Decimal("0.02"),
        funding_rate=Decimal("-0.0002"),
        oi_change_1h=Decimal("0.03"),
        taker_ratio=Decimal("1.2"),
        rationale=("OI-led setup",),
        macro_narrative="neutral",
        conviction=score,
    )
    screener = CryptoOpportunityScreener(settings)

    async def evaluate(**kwargs: Any) -> list[CryptoOpportunity]:
        if kwargs.get("candle_cache") is not None:
            kwargs["candle_cache"][instrument.id.symbol] = ((), (), ())
        return [opportunity] if kwargs.get("explicit_derivatives") is not None else []

    class Macro:
        async def get_context(self) -> Any:
            return type("Context", (), {"narrative": lambda self: "neutral", "coi_score": None})()

    monkeypatch.setattr(screener, "_evaluate_single", evaluate)
    screener._macro_provider = Macro()  # type: ignore[assignment]

    result = await screener.screen(
        Market(),  # type: ignore[arg-type]
        Derivatives(),  # type: ignore[arg-type]
        max_symbols=1,
        min_score=Decimal("50"),
        mode=ScreenerMode.ALL,
    )

    assert [item.instrument.id.symbol for item in result] == ["FUELUSDT"]
    assert screener.scan_counts == {
        "eligible": 1,
        "liquid": 1,
        "selected": 1,
        "evaluated": 1,
        "failed": 0,
        "stage1_survivors": 0,
        "stage2_shortlist": 0,
        "stage2_probes": 1,
        "returned": 1,
    }


async def test_discovery_promotes_ready_and_vvi_setups_with_bounded_overflow(settings, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from algotrader.domain.scan_observations import ScanDecisionJournal
    from tests.test_screener_publication_policy import _opportunity

    instruments = [make_instrument(symbol=f"COIN{i:03}USDT") for i in range(80)]
    quotes = [Quote(i.id, Decimal("100"), Decimal("100"), Decimal("100"),
                    Decimal("5000000")) for i in instruments]
    market = SimpleNamespace(instruments=AsyncMock(return_value=instruments),
                             quotes=AsyncMock(return_value=quotes))
    derivatives = SimpleNamespace(derivatives_snapshot=AsyncMock(return_value=DerivativesSnapshot(
        funding_rate=None, basis_rate=None, open_interest=None, open_interest_change_1h=None,
        taker_buy_sell_ratio=None, bid_depth_notional=None, ask_depth_notional=None)))
    macro = SimpleNamespace(get_context=AsyncMock(return_value=SimpleNamespace(
        narrative=lambda: "neutral", coi_score=None)))
    journal = ScanDecisionJournal()
    screener = CryptoOpportunityScreener(settings, macro, journal=journal, feature_contract="discovery_v2")
    opportunity = _opportunity(Direction.LONG)

    async def evaluate(**kwargs):
        inst = kwargs["inst"]
        index = int(inst.id.symbol[4:7])
        if kwargs.get("explicit_derivatives") is not None:
            return [replace(opportunity, instrument=inst)]
        kwargs["candle_cache"][inst.id.symbol] = ((), (), ())
        if index >= 45:
            # A strong ignition without readiness must have its own admission path.
            if index == 79:
                screener._vvi_symbols.add(inst.id.symbol)
            else:
                screener._stage1_ready_symbols.add(inst.id.symbol)
            return []
        return [replace(opportunity, instrument=inst, score=Decimal("40"))]

    monkeypatch.setattr(screener, "_evaluate_single", evaluate)
    await screener.screen(market, derivatives, max_symbols=320, min_score=Decimal("50"), mode=ScreenerMode.ALL)
    assert screener.scan_counts["stage2_shortlist"] == 45
    assert screener.scan_counts["stage2_probes"] == 30
    assert screener.scan_counts["stage2_probe_overflow"] == 5
    assert derivatives.derivatives_snapshot.await_count == 75
    admissions = [x for x in journal.snapshot() if x["stage"] == "stage2" and x["reason"] == "admitted"]
    assert any(x["symbol"] == "COIN079USDT" and x["vvi_ignition"] for x in admissions)
