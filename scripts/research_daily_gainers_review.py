"""Offline descriptive audit of retained Nov 3/4 public market data; no network or trading."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/security/evidence"
HOUR = 3_600_000


def load(name):
    return json.loads((EVIDENCE / name).read_text())


def rows(value):
    if isinstance(value, dict):
        value = value.get("result", [])
    return value if isinstance(value, list) else []


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def pct(a, b):
    return (float(a) / float(b) - 1) * 100 if float(b) else None


def main():
    market = load("2026-11-04-market-input.json")
    universe = {s["symbol"] for s in market["exchange"]["symbols"]
                if s["status"] == "TRADING" and s["quoteAsset"] == "USDT"
                and s["contractType"] == "PERPETUAL"}
    current = sorted((x for x in market["tickers"] if x["symbol"] in universe),
                     key=lambda x: (-float(x["priceChangePercent"]), x["symbol"]))
    newest = max(x["closeTime"] for x in current)
    stale = [x["symbol"] for x in current if newest - x["closeTime"] > 60_000]
    absent = sorted(universe - {x["symbol"] for x in current})
    current = [x for x in current if x["symbol"] not in stale]
    daily = load("2026-11-03-rome-hourly.json")
    start = int(datetime.fromisoformat(daily["start"].replace("Z", "+00:00")).timestamp() * 1000)
    expected = [start + i * HOUR for i in range(24)]
    yesterday, missing = [], {}
    for symbol in sorted(universe):
        candles = rows(daily["results"].get(symbol, {}))
        if [c[0] for c in candles] != expected or any(c[6] != c[0] + HOUR - 1 for c in candles):
            missing[symbol] = {"bars": len(candles), "reason": "missing or incomplete hourly window"}
            continue
        yesterday.append({"symbol": symbol, "change_pct": pct(candles[-1][4], candles[0][1]),
                          "quote_volume": sum(float(c[7]) for c in candles)})
    yesterday.sort(key=lambda x: (-x["change_pct"], x["symbol"]))
    scan = load("2026-11-04-local-screener.json")
    longs = {x["symbol"]: x for x in scan["opportunities"] if x["direction"] == "long"}
    short_symbols = [x["symbol"] for x in scan["opportunities"] if x["direction"] == "short"]
    ticks = {x["symbol"]: x for x in current}
    def ranked(items, is_current=False):
        out = []
        for rank, item in enumerate(items[:10], 1):
            s = item["symbol"]
            out.append({"rank": rank, "symbol": s,
                        "change_pct": float(item["priceChangePercent"]) if is_current else item["change_pct"],
                        "current_local_long": s in longs,
                        "rank_lower": rank, "rank_upper": rank + (len(stale) + len(absent) if is_current else len(missing))})
        return out
    before = load("2026-11-04-pre-move-data.json")["results"]
    extra = EVIDENCE / "2026-11-04-yesterday-pre-move-data.json"
    if extra.exists():
        before.update(json.loads(extra.read_text())["results"])
    events = []
    current_start = int(datetime(2026, 11, 3, 8, tzinfo=timezone.utc).timestamp() * 1000)
    current_end = int(datetime(2026, 11, 4, 8, tzinfo=timezone.utc).timestamp() * 1000)
    for label, symbols, begin, end in [
        ("current_winners_hourly_proxy", [x["symbol"] for x in current[:10]], current_start, current_end),
        ("yesterday_rome", [x["symbol"] for x in yesterday[:10]], start, start + 24 * HOUR),
    ]:
        for s in symbols:
            evidence = before.get(s, {})
            candles = rows(evidence.get("klines", {}))
            window = [c for c in candles if begin <= c[0] < end and c[6] < end]
            if len(window) != 24:
                events.append({"period": label, "symbol": s, "error": "incomplete event window"})
                continue
            # Retrospective event selection. All explanatory features end BEFORE this bar opens.
            event = max(window, key=lambda c: float(c[4]) / float(c[1]))
            t = event[0]
            history = [c for c in candles if c[6] < t]
            last6, prior18 = history[-6:], history[-24:-6]
            contiguous = len(history) >= 24 and [c[0] for c in history[-24:]] == [t - i * HOUR for i in range(24, 0, -1)]
            oi = {int(x["timestamp"]): x for x in rows(evidence.get("oi", {}))}
            oi_change = None
            if t in oi and t - 6 * HOUR in oi:
                oi_change = pct(oi[t]["sumOpenInterest"], oi[t - 6 * HOUR]["sumOpenInterest"])
            events.append({"period": label, "symbol": s, "largest_up_hour_start": iso(t),
                           "largest_up_hour_return_pct": pct(event[4], event[1]),
                           "pre6h_return_pct": pct(last6[-1][4], last6[0][1]) if contiguous else None,
                           "pre6h_quote_volume_vs_prior18h": (sum(float(c[7]) for c in last6) / 6) / (sum(float(c[7]) for c in prior18) / 18) if contiguous and sum(float(c[7]) for c in prior18) else None,
                           "pre6h_oi_units_change_pct": oi_change,
                           "move_before_event_pct": pct(event[1], window[0][1]),
                           "pre24h_complete": contiguous})
    candidates = [{"symbol": s, "evaluated_at": x["evaluated_at"],
                   "snapshot_price": x["current_price"], "current_market_price": ticks.get(s, {}).get("lastPrice"),
                   "change_since_scan_price_pct": pct(ticks[s]["lastPrice"], x["current_price"]) if s in ticks else None,
                   "current_rank": next((i for i, tick in enumerate(current, 1) if tick["symbol"] == s), None)}
                  for s, x in longs.items()]
    names = [p for p in EVIDENCE.glob("2026-11-0[34]-*.json") if p.name != "2026-11-04-gainers-analysis.json"]
    result = {"universe_count": len(universe), "ticker_count": len(current),
              "current_unknown_symbols": stale + absent,
              "snapshot_close_time_range": [iso(min(x["closeTime"] for x in current)), iso(max(x["closeTime"] for x in current))],
              "yesterday_start": daily["start"], "yesterday_end_exclusive": daily["end_exclusive"],
              "yesterday_complete_count": len(yesterday), "yesterday_missing": missing,
              "current_top10": ranked(current, True), "yesterday_top10": ranked(yesterday),
              "local_scan_timestamp": scan["timestamp"], "local_longs": candidates,
              "local_shorts": short_symbols, "events": events,
              "input_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(names)}}
    path = EVIDENCE / "2026-11-04-gainers-analysis.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in ["input_sha256", "events"]}, indent=2))


if __name__ == "__main__":
    main()
