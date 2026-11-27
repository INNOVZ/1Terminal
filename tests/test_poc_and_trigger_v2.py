"""Tests for ALFA v3.5 Hybrid Log-Price Recency-Weighted POC/VA and Confluence-Bounded Trigger v2."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from algotrader.domain.execution_brackets import (
    build_long_bracket,
    compute_structural_shelf_high_72h,
)
from algotrader.domain.indicators import hybrid_volume_profile, volume_profile
from algotrader.domain.models import Candle


def _bar(ts: datetime, o: str, h: str, lo: str, c: str, vol: str, hours: int = 1) -> Candle:
    close_d = Decimal(c)
    vol_d = Decimal(vol)
    return Candle(
        opened_at=ts - timedelta(hours=hours),
        closed_at=ts,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(lo),
        close=close_d,
        volume=vol_d,
        quote_volume=vol_d * close_d,
        closed=True,
    )


def test_hybrid_volume_profile_eliminates_stale_high_price_ghost_poc_and_wick_distortion() -> None:
    now = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    # Days 16..30 (4H bars): old distribution cluster at $2.00 + 1 outlier flash wick to $4.00
    c4h: list[Candle] = []
    for i in range(90):
        ts = now - timedelta(days=30) + timedelta(hours=4 * (i + 1))
        hi = "4.00" if i == 10 else "2.05"
        c4h.append(_bar(ts, "1.98", hi, "1.95", "2.00", "10000", hours=4))
    # Days 0..15 (1H bars): massive fresh base accumulation around $1.00 (same base volume per hour!)
    c1h: list[Candle] = []
    for i in range(360):
        ts = now - timedelta(days=15) + timedelta(hours=i + 1)
        c1h.append(_bar(ts, "0.99", "1.02", "0.98", "1.00", "2500", hours=1))
        if (i + 1) % 4 == 0:
            c4h.append(_bar(ts, "0.99", "1.02", "0.98", "1.00", "10000", hours=4))

    legacy_vp = volume_profile(c4h[-180:], bins=50, tick_size=Decimal("0.001"))
    hyb_vp = hybrid_volume_profile(c1h, c4h, bins=80, half_life_days=10.0, tick_size=Decimal("0.001"))

    # Legacy quote-weighted unweighted 4H profile is pulled up toward $2.00 because 2.00 * 10000 = 2x quote volume
    assert legacy_vp.poc_price > Decimal("1.50")
    # Hybrid recency-weighted log-price base-volume profile locks directly onto the active $1.00 base
    assert Decimal("0.98") <= hyb_vp.poc_price <= Decimal("1.03")
    assert hyb_vp.val_price <= hyb_vp.poc_price <= hyb_vp.vah_price


def test_structural_shelf_high_strips_single_bar_stop_hunt_wick() -> None:
    now = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    bars: list[Candle] = []
    for i in range(72):
        ts = now - timedelta(hours=72 - i)
        # Flat $1.00-$1.02 box with one single-bar stop-hunt spike to $1.12
        hi = "1.12" if i == 20 else "1.02"
        bars.append(_bar(ts, "1.00", hi, "0.99", "1.01", "5000", hours=1))

    shelf = compute_structural_shelf_high_72h(bars, atr_1h=Decimal("0.015"))
    # Must NOT be hijacked to $1.12 by the single stop-hunt wick
    assert shelf <= Decimal("1.03")
    assert shelf >= Decimal("1.01")


def test_confluence_trigger_v2_does_not_hijack_sub_poc_trigger_to_distant_ghost_poc() -> None:
    # Sub-POC base at $1.00 with tight local shelf at $1.02, but distant 30d POC at $1.40 (+40%)
    brk_legacy = build_long_bracket(
        close=Decimal("1.00"),
        ema50_4h=Decimal("1.08"),
        poc_30d=Decimal("1.40"),
        atr14_4h=Decimal("0.03"),
        local_compression_high_4h=Decimal("1.02"),
        swing_low_4h=Decimal("0.95"),
        high_30d=Decimal("1.60"),
        vah_30d=Decimal("1.50"),
        val_30d=Decimal("0.94"),
        tick_size=Decimal("0.001"),
        use_confluence_trigger_v2=False,
    )
    brk_v2 = build_long_bracket(
        close=Decimal("1.00"),
        ema50_4h=Decimal("1.08"),
        poc_30d=Decimal("1.40"),
        atr14_4h=Decimal("0.03"),
        local_compression_high_4h=Decimal("1.02"),
        swing_low_4h=Decimal("0.95"),
        high_30d=Decimal("1.60"),
        vah_30d=Decimal("1.50"),
        val_30d=Decimal("0.94"),
        tick_size=Decimal("0.001"),
        use_confluence_trigger_v2=True,
    )
    # Legacy hijacks trigger to > $1.40 (+40%); v2 keeps trigger tight at ~$1.022 and spaces T1/T2/T3 cleanly!
    assert brk_legacy.breakout_trigger > Decimal("1.40")
    assert Decimal("1.020") <= brk_v2.breakout_trigger <= Decimal("1.025")
    assert brk_v2.t1 < Decimal("1.40")
    assert brk_v2.t2 >= brk_v2.t1 + Decimal("0.70") * brk_v2.risk_unit
    assert brk_v2.t3 >= brk_v2.t2 + Decimal("0.70") * brk_v2.risk_unit
    assert brk_v2.asymmetric_rr >= Decimal("2.00")

    # When POC IS in tight confluence ($1.04, within 1.5*ATR and <= 5%), v2 cleanly snaps trigger to POC!
    brk_v2_conf = build_long_bracket(
        close=Decimal("1.00"),
        ema50_4h=Decimal("1.03"),
        poc_30d=Decimal("1.04"),
        atr14_4h=Decimal("0.03"),
        local_compression_high_4h=Decimal("1.02"),
        swing_low_4h=Decimal("0.95"),
        high_30d=Decimal("1.20"),
        vah_30d=Decimal("1.12"),
        val_30d=Decimal("0.94"),
        tick_size=Decimal("0.001"),
        use_confluence_trigger_v2=True,
    )
    assert brk_v2_conf.breakout_trigger >= Decimal("1.040")
    assert brk_v2_conf.breakout_trigger <= Decimal("1.045")


def test_solvusdt_style_spaced_targets_anchor_intermediate_shelf_and_never_stack_t1_t2() -> None:
    """Regression (SOLVUSDT screenshot): T1 must anchor to the $0.00468 intermediate shelf and never stack 0.2% from T2."""
    brk = build_long_bracket(
        close=Decimal("0.004183"),
        ema50_4h=Decimal("0.004000"),
        poc_30d=Decimal("0.005331"),
        atr14_4h=Decimal("0.000120"),
        local_compression_high_4h=Decimal("0.004235"),
        swing_low_4h=Decimal("0.003680"),
        high_30d=Decimal("0.006141"),
        vah_30d=Decimal("0.005343"),  # Only 0.2% above POC — previously caused T1/T2 stacking!
        val_30d=Decimal("0.004100"),
        tick_size=Decimal("0.000001"),
        use_confluence_trigger_v2=True,
        overhead_shelves=(Decimal("0.004686"), Decimal("0.005331"), Decimal("0.006141")),
    )
    # T1 locks directly onto the $0.004686 intermediate shelf, T2 onto POC $0.005331, T3 onto $0.006141!
    assert brk.t1 == Decimal("0.004686")
    assert brk.t2 == Decimal("0.005331")
    assert brk.t3 == Decimal("0.006141")
    assert brk.t2 - brk.t1 >= Decimal("0.70") * brk.risk_unit
    assert brk.t3 - brk.t2 >= Decimal("0.70") * brk.risk_unit
    assert brk.asymmetric_rr >= Decimal("2.00")


