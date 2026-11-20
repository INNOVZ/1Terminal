"""Historical Top-Gainer -> Short Opportunity study on Nov 8-10 and Nov 12-14 cohorts.

Evaluates the exact current short screening pipeline (classify_short_archetype,
evaluate_short_gates S1-S5, build_short_bracket, and _score_distribution_setup 5/5
checklist) hourly across the 48h window for every observed Top-10 gainer, momentum
reference, and top 24h/48h loser in both cohorts.

Reads existing cohort summaries/reports, caches public Binance USD-M historical
candles/OI/taker/funding into tmp/top_gainer_short_raw/, and runs completely
offline once cached (or with --offline).
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from algotrader.adapters.external.models import (
    FearGreedReading,
    MacroSentimentContext,
    StablecoinSupplyMetric,
)
from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.config import load_settings
from algotrader.domain.archetypes import ShortArchetypeFeatures, classify_short_archetype
from algotrader.domain.crypto_screener_models import ScreenerMode, SetupLabel
from algotrader.domain.execution_brackets import InvalidExecutionBracket, build_short_bracket
from algotrader.domain.indicators import (
    atr,
    confirmed_macd_divergences,
    confirmed_rsi_divergences,
    ema,
    macd,
    resample_candles,
    rsi,
    snapshot as indicator_snapshot,
    taker_buy_ratio_from_candles,
    volume_profile,
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
from algotrader.domain.screening_gates import EPSILON, ShortGateInputs, evaluate_short_gates

RAW_CACHE_DIR = ROOT / "tmp" / "top_gainer_short_raw"
EVIDENCE_OUT = ROOT / "docs" / "security" / "evidence" / "2026-11-16-top-gainer-short-study.json"
ZERO = Decimal("0")


def unwrap(obj: Any) -> Any:
    if isinstance(obj, dict):
        if "@" in obj:
            tag = obj["@"]
            if tag in ("Decimal", "datetime"):
                return obj["value"]
            if tag == "mapping":
                return {k: unwrap(v) for k, v in obj["items"].items()}
            if tag in ("tuple", "list", "set"):
                return [unwrap(x) for x in obj["items"]]
            return obj.get("items", obj.get("value", obj))
        return {k: unwrap(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [unwrap(x) for x in obj]
    return obj


def load_cohorts() -> dict[str, dict[str, Any]]:
    # Nov 8-10 cohort
    cs8 = json.loads((ROOT / "docs/security/evidence/2026-11-11-pre-gainer-cohort-summary.json").read_text())
    nov8_top10 = {
        s["symbol"]: s
        for s in cs8["symbols"]
        if s.get("initial_inside") is True or s.get("first_observed_top10_at") is not None
    }
    nov8_known = [s for s in cs8["symbols"] if s.get("endpoint_return_pct") is not None]
    nov8_worst10 = sorted(nov8_known, key=lambda x: float(x["endpoint_return_pct"]))[:10]
    for s in nov8_worst10:
        nov8_top10.setdefault(s["symbol"], s)
    # Include Nov 8-10 Momentum Reference traps explicitly mentioned in blueprint
    for sym in ("JCTUSDT", "QUSDT", "TAUSDT", "BMTUSDT"):
        match = next((s for s in cs8["symbols"] if s["symbol"] == sym), None)
        if match:
            nov8_top10.setdefault(sym, match)

    # Nov 12-14 cohort
    rep12_24 = unwrap(json.loads((ROOT / "tmp/research-vm-review-20261114/report-24h.json").read_text()))["payload"]
    rep12_48 = unwrap(json.loads((ROOT / "tmp/research-vm-review-20261114/report-48h.json").read_text()))["payload"]
    defn12 = unwrap(json.loads((ROOT / "tmp/research-vm-review-20261114/definition.json").read_text()))["payload"]

    nov12_map: dict[str, dict[str, Any]] = {}
    rep24_by_sym = {s["symbol"]: s for s in rep12_24["symbols"]}
    for s in rep12_48["symbols"]:
        sym = s["symbol"]
        if s.get("initial_inside") is True or s.get("first_observed_top10_at") is not None:
            merged = dict(s)
            if sym in rep24_by_sym:
                merged["return_24h_endpoint_pct"] = rep24_by_sym[sym].get("endpoint_return_pct")
            nov12_map[sym] = merged
    for sym in defn12.get("momentum_reference", ()):
        s = next((x for x in rep12_48["symbols"] if x["symbol"] == sym), None)
        if s and sym not in nov12_map:
            merged = dict(s)
            if sym in rep24_by_sym:
                merged["return_24h_endpoint_pct"] = rep24_by_sym[sym].get("endpoint_return_pct")
            nov12_map[sym] = merged
    nov12_24_known = [x for x in rep12_24["symbols"] if x.get("endpoint_return_pct") is not None]
    for s24 in sorted(nov12_24_known, key=lambda x: float(x["endpoint_return_pct"]))[:10]:
        sym = s24["symbol"]
        if sym not in nov12_map:
            s48 = next((x for x in rep12_48["symbols"] if x["symbol"] == sym), dict(s24))
            merged = dict(s48)
            merged["return_24h_endpoint_pct"] = s24.get("endpoint_return_pct")
            nov12_map[sym] = merged

    return {
        "nov8": {
            "name": "Nov 8-10 (71346822)",
            "anchor": datetime(2026, 11, 8, 13, 50, tzinfo=UTC),
            "endpoint_48h": datetime(2026, 11, 10, 13, 50, tzinfo=UTC),
            "forward_limit": datetime(2026, 11, 12, 14, 0, tzinfo=UTC),
            "symbols": nov8_top10,
            "worst_10_48h": [s["symbol"] for s in nov8_worst10],
        },
        "nov12": {
            "name": "Nov 12-14 (8dd933f0)",
            "anchor": datetime(2026, 11, 12, 6, 20, tzinfo=UTC),
            "endpoint_48h": datetime(2026, 11, 14, 6, 20, tzinfo=UTC),
            "forward_limit": datetime(2026, 11, 16, 6, 0, tzinfo=UTC),
            "symbols": nov12_map,
            "worst_10_24h": [
                s["symbol"] for s in sorted(nov12_24_known, key=lambda x: float(x["endpoint_return_pct"]))[:10]
            ],
        },
    }


async def fetch_symbol_history(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, symbol: str
) -> dict[str, Any]:
    cache_path = RAW_CACHE_DIR / f"{symbol}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())

    # Full window: Sep 1 2026 -> Nov 16 2026 06:00 UTC
    end_ms = int(datetime(2026, 11, 16, 6, 0, tzinfo=UTC).timestamp() * 1000)
    start_1h_ms = int(datetime(2026, 10, 15, 0, 0, tzinfo=UTC).timestamp() * 1000)
    start_4h_ms = int(datetime(2026, 8, 15, 0, 0, tzinfo=UTC).timestamp() * 1000)
    start_oi_ms = int(datetime(2026, 10, 28, 0, 0, tzinfo=UTC).timestamp() * 1000)

    async def get_json(path: str, params: dict[str, Any]) -> Any:
        async with sem:
            for attempt in range(4):
                resp = await client.get(f"https://fapi.binance.com{path}", params=params)
                if resp.status_code == 429 or resp.status_code >= 500:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                if resp.status_code == 400:
                    return []
                resp.raise_for_status()
                return resp.json()
            return []

    k1h_p1, k1h_p2, k4h, k1d, k1w, oi_1h, taker_1h, funding = await asyncio.gather(
        get_json("/fapi/v1/klines", {"symbol": symbol, "interval": "1h", "startTime": start_1h_ms, "endTime": end_ms, "limit": 500}),
        get_json("/fapi/v1/klines", {"symbol": symbol, "interval": "1h", "startTime": start_1h_ms + 500 * 3600 * 1000, "endTime": end_ms, "limit": 500}),
        get_json("/fapi/v1/klines", {"symbol": symbol, "interval": "4h", "startTime": start_4h_ms, "endTime": end_ms, "limit": 600}),
        get_json("/fapi/v1/klines", {"symbol": symbol, "interval": "1d", "startTime": start_4h_ms, "endTime": end_ms, "limit": 120}),
        get_json("/fapi/v1/klines", {"symbol": symbol, "interval": "1w", "endTime": end_ms, "limit": 60}),
        get_json("/futures/data/openInterestHist", {"symbol": symbol, "period": "1h", "startTime": start_oi_ms, "endTime": end_ms, "limit": 500}),
        get_json("/futures/data/takerlongshortRatio", {"symbol": symbol, "period": "1h", "startTime": start_oi_ms, "endTime": end_ms, "limit": 500}),
        get_json("/fapi/v1/fundingRate", {"symbol": symbol, "startTime": start_oi_ms, "endTime": end_ms, "limit": 250}),
    )
    # Deduplicate 1h klines
    k1h_by_open = {int(row[0]): row for row in (k1h_p1 or []) + (k1h_p2 or [])}
    k1h = [k1h_by_open[k] for k in sorted(k1h_by_open)]

    # Fetch OI/taker tail if truncated at 500 rows (Nov 1 -> Nov 16 is ~450h, should fit, but paginate if needed)
    if oi_1h and int(oi_1h[-1]["timestamp"]) < end_ms - 3600 * 1000:
        oi_tail = await get_json("/futures/data/openInterestHist", {"symbol": symbol, "period": "1h", "startTime": int(oi_1h[-1]["timestamp"]) + 1, "endTime": end_ms, "limit": 500})
        oi_by_ts = {int(r["timestamp"]): r for r in oi_1h + (oi_tail or [])}
        oi_1h = [oi_by_ts[k] for k in sorted(oi_by_ts)]
    if taker_1h and int(taker_1h[-1]["timestamp"]) < end_ms - 3600 * 1000:
        tk_tail = await get_json("/futures/data/takerlongshortRatio", {"symbol": symbol, "period": "1h", "startTime": int(taker_1h[-1]["timestamp"]) + 1, "endTime": end_ms, "limit": 500})
        tk_by_ts = {int(r["timestamp"]): r for r in taker_1h + (tk_tail or [])}
        taker_1h = [tk_by_ts[k] for k in sorted(tk_by_ts)]

    payload = {
        "symbol": symbol,
        "klines_1h": k1h,
        "klines_4h": k4h or [],
        "klines_1d": k1d or [],
        "klines_1w": k1w or [],
        "oi_1h": oi_1h or [],
        "taker_1h": taker_1h or [],
        "funding": funding or [],
    }
    RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload))
    return payload


def parse_candles(rows: list[list[Any]]) -> list[Candle]:
    out: list[Candle] = []
    for r in rows:
        opened = datetime.fromtimestamp(int(r[0]) / 1000, UTC)
        closed = datetime.fromtimestamp(int(r[6]) / 1000, UTC)
        o, h, l, c = Decimal(str(r[1])), Decimal(str(r[2])), Decimal(str(r[3])), Decimal(str(r[4]))
        v, qv = Decimal(str(r[5])), Decimal(str(r[7]))
        if o <= 0 or h <= 0 or l <= 0 or c <= 0 or h < l:
            continue
        out.append(Candle(
            opened_at=opened,
            closed_at=closed,
            open=o,
            high=h,
            low=l,
            close=c,
            volume=v,
            quote_volume=qv,
            closed=True,
            taker_buy_volume=Decimal(str(r[9])) if len(r) > 9 else Decimal("0"),
            taker_buy_quote_volume=Decimal(str(r[10])) if len(r) > 10 else Decimal("0"),
        ))
    return out


def make_instrument(symbol: str, tick: Decimal) -> Instrument:
    base = symbol.removesuffix("USDT").removesuffix("USDC")
    return Instrument(
        id=InstrumentId(venue=Venue.BINANCE_USDM, symbol=symbol),
        base_asset=base,
        quote_asset="USDT",
        asset_class=AssetClass.CRYPTO,
        market_kind=MarketKind.FUTURES,
        trading=True,
        price_tick=tick,
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal("5"),
    )


def infer_tick(candles: list[Candle]) -> Decimal:
    for c in candles[-20:]:
        s = format(c.close.normalize(), "f")
        if "." in s:
            dec = len(s.split(".")[1])
            return Decimal(f"1e-{min(8, max(2, dec))}")
    return Decimal("0.0001")


def build_derivatives_at(
    oi_map: dict[int, Decimal],
    taker_map: dict[int, tuple[Decimal, Decimal]],
    funding_list: list[tuple[int, Decimal]],
    cutoff_ms: int,
) -> tuple[DerivativesSnapshot | None, Decimal | None]:
    # Funding: most recent settlement <= cutoff_ms
    funding_rate: Decimal | None = None
    for fts, fr in funding_list:
        if fts <= cutoff_ms:
            funding_rate = fr
        else:
            break

    # OI: use exact closed hour <= cutoff_ms (lagged 1h conservatively to match closed bar)
    hour_ms = (cutoff_ms // 3_600_000) * 3_600_000
    oi_now_ts = hour_ms if hour_ms in oi_map else (hour_ms - 3_600_000 if (hour_ms - 3_600_000) in oi_map else None)
    if oi_now_ts is None:
        return None, funding_rate
    oi_now = oi_map[oi_now_ts]

    def oi_chg(hours: int) -> Decimal | None:
        prev = oi_map.get(oi_now_ts - hours * 3_600_000)
        if prev is None or prev <= 0:
            return None
        return (oi_now - prev) / prev

    def taker_ratio(hours: int) -> Decimal | None:
        buys = ZERO
        sells = ZERO
        count = 0
        for h in range(hours):
            pair = taker_map.get(oi_now_ts - h * 3_600_000)
            if pair is not None:
                buys += pair[0]
                sells += pair[1]
                count += 1
        if count == 0 or sells <= 0:
            return None
        return buys / sells

    snap = DerivativesSnapshot(
        funding_rate=funding_rate,
        basis_rate=Decimal("0"),
        open_interest=oi_now,
        open_interest_change_1h=oi_chg(1),
        open_interest_change_4h=oi_chg(4),
        open_interest_change_24h=oi_chg(24),
        open_interest_change_7d=oi_chg(168),
        taker_buy_sell_ratio=taker_ratio(1),
        taker_buy_sell_ratio_4h=taker_ratio(4),
        taker_buy_sell_ratio_24h=taker_ratio(24),
        bid_depth_notional=Decimal("100000"),
        ask_depth_notional=Decimal("100000"),
    )
    return snap, funding_rate


def diagnose_short_step(
    screener: CryptoOpportunityScreener,
    inst: Instrument,
    closed_1h: list[Candle],
    closed_4h: list[Candle],
    closed_1d: list[Candle],
    closed_1w: list[Candle],
    btc_1h: list[Candle],
    deriv: DerivativesSnapshot | None,
    funding_rate: Decimal | None,
    cutoff_dt: datetime,
    macro: MacroSentimentContext,
) -> dict[str, Any]:
    snap_1h = indicator_snapshot(closed_1h)
    close = snap_1h.close
    last_24 = closed_1h[-24:]
    quote_vol_24h = sum(c.quote_volume for c in last_24)
    high_24h = max(c.high for c in last_24)
    low_24h = min(c.low for c in last_24)
    ret_24h = (close - closed_1h[-25].close) / closed_1h[-25].close if len(closed_1h) >= 25 else ZERO
    quote = Quote(
        instrument_id=inst.id,
        bid=close * Decimal("0.9995"),
        ask=close * Decimal("1.0005"),
        last=close,
        quote_volume_24h=quote_vol_24h,
        observed_at=cutoff_dt,
        price_change_pct_24h=ret_24h * Decimal("100"),
        high_24h=high_24h,
        low_24h=low_24h,
        funding_rate=funding_rate,
    )

    # First run the EXACT production _score_distribution_setup
    screener._evaluated_at = cutoff_dt
    opp = screener._score_distribution_setup(
        inst=inst,
        quote=quote,
        snap_1h=snap_1h,
        closed_1h=closed_1h,
        closed_4h=closed_4h,
        derivatives=deriv,
        macro_context=macro,
        candles_1d=closed_1d,
        candles_1w=closed_1w,
    )

    if opp is not None:
        failed_items = [title for title, passed, _ in opp.checklist if not passed]
        passed_count = sum(1 for _, passed, _ in opp.checklist if passed)
        return {
            "status": "confirmed_5_of_5" if (passed_count == 5 and opp.score >= Decimal("50")) else "partial_candidate",
            "archetype": opp.setup_label.value,
            "score": float(opp.score),
            "conviction": float(opp.conviction or opp.score),
            "checklist_passed": passed_count,
            "failed_checklist_items": failed_items,
            "blocker": None if (passed_count == 5 and opp.score >= Decimal("50")) else (
                "score_below_50" if opp.score < Decimal("50") else f"checklist_{passed_count}_of_5:{','.join(failed_items)}"
            ),
            "close": float(close),
            "entry": float(opp.suggested_entry),
            "stop": float(opp.suggested_stop),
            "trigger": float(opp.breakout_trigger),
            "t1": float(opp.suggested_targets[0]),
            "rr": float(opp.risk_reward_ratio),
            "funding_8h": float(funding_rate) if funding_rate is not None else None,
            "oi_7d": float(deriv.open_interest_change_7d) if deriv and deriv.open_interest_change_7d is not None else None,
        }

    # Diagnostic trace of WHY _score_distribution_setup returned None
    if deriv is not None and deriv.open_interest_change_7d is None:
        return {"status": "rejected", "blocker": "missing_7d_oi", "close": float(close)}

    closes_4h = [c.close for c in closed_4h]
    if len(closes_4h) < 20:
        return {"status": "rejected", "blocker": "insufficient_4h_bars", "close": float(close)}
    ema20_4h = ema(closes_4h, 20)[-1]
    ema50_4h = ema(closes_4h, 50)[-1] if len(closes_4h) >= 50 else ema20_4h
    atr14_4h = atr(closed_4h, 14)[-1]
    vpvr_window = closed_4h[-180:] if len(closed_4h) >= 180 else closed_4h
    vp = volume_profile(vpvr_window, bins=50, tick_size=inst.price_tick)
    high_30d = max(c.high for c in vpvr_window)
    low_30d = min(c.low for c in vpvr_window)
    range_height_30d = (high_30d - low_30d) / max(low_30d, Decimal("1e-8"))
    pos_in_range = (close - low_30d) / max(high_30d - low_30d, Decimal("1e-8"))

    d_bars = closed_1d if len(closed_1d) >= 15 else resample_candles(closed_4h, "1d")
    w_bars = closed_1w if len(closed_1w) >= 15 else resample_candles(d_bars, "1w")
    rsi_4h = (rsi(closes_4h, 14) or [Decimal("50")])[-1]
    rsi_1d = (rsi([c.close for c in d_bars], 14) or [rsi_4h])[-1] if len(d_bars) >= 15 else rsi_4h
    rsi_1w = (rsi([c.close for c in w_bars], 14) or [rsi_1d])[-1] if len(w_bars) >= 15 else rsi_1d

    has_bearish_div = (
        any(d.kind == "bearish" for d in confirmed_rsi_divergences(closed_4h, lookback=48, min_rsi_delta=Decimal("2.0")))
        or any(d.kind == "bearish" for d in confirmed_macd_divergences(closed_4h, lookback=48))
        or (len(d_bars) >= 20 and any(d.kind == "bearish" for d in confirmed_rsi_divergences(d_bars, lookback=30, min_rsi_delta=Decimal("2.0"))))
        or (len(d_bars) >= 30 and any(d.kind == "bearish" for d in confirmed_macd_divergences(d_bars, lookback=30)))
    )
    _, _, macd_hist_4h_series = macd(closes_4h)
    macd_hist_4h = macd_hist_4h_series[-1] if macd_hist_4h_series else ZERO
    macd_hist_4h_prev = macd_hist_4h_series[-2] if len(macd_hist_4h_series) >= 2 else macd_hist_4h
    recent_bars_4h = closed_4h[-8:]
    upper_wick_ratio_4h = max(
        ((b.high - max(b.open, b.close)) / max(b.high - b.low, Decimal("1e-8")))
        for b in recent_bars_4h
    )
    ret_7d = (close - closed_1h[-169].close) / closed_1h[-169].close if len(closed_1h) >= 169 else ZERO
    ret_14d = (close - closed_1h[-337].close) / closed_1h[-337].close if len(closed_1h) >= 337 else ret_7d
    high_7d = max(c.high for c in closed_1h[-168:]) if len(closed_1h) >= 168 else high_30d
    taker_24h = deriv.taker_buy_sell_ratio_24h if (deriv and deriv.taker_buy_sell_ratio_24h is not None) else taker_buy_ratio_from_candles(closed_1h, hours=24)

    short_features = ShortArchetypeFeatures(
        close=close, high_24h=high_24h, high_30d=high_30d, ema50_4h=ema50_4h, poc_30d=vp.poc_price,
        atr14_4h=atr14_4h, has_bearish_div=has_bearish_div, rsi_4h=rsi_4h, rsi_1d=rsi_1d, rsi_1w=rsi_1w,
        oi_delta_7d=deriv.open_interest_change_7d if deriv else None,
        upper_wick_ratio_4h=upper_wick_ratio_4h, max_ret_30d=range_height_30d,
        taker_buy_ratio_24h=taker_24h, macd_hist_4h=macd_hist_4h, pos_in_range_30d=pos_in_range,
        macd_hist_4h_prev=macd_hist_4h_prev, runup_30d=range_height_30d, ret_14d=ret_14d,
    )
    archetype = classify_short_archetype(short_features)
    if archetype is None:
        if pos_in_range < Decimal("0.45"):
            sub = "pos_in_range_lt_0.45"
        else:
            sub = "no_utad_exhaustion_dist_vcp_match"
        return {
            "status": "rejected",
            "blocker": f"no_short_archetype:{sub}",
            "pos_in_range_30d": float(pos_in_range),
            "rsi_4h": float(rsi_4h),
            "rsi_1d": float(rsi_1d),
            "has_bearish_div": has_bearish_div,
            "funding_8h": float(funding_rate) if funding_rate is not None else None,
            "close": float(close),
        }

    # Evaluate S1-S5 + anti-squeeze circuit breaker
    high_idx = max(range(len(vpvr_window)), key=lambda i: vpvr_window[i].high)
    days_since_high = Decimal(str(len(vpvr_window) - 1 - high_idx)) / Decimal("24")
    low_72h = min(c.low for c in closed_1h[-72:]) if len(closed_1h) >= 72 else low_30d
    gate_inputs = ShortGateInputs(
        close=close, ema50_4h=ema50_4h, high_7d=high_7d, range_height_30d=range_height_30d,
        ret_7d=ret_7d, ret_14d=ret_14d, high_30d=high_30d, low_30d=low_30d,
        days_since_30d_high=days_since_high, ema20_4h=ema20_4h, atr14_4h=atr14_4h,
        ret_24h=ret_24h, rsi_1h=snap_1h.rsi14, rsi_4h=rsi_4h, has_bearish_div=has_bearish_div,
        has_climax_blowoff=(rsi_1d >= Decimal("76.0")) and (upper_wick_ratio_4h >= Decimal("0.45")),
        poc_30d=vp.poc_price, macd_hist_4h=macd_hist_4h, taker_buy_ratio_24h=taker_24h,
        funding_rate_8h=funding_rate,
        oi_delta_7d=deriv.open_interest_change_7d if deriv else None,
        oi_delta_1h=deriv.open_interest_change_1h if deriv else None,
        taker_buy_ratio_1h=deriv.taker_buy_sell_ratio if deriv else None,
        low_72h=low_72h, high_24h=high_24h,
        open_4h=closed_4h[-1].open, high_4h=closed_4h[-1].high, low_4h=closed_4h[-1].low,
        rsi_1d=rsi_1d, macd_hist_4h_prev=macd_hist_4h_prev, basis_bps=ZERO, runup_30d=range_height_30d,
    )
    gates_passed, gate_details = evaluate_short_gates(gate_inputs, archetype)
    if not gates_passed:
        failed_gates = [k for k, v in gate_details.items() if not v]
        if failed_gates == ["S1", "S2", "S3", "S4", "S5"]:
            f8 = funding_rate or ZERO
            if funding_rate is not None and f8 < ZERO and close >= ema50_4h * Decimal("0.975"):
                blocker = "anti_squeeze_veto:negative_funding_above_ema50"
            else:
                blocker = "anti_squeeze_veto:buyer_dominated_trend"
        else:
            blocker = "gates_failed:" + ",".join(failed_gates)
        return {
            "status": "rejected",
            "archetype": archetype.value,
            "blocker": blocker,
            "pos_in_range_30d": float(pos_in_range),
            "rsi_4h": float(rsi_4h),
            "has_bearish_div": has_bearish_div,
            "funding_8h": float(funding_rate) if funding_rate is not None else None,
            "close": float(close),
        }

    # Passed archetype + S1-S5, so failure was bracket / entry proximity / R:R
    try:
        bracket = build_short_bracket(
            close=close, ema50_4h=ema50_4h, poc_30d=vp.poc_price, atr14_4h=atr14_4h,
            local_consolidation_low_4h=min(c.low for c in closed_4h[-12:]),
            swing_high_4h=max(c.high for c in closed_4h[-42:]),
            low_30d=low_30d, vah_30d=vp.vah_price, val_30d=vp.val_price, tick_size=inst.price_tick,
        )
    except InvalidExecutionBracket as exc:
        return {"status": "rejected", "archetype": archetype.value, "blocker": f"invalid_bracket:{exc}", "close": float(close)}
    dist_entry = abs(close - bracket.suggested_entry) / max(close, EPSILON)
    if dist_entry > Decimal("0.045"):
        return {"status": "rejected", "archetype": archetype.value, "blocker": "dist_to_entry_gt_4.5pct", "close": float(close)}
    if bracket.asymmetric_rr < Decimal("1.40"):
        return {"status": "rejected", "archetype": archetype.value, "blocker": "rr_lt_1.40", "close": float(close)}
    return {"status": "rejected", "archetype": archetype.value, "blocker": "other", "close": float(close)}


def forward_short_outcome(
    candles_1h: list[Candle], signal_dt: datetime, entry: float | None = None, stop: float | None = None
) -> dict[str, Any]:
    fwd = [c for c in candles_1h if c.opened_at >= signal_dt]
    if not fwd:
        return {"available": False}
    ref_price = float(fwd[0].open)
    horizon_24 = fwd[:24]
    horizon_48 = fwd[:48]

    def stats(bars: list[Candle]) -> dict[str, Any]:
        if not bars:
            return {"available": False}
        end_close = float(bars[-1].close)
        max_high = max(float(b.high) for b in bars)
        min_low = min(float(b.low) for b in bars)
        short_ret = (ref_price - end_close) / ref_price * 100.0
        max_drop = (ref_price - min_low) / ref_price * 100.0
        max_squeeze = (max_high - ref_price) / ref_price * 100.0
        breached_6pct = max_squeeze >= 6.0
        breached_stop = (max_high >= stop) if stop is not None else None
        return {
            "bars": len(bars),
            "short_return_pct": round(short_ret, 2),
            "max_drop_favorable_pct": round(max_drop, 2),
            "max_squeeze_adverse_pct": round(max_squeeze, 2),
            "breached_6pct_squeeze_cap": breached_6pct,
            "breached_stop": breached_stop,
        }

    return {"available": True, "ref_price": ref_price, "h24": stats(horizon_24), "h48": stats(horizon_48)}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="Only read cached tmp/top_gainer_short_raw")
    args = parser.parse_args()

    cohorts = load_cohorts()
    all_symbols = sorted(
        {"BTCUSDT"}
        | set(cohorts["nov8"]["symbols"].keys())
        | set(cohorts["nov12"]["symbols"].keys())
    )
    print(f"Total unique symbols across Nov 8-10 and Nov 12-14 top-10 + losers: {len(all_symbols)}")

    if not args.offline:
        sem = asyncio.Semaphore(8)
        async with httpx.AsyncClient(timeout=15.0) as client:
            tasks = [fetch_symbol_history(client, sem, sym) for sym in all_symbols]
            raw_list = await asyncio.gather(*tasks)
        raw_by_sym = {d["symbol"]: d for d in raw_list}
    else:
        raw_by_sym = {
            sym: json.loads((RAW_CACHE_DIR / f"{sym}.json").read_text())
            for sym in all_symbols
            if (RAW_CACHE_DIR / f"{sym}.json").exists()
        }

    settings = load_settings()
    macro = MacroSentimentContext(
        fear_greed=FearGreedReading(value=50, classification="Neutral", observed_at=datetime(2026, 11, 10, tzinfo=UTC)),
        stablecoin_supply=StablecoinSupplyMetric(
            total_circulating_usd=Decimal("160000000000"),
            change_1d_pct=ZERO,
            change_7d_pct=ZERO,
            change_30d_pct=ZERO,
        ),
    )
    screener = CryptoOpportunityScreener(settings, feature_contract="discovery_v2")

    # Parse candles & derivatives maps once per symbol
    parsed: dict[str, dict[str, Any]] = {}
    for sym, raw in raw_by_sym.items():
        c1h = parse_candles(raw["klines_1h"])
        if not c1h:
            continue
        c4h = parse_candles(raw["klines_4h"])
        c1d = parse_candles(raw["klines_1d"])
        c1w = parse_candles(raw["klines_1w"])
        oi_map = {int(r["timestamp"]): Decimal(str(r["sumOpenInterest"])) for r in raw["oi_1h"] if float(r["sumOpenInterest"]) > 0}
        taker_map = {
            int(r["timestamp"]): (Decimal(str(r["buyVol"])), Decimal(str(r["sellVol"])))
            for r in raw["taker_1h"]
        }
        funding_list = sorted((int(r["fundingTime"]), Decimal(str(r["fundingRate"]))) for r in raw["funding"])
        inst = make_instrument(sym, infer_tick(c1h))
        parsed[sym] = {
            "inst": inst, "c1h": c1h, "c4h": c4h, "c1d": c1d, "c1w": c1w,
            "oi_map": oi_map, "taker_map": taker_map, "funding": funding_list,
        }

    btc_1h = parsed["BTCUSDT"]["c1h"]
    report: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat(), "cohorts": {}}

    for cid, cohort in cohorts.items():
        anchor: datetime = cohort["anchor"]
        # Step through every closed 1h boundary from anchor to anchor+48h (49 steps)
        start_hour = anchor.replace(minute=0, second=0, microsecond=0)
        if anchor.minute > 0:
            # First closed 1H bar at or before anchor is start_hour; scan every 1H from start_hour to +48h
            pass
        step_times = [start_hour + timedelta(hours=h) for h in range(49)]

        blocker_counts: Counter[str] = Counter()
        confirmed_signals: list[dict[str, Any]] = []
        near_miss_4_of_5: list[dict[str, Any]] = []
        symbol_summaries: dict[str, Any] = {}

        for sym, meta in sorted(cohort["symbols"].items()):
            pdata = parsed.get(sym)
            if not pdata:
                continue
            first_obs = meta.get("first_observed_top10_at")
            first_obs_dt = datetime.fromisoformat(first_obs) if first_obs else None

            first_5_of_5: dict[str, Any] | None = None
            first_4_of_5: dict[str, Any] | None = None
            first_archetype: dict[str, Any] | None = None
            sym_blockers: Counter[str] = Counter()
            hourly_traces = []

            for dt in step_times:
                cutoff_ms = int(dt.timestamp() * 1000)
                c1h = [c for c in pdata["c1h"] if c.closed_at <= dt]
                c4h = [c for c in pdata["c4h"] if c.closed_at <= dt]
                c1d = [c for c in pdata["c1d"] if c.closed_at <= dt]
                c1w = [c for c in pdata["c1w"] if c.closed_at <= dt]
                if len(c1h) < 210 or len(c4h) < 55:
                    continue
                deriv, f_rate = build_derivatives_at(pdata["oi_map"], pdata["taker_map"], pdata["funding"], cutoff_ms)
                diag = diagnose_short_step(
                    screener, pdata["inst"], c1h, c4h, c1d, c1w, btc_1h, deriv, f_rate, dt, macro
                )
                diag["at"] = dt.isoformat()
                diag["after_top10_entry"] = (
                    True if meta.get("initial_inside") else (first_obs_dt is not None and dt >= first_obs_dt)
                )
                hourly_traces.append(diag)

                if diag.get("archetype") and first_archetype is None:
                    first_archetype = diag
                if diag["status"] == "confirmed_5_of_5" and first_5_of_5 is None:
                    first_5_of_5 = diag
                elif diag["status"] == "partial_candidate" and diag.get("checklist_passed") == 4 and first_4_of_5 is None:
                    first_4_of_5 = diag

                if diag.get("blocker"):
                    sym_blockers[diag["blocker"]] += 1
                    blocker_counts[diag["blocker"]] += 1

            # Measure forward outcome of first_5_of_5, first_4_of_5, and anchor/top10
            sig5_fwd = (
                forward_short_outcome(pdata["c1h"], datetime.fromisoformat(first_5_of_5["at"]), first_5_of_5["entry"], first_5_of_5["stop"])
                if first_5_of_5 else None
            )
            sig4_fwd = (
                forward_short_outcome(pdata["c1h"], datetime.fromisoformat(first_4_of_5["at"]), first_4_of_5["entry"], first_4_of_5["stop"])
                if first_4_of_5 else None
            )
            if first_5_of_5:
                confirmed_signals.append({"symbol": sym, "signal": first_5_of_5, "forward": sig5_fwd})
            if first_4_of_5 and not first_5_of_5:
                near_miss_4_of_5.append({"symbol": sym, "signal": first_4_of_5, "forward": sig4_fwd})

            endpoint_ret = meta.get("endpoint_return_pct")
            ret_24 = meta.get("return_24h_endpoint_pct")
            symbol_summaries[sym] = {
                "initial_inside": meta.get("initial_inside"),
                "first_observed_top10_at": first_obs,
                "endpoint_48h_pct": float(endpoint_ret) if endpoint_ret is not None else None,
                "endpoint_24h_pct": float(ret_24) if ret_24 is not None else None,
                "sampled_max_pct": float(meta["sampled_max_return_pct"]) if meta.get("sampled_max_return_pct") is not None else None,
                "sampled_min_pct": float(meta["sampled_min_return_pct"]) if meta.get("sampled_min_return_pct") is not None else None,
                "triggered_5_of_5": first_5_of_5 is not None,
                "first_5_of_5": first_5_of_5,
                "first_5_of_5_forward": sig5_fwd,
                "triggered_4_of_5": first_4_of_5 is not None,
                "first_4_of_5": first_4_of_5,
                "first_4_of_5_forward": sig4_fwd,
                "first_archetype": first_archetype,
                "top_blockers": sym_blockers.most_common(5),
            }

        def summarize_signals(items: list[dict[str, Any]]) -> dict[str, Any]:
            r24 = [x["forward"]["h24"]["short_return_pct"] for x in items if x["forward"] and x["forward"]["h24"].get("short_return_pct") is not None]
            r48 = [x["forward"]["h48"]["short_return_pct"] for x in items if x["forward"] and x["forward"]["h48"].get("short_return_pct") is not None]
            sq24 = [x["forward"]["h24"]["max_squeeze_adverse_pct"] for x in items if x["forward"] and x["forward"]["h24"].get("max_squeeze_adverse_pct") is not None]
            sq6 = sum(1 for x in items if x["forward"] and x["forward"]["h24"].get("breached_6pct_squeeze_cap"))
            return {
                "count": len(items),
                "symbols": [x["symbol"] for x in items],
                "mean_24h_short_return_pct": round(sum(r24) / len(r24), 2) if r24 else None,
                "positive_24h_count": sum(1 for v in r24 if v > 0),
                "mean_48h_short_return_pct": round(sum(r48) / len(r48), 2) if r48 else None,
                "positive_48h_count": sum(1 for v in r48 if v > 0),
                "mean_24h_max_squeeze_pct": round(sum(sq24) / len(sq24), 2) if sq24 else None,
                "breached_6pct_squeeze_cap_24h": sq6,
            }

        report["cohorts"][cid] = {
            "name": cohort["name"],
            "symbols_evaluated": len(symbol_summaries),
            "confirmed_5_of_5_summary": summarize_signals(confirmed_signals),
            "near_miss_4_of_5_only_summary": summarize_signals(near_miss_4_of_5),
            "top_hourly_blockers": blocker_counts.most_common(15),
            "focus_dumpers": {
                sym: symbol_summaries[sym]
                for sym in ("JCTUSDT", "ONUSDT", "INITUSDT", "BULLAUSDT", "AGTUSDT", "QUSDT", "TAUSDT", "BMTUSDT", "AINUSDT", "AKEUSDT")
                if sym in symbol_summaries
            },
            "symbols": symbol_summaries,
        }

    EVIDENCE_OUT.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_OUT.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Wrote study report to {EVIDENCE_OUT}")
    for cid, cdata in report["cohorts"].items():
        print(f"\n=== {cdata['name']} ({cdata['symbols_evaluated']} symbols) ===")
        print("Confirmed 5/5 shorts:", cdata["confirmed_5_of_5_summary"])
        print("4/5 near-misses (only):", cdata["near_miss_4_of_5_only_summary"])
        print("Top hourly blockers:", cdata["top_hourly_blockers"][:8])


if __name__ == "__main__":
    asyncio.run(main())
