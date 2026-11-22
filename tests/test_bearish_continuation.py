"""Closed-bar continuation semantics and disabled-path integration regressions."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from algotrader.application.bearish_continuation import score_continuation
from algotrader.application.scan_recording import screening_configuration
from algotrader.config import Settings
from algotrader.domain.bearish_continuation import (
    POLICY_VERSION,
    ContinuationPlan,
    evaluate_continuation,
)
from algotrader.domain.crypto_screener_models import SetupLabel
from algotrader.domain.models import (
    Candle,
    DerivativesSnapshot,
    Direction,
    MarketKind,
    Quote,
    Venue,
)
from algotrader.domain.radar_lifecycle import reverify_candidate_gates, same_radar_episode
from algotrader.domain.screener_policy import publication_rejection_reasons
from tests.conftest import make_instrument

D = Decimal
NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)


def with_inputs(snapshot, **changes):
    from algotrader.domain.continuation_inputs import HOUR_MS, aligned_inputs

    result = replace(snapshot, **changes)
    observed = result.observed_at
    end = observed.replace(minute=0, second=0, microsecond=0)
    ms = int(end.timestamp() * 1000)
    oi = {ms: D("1000")}
    for hours, delta in (
        (1, result.open_interest_change_1h),
        (4, result.open_interest_change_4h),
        (168, result.open_interest_change_7d),
    ):
        if delta is not None and delta.is_finite() and delta > -1:
            oi[ms - hours * HOUR_MS] = D("1000") / (1 + delta)
    ratio1, ratio4 = result.taker_buy_sell_ratio, result.taker_buy_sell_ratio_4h
    taker = {}
    if ratio1 is not None and ratio1.is_finite() and ratio4 is not None and ratio4.is_finite():
        taker[ms - HOUR_MS] = (ratio1 * 100, D("100"))
        for offset in (2, 3, 4):
            taker[ms - offset * HOUR_MS] = ((ratio4 * 400 - ratio1 * 100) / 3, D("100"))
    ci = aligned_inputs(
        oi,
        taker,
        {"time": int(observed.timestamp() * 1000), "lastFundingRate": str(result.funding_rate)},
        int(observed.timestamp() * 1000),
        observed,
    )
    return replace(result, continuation_inputs=ci)


def fixture():
    h = []
    for i in range(160):
        close = D("106") + D(i % 6) / 10
        low = close - D("0.4")
        if i in (90, 110):
            low = D("100")
        if i == 50:
            low = D("86")
        opened = NOW - timedelta(hours=160 - i)
        h.append(
            Candle(
                opened,
                opened + timedelta(hours=1),
                close + D("0.1"),
                close + D("0.3"),
                low,
                close,
                D("100"),
                closed=True,
            )
        )
    prefix = [
        replace(
            h[0],
            opened_at=h[0].opened_at - timedelta(hours=176 - i),
            closed_at=h[0].closed_at - timedelta(hours=176 - i),
        )
        for i in range(176)
    ]
    h = prefix + h
    # A prior known support, a distinct break, then a later failed reclaim.
    h[-2] = replace(h[-2], open=D("101"), high=D("101.2"), low=D("99.1"), close=D("99.5"))
    h[-1] = replace(
        h[-1], open=D("99.8"), high=D("100.1"), low=D("99.2"), close=D("99.4"), volume=D("150")
    )
    q = []
    for i in range(80):
        close = D("140") - D(i) * D("0.45") + D([0, 1, 2, 1, 0, -1, -2, -1][i % 8])
        low = close - D("0.4")
        if i == 68:
            low = D("90")
        if i == 76:
            low = D("88")
        opened = NOW - timedelta(hours=4 * (80 - i))
        q.append(
            Candle(
                opened,
                opened + timedelta(hours=4),
                close + D("0.1"),
                close + D("0.4"),
                low,
                close,
                D("400"),
                closed=True,
            )
        )
    q[-1] = replace(q[-1], low=D("99.1"), close=D("99.4"))
    inst = make_instrument(
        venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="BEARUSDT"
    )
    quote = Quote(inst.id, D("99.39"), D("99.41"), D("99.4"), D("5000000"), NOW)
    derivatives = DerivativesSnapshot(
        funding_rate=D("0.0001"),
        basis_rate=D("0"),
        open_interest=D("100000"),
        open_interest_change_1h=D("0.01"),
        open_interest_change_4h=D("0.02"),
        open_interest_change_7d=D("0"),
        taker_buy_sell_ratio=D("0.85"),
        taker_buy_sell_ratio_4h=D("0.9"),
        bid_depth_notional=D("100000"),
        ask_depth_notional=D("100000"),
        observed_at=NOW,
    )
    return h, q, inst, quote, with_inputs(derivatives)


def evaluate(h, q, quote, derivatives=None, **kwargs):
    return evaluate_continuation(
        h, q, now=NOW, price=quote.last, tick=D("0.01"), derivatives=derivatives, **kwargs
    )


def test_structural_admission_and_derivatives_confirmation():
    h, q, _, quote, derivatives = fixture()
    preliminary = evaluate(h, q, quote)
    assert preliminary.structurally_ready, preliminary
    assert preliminary.gates[-1] is None
    assert not preliminary.confirmed
    confirmed = evaluate(h, q, quote, derivatives)
    assert confirmed.confirmed, confirmed
    plan = confirmed.plan
    assert plan.support_known_at < plan.break_at < plan.rejection_at <= NOW
    assert 0 < plan.target < plan.entry < plan.stop
    assert plan.net_rr >= D("1.5")
    assert ContinuationPlan.from_dict(plan.as_dict()) == plan


@pytest.mark.parametrize(
    "changes",
    [
        {"funding_rate": D("-0.0001")},
        {"open_interest_change_1h": D("-0.03")},
        {"open_interest_change_4h": None},
        {"taker_buy_sell_ratio": D("1.2")},
        {"taker_buy_sell_ratio": D("NaN")},
        {"degraded_telemetry": True},
        {"observed_at": NOW - timedelta(minutes=6)},
    ],
)
def test_unavailable_or_adverse_derivatives_never_confirm(changes):
    h, q, _, quote, derivatives = fixture()
    assert not evaluate(h, q, quote, with_inputs(derivatives, **changes)).confirmed


def test_no_lookahead_no_gap_no_chasing_or_invented_target():
    h, q, _, quote, derivatives = fixture()
    base = evaluate(h, q, quote, derivatives)
    future = replace(h[-1], opened_at=NOW, closed_at=NOW + timedelta(hours=1), low=D("1"))
    assert evaluate(h + [future], q, quote, derivatives) == base
    assert not evaluate(h[:80] + h[81:], q, quote, derivatives).structurally_ready
    assert not evaluate(h, q, replace(quote, last=D("90")), derivatives).structurally_ready
    no_floor = [replace(c, low=max(c.low, D("99"))) for c in h]
    no_floor_q = [replace(c, low=max(c.low, D("99"))) for c in q]
    assert not evaluate(no_floor, no_floor_q, quote, derivatives).structurally_ready


def test_confirmation_serialization_and_shared_publication_floor():
    h, q, inst, quote, derivatives = fixture()
    kwargs = dict(
        now=NOW,
        note=lambda *a, **kw: None,
        macro_narrative="test",
        beta=D("1"),
        residual=D("0"),
        is_discovery_tier=False,
    )
    prelim = score_continuation(inst, quote, h, q, derivatives=None, **kwargs)
    assert prelim is not None
    assert "continuation_requires_five_current_gates" in publication_rejection_reasons(prelim)
    confirmed = score_continuation(inst, quote, h, q, derivatives=derivatives, **kwargs)
    assert confirmed is not None
    assert not publication_rejection_reasons(confirmed)
    serialized = confirmed.as_dict()
    assert serialized["continuation_evidence"]["policy_version"] == POLICY_VERSION
    assert len(serialized["selection_gates"]) == 5
    assert serialized["order_execution_enabled"] is False


def test_gate_revalidation_cannot_trust_old_serialized_passes():
    candidate = {"setup_label": "BREAKDOWN_RETEST_SHORT", "checklist": [{"passed": True}] * 5}
    assert reverify_candidate_gates(candidate, current_price=D("99"))[1] == 0
    assert (
        reverify_candidate_gates(candidate, current_price=D("99"), continuation_gates=(True,) * 5)[
            1
        ]
        == 5
    )


def test_disabled_configuration_preserves_recorded_baseline_and_episode_identity():
    assert "bearish_continuation_enabled" not in screening_configuration(Settings(_env_file=None))
    assert screening_configuration(Settings(_env_file=None, bearish_continuation_enabled=True))[
        "bearish_continuation_enabled"
    ]
    old = {
        "symbol": "BEARUSDT",
        "direction": "short",
        "setup_label": "BREAKDOWN_RETEST_SHORT",
        "continuation_evidence": {"policy_version": POLICY_VERSION, "plan": {"support": "100"}},
    }
    assert same_radar_episode(old, dict(old))
    assert not same_radar_episode(
        old, {**old, "continuation_evidence": {"plan": {"support": "101"}}}
    )


@pytest.mark.parametrize(
    "mode,direction,expected",
    [
        ("all", "short", True),
        ("distribution", "short", True),
        ("all", "long", False),
        ("accumulation", "short", False),
    ],
)
async def test_real_two_stage_screening_route(mode, direction, expected):
    from unittest.mock import AsyncMock

    from algotrader.application.crypto_screener import CryptoOpportunityScreener
    from algotrader.domain.crypto_screener_models import ScreenerMode
    from tests.test_top_gainer_short_watch import _macro

    h, q, inst, quote, derivatives = fixture()
    market = AsyncMock()
    market.instruments.return_value = [inst]
    market.quotes.return_value = [quote]

    async def candles(instrument, interval, limit):
        return h if interval == "1h" else q if interval == "4h" else []

    market.candles.side_effect = candles
    market.derivatives_snapshot.return_value = derivatives
    macro = AsyncMock()
    macro.get_context.return_value = _macro(NOW)
    screener = CryptoOpportunityScreener(
        Settings(_env_file=None, bearish_continuation_enabled=True),
        macro_provider=macro,
        evaluated_at=NOW,
    )
    result = await screener.screen(
        market,
        derivatives_port=market,
        min_score=D("0"),
        max_symbols=1,
        mode=ScreenerMode(mode),
        target_direction=Direction(direction),
    )
    actual = [o for o in result if o.setup_label is SetupLabel.BREAKDOWN_RETEST_SHORT]
    assert bool(actual) == expected
    assert screener.scan_counts["stage2_shortlist"] <= 45
    if expected:
        market.derivatives_snapshot.assert_awaited_once()
        assert actual[0].continuation_evidence["derivatives_confirmed"] is True


async def test_flag_off_never_evaluates_continuation_and_missing_derivatives_cannot_publish(
    monkeypatch,
):
    from unittest.mock import AsyncMock

    from algotrader.application.crypto_screener import CryptoOpportunityScreener
    from algotrader.domain.crypto_screener_models import ScreenerMode
    from tests.test_top_gainer_short_watch import _macro

    h, q, inst, quote, _ = fixture()
    macro = AsyncMock()
    macro.get_context.return_value = _macro(NOW)
    market = AsyncMock()
    market.instruments.return_value = [inst]
    market.quotes.return_value = [quote]

    async def candles(instrument, interval, limit):
        return h if interval == "1h" else q if interval == "4h" else []

    market.candles.side_effect = candles
    for enabled in (False, True):
        screener = CryptoOpportunityScreener(
            Settings(_env_file=None, bearish_continuation_enabled=enabled),
            macro_provider=macro,
            evaluated_at=NOW,
        )
        if not enabled:
            monkeypatch.setattr(
                "algotrader.application.crypto_screener.score_continuation",
                lambda *a, **kw: pytest.fail("disabled path evaluated"),
            )
        else:
            monkeypatch.undo()
        result = await screener.screen(
            market,
            min_score=D("0"),
            max_symbols=1,
            mode=ScreenerMode.ALL,
            target_direction=Direction.SHORT,
        )
        assert not result


async def test_radar_rechecks_frozen_continuation_plan_and_withholds_unknown(monkeypatch):
    from unittest.mock import AsyncMock

    from algotrader.application.radar_service import reconcile_radar_cohort

    h, q, inst, quote, derivatives = fixture()
    opportunity = score_continuation(
        inst,
        quote,
        h,
        q,
        now=NOW,
        derivatives=derivatives,
        note=lambda *a, **kw: None,
        macro_narrative="test",
        beta=D("1"),
        residual=D("0"),
        is_discovery_tier=False,
    )
    assert opportunity is not None
    candidate = opportunity.as_dict()
    market = AsyncMock()
    market.instruments.return_value = [inst]
    market.quotes.return_value = [quote]

    async def candles(instrument, interval, limit):
        return h if interval == "1h" else q

    market.candles.side_effect = candles
    market.derivatives_snapshot.return_value = derivatives
    checked = await reconcile_radar_cohort(
        [candidate], set(), market, now=NOW, bearish_continuation_enabled=True
    )
    assert checked[0]["gates_passed"] == 5
    assert checked[0]["continuation_evidence"] == candidate["continuation_evidence"]
    checked = await reconcile_radar_cohort(
        [candidate], set(), market, now=NOW, bearish_continuation_enabled=False
    )
    assert checked[0]["gates_passed"] == 0
    assert checked[0]["radar_data_status"] == "unavailable"
    market.derivatives_snapshot.return_value = with_inputs(
        derivatives, open_interest_change_4h=None
    )
    checked = await reconcile_radar_cohort(
        [candidate], set(), market, now=NOW, bearish_continuation_enabled=True
    )
    assert checked[0]["gates_passed"] == 4


def test_reclaim_expiry_already_triggered_and_price_normalization():
    h, q, _, quote, derivatives = fixture()
    original = evaluate(h, q, quote, derivatives)
    assert original.confirmed
    assert not evaluate(
        h, q, replace(quote, last=original.plan.entry), derivatives
    ).structurally_ready
    assert not evaluate_continuation(
        h, q, now=NOW, price=quote.last, tick=D("10"), derivatives=derivatives
    ).structurally_ready
    reclaimed = list(h)
    reclaimed[-1] = replace(h[-1], close=D("100.5"), high=D("100.6"))
    assert not evaluate(
        reclaimed, q, replace(quote, last=D("100.5")), derivatives
    ).structurally_ready
    for i in range(1, 6):
        h.append(
            replace(
                h[-1], opened_at=NOW + timedelta(hours=i - 1), closed_at=NOW + timedelta(hours=i)
            )
        )
    q.append(replace(q[-1], opened_at=NOW, closed_at=NOW + timedelta(hours=4)))
    expired = evaluate_continuation(
        h,
        q,
        now=NOW + timedelta(hours=5),
        price=quote.last,
        tick=D("0.01"),
        existing_plan=original.plan,
    )
    assert expired.reason == "invalid_or_expired_plan"


def test_nonfinite_and_naive_inputs_do_not_produce_candidates():
    h, q, _, quote, _ = fixture()
    assert not evaluate(h, q, replace(quote, last=D("NaN"))).structurally_ready
    assert not evaluate(h, q, replace(quote, last=D("Infinity"))).structurally_ready
    bad = [replace(h[0], opened_at=h[0].opened_at.replace(tzinfo=None))] + h[1:]
    assert evaluate(bad, q, quote).reason == "naive_candle_time"


async def test_live_cache_rollback_withholds_experimental_cards(monkeypatch):
    from algotrader.api import portal
    from algotrader.domain.screener_policy import SCREENING_POLICY_VERSION

    monkeypatch.setattr(portal, "_shared_runtime", None)
    monkeypatch.setattr(portal, "_screener_task", None)
    monkeypatch.setattr(
        portal,
        "load_settings",
        lambda: Settings(_env_file=None, bearish_continuation_enabled=False),
    )
    monkeypatch.setattr(
        portal,
        "_screener_cache",
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "screening_policy_version": SCREENING_POLICY_VERSION,
            "radar_lifecycle_version": portal.RADAR_PUBLICATION_VERSION,
            "opportunities": [
                {"symbol": "BEARUSDT", "setup_label": "BREAKDOWN_RETEST_SHORT"},
                {"symbol": "BASEUSDT", "setup_label": "VCP_COMPRESSION_LONG"},
            ],
            "qualifying_count": 2,
        },
    )
    result = await portal.get_live_screener()
    assert [o["symbol"] for o in result["opportunities"]] == ["BASEUSDT"]
    assert result["qualifying_count"] == 1


async def test_continuation_is_not_blocked_by_reversal_history_requirement():
    from unittest.mock import AsyncMock

    from algotrader.application.crypto_screener import CryptoOpportunityScreener
    from algotrader.domain.crypto_screener_models import ScreenerMode
    from tests.test_top_gainer_short_watch import _macro

    h, q, inst, quote, derivatives = fixture()
    screener = CryptoOpportunityScreener(
        Settings(_env_file=None, bearish_continuation_enabled=True),
        macro_provider=AsyncMock(),
        evaluated_at=NOW,
    )
    result = await screener._evaluate_single(
        inst,
        quote,
        AsyncMock(),
        None,
        (),
        _macro(NOW),
        mode=ScreenerMode.DISTRIBUTION,
        target_direction=Direction.SHORT,
        preloaded_candles=(h[-160:], q, ()),
        explicit_derivatives=derivatives,
    )
    assert [o.setup_label for o in result] == [SetupLabel.BREAKDOWN_RETEST_SHORT]


@pytest.mark.parametrize("reverse", [False, True])
async def test_reserved_capacity_is_inside_existing_45_and_canonically_ordered(
    monkeypatch, reverse
):
    from unittest.mock import AsyncMock

    from algotrader.application.crypto_screener import CryptoOpportunityScreener
    from algotrader.domain.crypto_screener_models import ScreenerMode
    from tests.test_top_gainer_short_watch import _macro

    h, q, inst, quote, derivatives = fixture()
    prototype = score_continuation(
        inst,
        quote,
        h,
        q,
        now=NOW,
        derivatives=derivatives,
        note=lambda *a, **kw: None,
        macro_narrative="test",
        beta=D("1"),
        residual=D("0"),
        is_discovery_tier=False,
    )
    assert prototype is not None
    instruments = [
        make_instrument(
            venue=Venue.BINANCE_USDM,
            market_kind=MarketKind.FUTURES,
            symbol=f"{'NEW' if i >= 45 else 'OLD'}{i:02}USDT",
        )
        for i in range(50)
    ]
    if reverse:
        instruments.reverse()
    market, macro = AsyncMock(), AsyncMock()
    market.instruments.return_value = instruments
    market.quotes.return_value = [replace(quote, instrument_id=i.id) for i in instruments]
    macro.get_context.return_value = _macro(NOW)
    screener = CryptoOpportunityScreener(
        Settings(_env_file=None, bearish_continuation_enabled=True),
        macro_provider=macro,
        evaluated_at=NOW,
    )

    async def evaluate_inst(**kwargs):
        i = kwargs["inst"]
        return [
            replace(
                prototype,
                instrument=i,
                setup_label=(
                    SetupLabel.BREAKDOWN_RETEST_SHORT
                    if i.id.symbol.startswith("NEW")
                    else SetupLabel.DISTRIBUTION_SHORT
                ),
                score=D("70") if i.id.symbol.startswith("NEW") else D("90"),
                conviction=D("90"),
            )
        ]

    monkeypatch.setattr(screener, "_evaluate_single", evaluate_inst)
    result = await screener.screen(market, max_symbols=50, min_score=D("0"), mode=ScreenerMode.ALL)
    assert screener.scan_counts["stage2_shortlist"] == 45
    assert screener.scan_counts["continuation_stage2_reserved"] == 3
    assert sorted(
        o.instrument.id.symbol for o in result if o.setup_label is SetupLabel.BREAKDOWN_RETEST_SHORT
    ) == [
        "NEW45USDT",
        "NEW46USDT",
        "NEW47USDT",
    ]


async def test_confirmed_continuation_survives_actual_portal_retention(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from algotrader.api import portal
    from algotrader.domain.scan_observations import ScanReceipt
    from algotrader.domain.screener_policy import SCREENING_POLICY_VERSION
    from algotrader.infrastructure.runtime import ScreeningRun

    h, q, inst, quote, derivatives = fixture()
    clock = datetime.now(UTC)
    delta = clock - NOW
    h = [replace(c, opened_at=c.opened_at + delta, closed_at=c.closed_at + delta) for c in h]
    q = [replace(c, opened_at=c.opened_at + delta, closed_at=c.closed_at + delta) for c in q]
    quote, derivatives = (
        replace(quote, observed_at=clock),
        with_inputs(derivatives, observed_at=clock),
    )
    opportunity = score_continuation(
        inst,
        quote,
        h,
        q,
        now=clock,
        derivatives=derivatives,
        note=lambda *a, **kw: None,
        macro_narrative="test",
        beta=D("1"),
        residual=D("0"),
        is_discovery_tier=False,
    )
    assert opportunity is not None
    market = AsyncMock()
    market.instruments.return_value = [inst]
    market.quotes.return_value = [quote]

    async def candles(instrument, interval, limit):
        return h if interval == "1h" else q

    market.candles.side_effect = candles
    market.derivatives_snapshot.return_value = derivatives
    runtime = SimpleNamespace(
        settings=Settings(
            _env_file=None,
            bearish_continuation_enabled=True,
            research_archive_dir=tmp_path / "research",
        ),
        futures_market=market,
        run_crypto_scan=AsyncMock(return_value=ScreeningRun([], {}, ScanReceipt("test", "saved"))),
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(portal, "get_portal_runtime", AsyncMock(return_value=runtime))
    monkeypatch.setattr(
        portal,
        "_screener_cache",
        {
            "screening_policy_version": SCREENING_POLICY_VERSION,
            "opportunities": [opportunity.as_dict()],
        },
    )
    monkeypatch.setattr(portal, "_cache_timestamp", None)
    result = await portal._do_crypto_screen()
    assert len(result["opportunities"]) == 1
    assert result["opportunities"][0]["gates_passed"] == 5
    assert all(g["status"] == "passed" for g in result["opportunities"][0]["checklist"])


def test_search_skips_bad_rr_shelf_without_skipping_nearest_support(monkeypatch):
    from algotrader.domain import bearish_continuation as domain

    h, q, _, quote, derivatives = fixture()
    h[-1] = replace(h[-1], high=D("100.6"))
    original_pivots = domain._pivots

    def pivots(bars, attr, width):
        if len(bars) == 120 and attr == "low":
            return [
                (v, h[i].closed_at)
                for v, i in [
                    (D("101"), 200),
                    (D("101"), 210),
                    (D("100"), 220),
                    (D("100"), 230),
                    (D("95"), 180),
                ]
            ]
        return original_pivots(bars, attr, width)

    monkeypatch.setattr(domain, "_pivots", pivots)
    monkeypatch.setattr(domain, "atr", lambda bars, length: [D("2")] * len(bars))
    result = evaluate(h, q, quote, derivatives)
    assert result.confirmed, result
    assert result.plan.support == D("100")
    assert result.plan.target == D("95.2")

    # A too-close intervening floor must block this entry, not be skipped for 95.
    def nearer_floor(bars, attr, width):
        values = pivots(bars, attr, width)
        if len(bars) == 120 and attr == "low":
            values.append((D("98.9"), h[180].closed_at))
        return values

    monkeypatch.setattr(domain, "_pivots", nearer_floor)
    assert not evaluate(h, q, quote, derivatives).structurally_ready


def test_search_does_not_stop_on_an_expired_rejection(monkeypatch):
    from algotrader.domain import bearish_continuation as domain

    h, q, _, quote, derivatives = fixture()
    h[-8] = replace(h[-8], open=D("101"), high=D("101.2"), low=D("99.1"), close=D("99.5"))
    h[-7] = replace(h[-7], open=D("99.8"), high=D("100.1"), low=D("99.2"), close=D("99.4"))
    h[-6] = replace(
        h[-6], open=D("100"), high=D("104.8"), low=D("99.2"), close=D("99.4"), volume=D("150")
    )
    for i in range(-5, -2):
        h[i] = replace(h[i], open=D("99.8"), high=D("100.1"), low=D("99.2"), close=D("99.4"))
    h[-2] = replace(h[-2], open=D("99.8"), high=D("100.1"), low=D("99.2"), close=D("99.4"))
    h[-1] = replace(h[-1], high=D("100.6"))
    original_pivots = domain._pivots

    def pivots(bars, attr, width):
        if len(bars) == 120 and attr == "low":
            if bars[-1].closed_at == h[-8].closed_at:
                return [
                    (D("105"), h[180].closed_at),
                    (D("105"), h[190].closed_at),
                    (D("95"), h[160].closed_at),
                ]
            if bars[-1].closed_at == h[-9].closed_at:
                return [
                    (D("100"), h[180].closed_at),
                    (D("100"), h[190].closed_at),
                    (D("95"), h[160].closed_at),
                ]
            return []
        return original_pivots(bars, attr, width)

    monkeypatch.setattr(domain, "_pivots", pivots)
    monkeypatch.setattr(domain, "atr", lambda bars, length: [D("2")] * len(bars))
    result = evaluate(h, q, quote, derivatives)
    assert result.confirmed, result
    assert result.plan.support == D("100")
    assert result.plan.rejection_at == NOW


def active_fixture(hours=5):
    h, q, inst, quote, derivatives = fixture()
    original = evaluate(h, q, quote, derivatives)
    assert original.confirmed
    plan = original.plan
    price = plan.entry - plan.atr * D("0.75")
    assert plan.target < price < plan.entry
    for i in range(1, hours + 1):
        h.append(
            replace(
                h[-1],
                opened_at=NOW + timedelta(hours=i - 1),
                closed_at=NOW + timedelta(hours=i),
                open=price + D("0.1"),
                high=price + D("0.2"),
                low=price - D("0.2"),
                close=price,
                volume=D("2000"),
            )
        )
    for i in range(1, hours // 4 + 1):
        q.append(
            replace(
                q[-1],
                opened_at=NOW + timedelta(hours=4 * (i - 1)),
                closed_at=NOW + timedelta(hours=4 * i),
                low=price - D("0.2"),
                close=price,
            )
        )
    clock = NOW + timedelta(hours=hours)
    quote = replace(
        quote, last=price, bid=price - D("0.01"), ask=price + D("0.01"), observed_at=clock
    )
    derivatives = with_inputs(derivatives, observed_at=clock)
    return h, q, inst, quote, derivatives, plan, clock


def test_triggered_retention_has_no_pending_entry_extension_or_wait_timeout():
    h, q, _, quote, derivatives, plan, clock = active_fixture()
    kwargs = dict(
        now=clock, price=quote.last, tick=D("0.01"), derivatives=derivatives, existing_plan=plan
    )
    assert not evaluate_continuation(h, q, **kwargs).confirmed
    active = evaluate_continuation(h, q, is_triggered=True, **kwargs)
    assert active.confirmed, active
    assert active.plan == plan
    stopped_h = h[:-1] + [replace(h[-1], high=plan.stop)]
    assert not evaluate_continuation(stopped_h, q, is_triggered=True, **kwargs).confirmed
    assert not evaluate_continuation(
        h, q, **{**kwargs, "price": plan.target}, is_triggered=True
    ).confirmed
    assert not evaluate_continuation(
        h, q, **{**kwargs, "derivatives": None}, is_triggered=True
    ).confirmed


@pytest.mark.parametrize("previously_observed", [False, True])
async def test_radar_retains_triggered_short_after_pending_window_and_extension(
    previously_observed,
):
    from unittest.mock import AsyncMock

    from algotrader.application.radar_service import reconcile_radar_cohort

    original_h, original_q, inst, original_quote, original_derivatives = fixture()
    opportunity = score_continuation(
        inst,
        original_quote,
        original_h,
        original_q,
        now=NOW,
        derivatives=original_derivatives,
        note=lambda *a, **kw: None,
        macro_narrative="test",
        beta=D("1"),
        residual=D("0"),
        is_discovery_tier=False,
    )
    candidate = opportunity.as_dict()
    h, q, _, quote, derivatives, plan, clock = active_fixture()
    if previously_observed:
        candidate.update(
            radar_triggered_at=(NOW + timedelta(hours=1)).isoformat(),
            radar_trigger_basis="breakout",
            radar_trigger_entry=str(plan.entry),
            radar_trigger_stop=str(plan.stop),
        )
    market = AsyncMock()
    market.instruments.return_value = [inst]
    market.quotes.return_value = [quote]

    async def candles(instrument, interval, limit):
        return h if interval == "1h" else q

    market.candles.side_effect = candles
    market.derivatives_snapshot.return_value = derivatives
    result = await reconcile_radar_cohort(
        [candidate], set(), market, now=clock, bearish_continuation_enabled=True
    )
    assert len(result) == 1
    assert result[0]["radar_status"] == "IN_ACTIVE_TRADE"
    assert result[0]["gates_passed"] == 5
    assert result[0]["failed_gates"] == []
    assert all(g["status"] == "passed" for g in result[0]["checklist"])
    assert result[0]["suggested_stop"] == candidate["suggested_stop"]
    # Hard stops and targets remain terminal even when the episode was profitable.
    market.quotes.return_value = [replace(quote, last=plan.stop)]
    assert not await reconcile_radar_cohort(
        [candidate], set(), market, now=clock, bearish_continuation_enabled=True
    )
    market.quotes.return_value = [replace(quote, last=plan.target)]
    assert not await reconcile_radar_cohort(
        [candidate], set(), market, now=clock, bearish_continuation_enabled=True
    )


async def test_untriggered_setup_cannot_be_revived_by_an_expired_window():
    from unittest.mock import AsyncMock

    from algotrader.application.radar_service import reconcile_radar_cohort

    old_h, old_q, inst, old_quote, old_derivatives = fixture()
    opportunity = score_continuation(
        inst,
        old_quote,
        old_h,
        old_q,
        now=NOW,
        derivatives=old_derivatives,
        note=lambda *a, **kw: None,
        macro_narrative="test",
        beta=D("1"),
        residual=D("0"),
        is_discovery_tier=False,
    )
    h, q, _, quote, derivatives, plan, clock = active_fixture()
    h = [
        replace(c, open=D("99.5"), high=D("99.6"), low=D("99.3"), close=D("99.4"))
        if c.opened_at >= NOW
        else c
        for c in h
    ]
    quote = replace(quote, last=D("99.4"), bid=D("99.39"), ask=D("99.41"))
    market = AsyncMock()
    market.instruments.return_value = [inst]
    market.quotes.return_value = [quote]

    async def candles(instrument, interval, limit):
        return h if interval == "1h" else q

    market.candles.side_effect = candles
    market.derivatives_snapshot.return_value = derivatives
    events = []
    assert not await reconcile_radar_cohort(
        [opportunity.as_dict()],
        set(),
        market,
        now=clock,
        events=events,
        bearish_continuation_enabled=True,
    )
    assert events[-1]["reason"] == "max_retention_age_exceeded"
