"""Retrospective, offline event study of the frozen November 8 cohort.

Uses subsequently retrieved public histories. No screening decisions are changed.
Run from the repository root with Python 3.11 or newer.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/security/evidence"
RAW = EVIDENCE / "2026-11-11-pre-gainer-raw"
RAW_FOUR = EVIDENCE / "2026-11-11-pre-gainer-raw-4h"
PREFIX = "2026-11-11-pre-gainer"
HOUR = 3_600_000


def milliseconds(value):
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def iso(value):
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


def change(new, old):
    return (float(new) / float(old) - 1) * 100 if float(old) else None


def ratio(new, old):
    return new / old if old else None


def read_result(result):
    assert not result.get("isError"), "Connector returned an error"
    content = json.loads(next(c["text"] for c in result["content"] if c["type"] == "text"))
    structured = result.get("structuredContent")
    if structured is not None:
        assert structured == content, "Connector representations differ"
    assert isinstance(content.get("result"), list)
    return content["result"]


def ema(values, length):
    if len(values) < length:
        return None
    result = sum(values[:length]) / length
    alpha = 2 / (length + 1)
    for value in values[length:]:
        result += alpha * (value - result)
    return result


def features(data, cutoff, btc):
    # A bar overlapping cutoff is excluded, even if the connector returned it.
    candles = [c for c in data["klines"] if c[6] < cutoff]
    last24 = candles[-24:]
    expected = [cutoff // HOUR * HOUR - i * HOUR for i in range(24, 0, -1)]
    if len(last24) != 24 or [c[0] for c in last24] != expected:
        return {"available": False, "cutoff": iso(cutoff), "reason": "noncontiguous_last24"}
    close = float(candles[-1][4])
    volume = lambda rows: sum(float(c[7]) for c in rows)
    pre6, prior18 = candles[-6:], candles[-24:-6]
    taker = sum(float(c[10]) for c in pre6)
    total = volume(pre6)
    four = [c for c in data["klines_4h"] if c[6] < cutoff]
    four_ema = ema([float(c[4]) for c in four], 50)
    oi = {int(x["timestamp"]): x for x in data["oi"]}
    # One-hour lag is a sensitivity check for unarchived OI publication latency.
    oi_cutoff = cutoff // HOUR * HOUR
    oi_change = lambda end: change(oi[end]["sumOpenInterest"], oi[end - 6 * HOUR]["sumOpenInterest"]) if end in oi and end - 6 * HOUR in oi else None
    funding = [x for x in data["funding"] if int(x["fundingTime"]) < cutoff]
    btc_candles = [c for c in btc["klines"] if c[6] < cutoff]
    btc_return = change(btc_candles[-1][4], btc_candles[-6][1]) if len(btc_candles) >= 6 else None
    ret6 = change(close, pre6[0][1])
    previous20 = sum(float(c[5]) for c in candles[-21:-1]) / 20
    result = {
        "available": True, "cutoff": iso(cutoff), "last_closed_at": iso(candles[-1][6]),
        "close": close, "return_1h_pct": change(close, candles[-1][1]),
        "return_4h_pct": change(close, candles[-4][1]), "return_6h_pct": ret6,
        "return_24h_pct": change(close, candles[-24][1]),
        "relative_strength_6h_vs_btc_pp": ret6 - btc_return if btc_return is not None else None,
        "quote_volume_6h_vs_prior18h_mean": ratio(volume(pre6) / 6, volume(prior18) / 18),
        "rvol_1h_base_vs_previous20": ratio(float(candles[-1][5]), previous20),
        "rvol_4h_base_vs_previous20": ratio(float(four[-1][5]), sum(float(x[5]) for x in four[-21:-1]) / 20) if len(four) >= 21 else None,
        "rvol_1h_base_inclusive20": ratio(float(candles[-1][5]), sum(float(x[5]) for x in candles[-20:]) / 20),
        "rvol_4h_base_inclusive20": ratio(float(four[-1][5]), sum(float(x[5]) for x in four[-20:]) / 20) if len(four) >= 20 else None,
        "taker_buy_sell_quote_ratio_6h": ratio(taker, total - taker),
        "oi_units_change_6h_pct": oi_change(oi_cutoff),
        "oi_units_change_6h_lagged1h_pct": oi_change(oi_cutoff - HOUR),
        "last_settled_funding_bps": float(funding[-1]["fundingRate"]) * 10000 if funding else None,
        "last_settled_funding_at": iso(int(funding[-1]["fundingTime"])) if funding else None,
        "settled_funding_sum_prior24h_bps": sum(float(x["fundingRate"]) for x in funding if int(x["fundingTime"]) >= cutoff - 24 * HOUR) * 10000,
        "ema50_4h": four_ema,
        "distance_ema50_4h_pct": change(close, four_ema) if four_ema else None,
        "distance_prior24h_high_pct": change(close, max(float(c[2]) for c in candles[-25:-1])),
        "range24h_pct_of_close": (max(float(c[2]) for c in last24) - min(float(c[3]) for c in last24)) / close * 100,
        "full336h_base_volume_ratio_recent168_to336": ratio(sum(float(c[5]) for c in candles[-168:]) / 168, sum(float(c[5]) for c in candles[-336:]) / 336) if len(candles) >= 336 and candles[-336][0] == candles[-1][0] - 335 * HOUR else None,
        "truncated239h_base_volume_ratio_recent168_to239": ratio(sum(float(c[5]) for c in candles[-168:]) / 168, sum(float(c[5]) for c in candles[-239:]) / 239) if len(candles) >= 239 else None,
    }
    assert candles[-1][6] < cutoff
    return result


def main():
    study_path = EVIDENCE / "2026-11-10-finalized-horizon-analysis.json"
    study = json.loads(study_path.read_text())
    horizon = study["horizons"]["48"]
    scan = milliseconds(study["issued_at"])
    anchor = milliseconds(study["anchor"])
    end = anchor + 48 * HOUR
    data, manifest = {}, []
    for path in sorted(RAW.glob("*.json")):
        source = json.loads(path.read_text())
        symbol = source["symbol"]
        decoded = {key: read_result(value) for key, value in source["results"].items()}
        four_path = RAW_FOUR / path.name
        four_source = json.loads(four_path.read_text())
        decoded["klines_4h"] = read_result(four_source["response"])
        problems = []
        candles = decoded["klines"]
        assert candles and all(len(c) >= 11 for c in candles)
        times = [c[0] for c in candles]
        assert times == sorted(set(times)), symbol
        for c in candles:
            assert c[6] == c[0] + HOUR - 1
            assert float(c[3]) <= min(float(c[1]), float(c[4])) <= max(float(c[1]), float(c[4])) <= float(c[2])
            assert 0 <= float(c[10]) <= float(c[7])
        gaps = sum((b - a) // HOUR - 1 for a, b in zip(times, times[1:]))
        if gaps:
            problems.append(f"{gaps} missing hourly bars")
        if times[0] != source["requested"]["startTime"]:
            problems.append("requested beginning unavailable")
        if times[-1] != end // HOUR * HOUR:
            problems.append("requested ending unavailable")
        four = decoded["klines_4h"]
        assert len(four) >= 180
        assert [c[0] for c in four] == list(range(four_source["requested"]["startTime"], end // (4 * HOUR) * 4 * HOUR + 1, 4 * HOUR))
        assert all(c[6] == c[0] + 4 * HOUR - 1 for c in four)
        hourly = {c[0]: c for c in candles}
        for bar in four:
            group = [hourly.get(bar[0] + i * HOUR) for i in range(4)]
            if any(c is None for c in group):
                continue
            expected = [float(group[0][1]), max(float(c[2]) for c in group),
                        min(float(c[3]) for c in group), float(group[-1][4]),
                        sum(float(c[5]) for c in group)]
            assert all(abs(float(a) - b) <= max(abs(b) * 1e-10, 1e-10) for a, b in zip(bar[1:6], expected)), (symbol, "4h/hourly mismatch")
        for key, field in [("oi", "timestamp"), ("funding", "fundingTime")]:
            values = decoded[key]
            timestamps = [int(v[field]) for v in values]
            assert timestamps == sorted(set(timestamps)), (symbol, key)
            assert all(v["symbol"] == symbol for v in values)
            assert all(source["requested"]["startTime"] <= t <= source["requested"]["endTime"] for t in timestamps)
        data[symbol] = decoded
        manifest.append({"symbol": symbol, "path": str(path.relative_to(ROOT)),
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "rows": {k: len(v) for k, v in decoded.items()}, "problems": problems,
                         "retrieved_at": source["retrieved_at"]})
        manifest.append({"symbol": symbol, "path": str(four_path.relative_to(ROOT)),
                         "sha256": hashlib.sha256(four_path.read_bytes()).hexdigest(),
                         "rows": {"klines_4h": len(four)}, "problems": [],
                         "retrieved_at": four_source["retrieved_at"]})
    records = {}
    for group in ["new_gainer_details", "reference_details", "candidate_details", "top12_endpoint_returns"]:
        for item in horizon[group]:
            records.setdefault(item["symbol"], item)
    cohort_path = EVIDENCE / f"{PREFIX}-cohort-summary.json"
    cohort = json.loads(cohort_path.read_text())
    assert cohort["report48_sha256"] == horizon["report_sha256"]
    assert len(cohort["symbols"]) == 512
    controls_path = EVIDENCE / f"{PREFIX}-controls.json"
    controls = json.loads(controls_path.read_text())
    cohort_by_symbol = {x["symbol"]: x for x in cohort["symbols"]}
    eligible = [x for x in cohort["symbols"] if x["initial_inside"] is False and x["first_observed_top10_at"] is None and x["symbol"] not in ["BTCUSDT", "ETHUSDT"]]
    selected_controls = sorted(eligible, key=lambda x: hashlib.sha256((controls["version"] + "|" + x["symbol"]).encode()).hexdigest())[:64]
    assert [x["symbol"] for x in selected_controls] == controls["symbols"]
    for symbol in controls["symbols"]:
        records.setdefault(symbol, cohort_by_symbol[symbol])
    cases = []
    for symbol, item in sorted(records.items()):
        first = item["first_observed_top10_at"]
        event = milliseconds(first) // HOUR * HOUR if first else None
        case = {"symbol": symbol, "original": item,
                "at_scan": features(data[symbol], scan, data["BTCUSDT"]),
                "before_first_observed_entry": features(data[symbol], event, data["BTCUSDT"]) if event else None,
                "event_lags": {str(lag): features(data[symbol], event - lag * HOUR, data["BTCUSDT"]) for lag in [1, 4, 6, 12, 24]} if event else {},
                "post_scan_hourly": []}
        window = [c for c in data[symbol]["klines"] if c[0] >= anchor and c[6] < end]
        impulse = max(window, key=lambda c: float(c[4]) / float(c[1]))
        impulse_start = impulse[0]
        oi_by_time = {int(v["timestamp"]): v for v in data[symbol]["oi"]}
        oi_start = oi_by_time.get(impulse_start)
        oi_end = oi_by_time.get(impulse_start + HOUR)
        case["largest_up_hour"] = {
            "selection": "Largest positive open-to-close hour selected retrospectively inside the observation window",
            "started_at": iso(impulse_start), "return_pct": change(impulse[4], impulse[1]),
            "before": features(data[symbol], impulse_start, data["BTCUSDT"]),
            "oi_units_change_during_hour_pct": change(oi_end["sumOpenInterest"], oi_start["sumOpenInterest"]) if oi_start and oi_end else None,
            "relation_to_first_observed_top10": (
                "before" if impulse[6] < milliseconds(first) else
                "after" if impulse_start > milliseconds(first) else "overlaps_entry_hour"
            ) if first else "no_observed_entry",
        }
        # One-hour observations allow timing inspection without rebuilding production forecasts.
        for cutoff in range((anchor // HOUR + 1) * HOUR, end // HOUR * HOUR + 1, HOUR):
            case["post_scan_hourly"].append(features(data[symbol], cutoff, data["BTCUSDT"]))
        cases.append(case)
    new_symbols = {x["symbol"] for x in horizon["new_gainer_details"]}
    new_cases = [x for x in cases if x["symbol"] in new_symbols]
    control_cases = [x for x in cases if x["symbol"] in controls["symbols"]]
    summary = {"new_entrants": len(new_cases), "features": {}}
    keys = ["return_6h_pct", "return_24h_pct", "quote_volume_6h_vs_prior18h_mean",
            "relative_strength_6h_vs_btc_pp", "taker_buy_sell_quote_ratio_6h",
            "oi_units_change_6h_pct", "distance_ema50_4h_pct"]
    for time in ["at_scan", "before_first_observed_entry"]:
        summary["features"][time] = {}
        for key in keys:
            values = [x[time][key] for x in new_cases if x[time] and x[time].get("available") and x[time].get(key) is not None]
            summary["features"][time][key] = {"known": len(values), "median": median(values) if values else None, "positive": sum(v > 0 for v in values)}
    conditions = {
        "volume_acceleration_gt1": lambda f: f["quote_volume_6h_vs_prior18h_mean"] > 1,
        "volume_acceleration_ge2": lambda f: f["quote_volume_6h_vs_prior18h_mean"] >= 2,
        "above_4h_ema50": lambda f: f["distance_ema50_4h_pct"] > 0,
        "taker_buy_sell_gt1": lambda f: f["taker_buy_sell_quote_ratio_6h"] > 1,
        "positive_oi_6h": lambda f: f["oi_units_change_6h_pct"] > 0,
        "positive_btc_relative_strength_6h": lambda f: f["relative_strength_6h_vs_btc_pp"] > 0,
    }
    summary["descriptive_conditions"] = {}
    for name, condition in conditions.items():
        summary["descriptive_conditions"][name] = {}
        for time in ["at_scan", "before_first_observed_entry"]:
            known = [x for x in new_cases if x[time] and x[time].get("available")]
            summary["descriptive_conditions"][name][time] = {"known": len(known), "count": sum(condition(x[time]) for x in known)}
        summary["descriptive_conditions"][name]["comparison_sample_at_scan"] = {
            "known": len(control_cases), "count": sum(condition(x["at_scan"]) for x in control_cases)}
    cache = {}
    def control_features(symbol, cutoff):
        key = (symbol, cutoff)
        if key not in cache:
            cache[key] = features(data[symbol], cutoff, data["BTCUSDT"])
        return cache[key]
    summary["event_lead_comparison"] = {}
    for lag in [24, 12, 6, 4, 1, 0]:
        summary["event_lead_comparison"][str(lag)] = {}
        for key in keys:
            values, control_medians = [], []
            for case in new_cases:
                feature = case["before_first_observed_entry"] if lag == 0 else case["event_lags"][str(lag)]
                cutoff = milliseconds(feature["cutoff"])
                baseline = [control_features(s, cutoff)[key] for s in controls["symbols"]]
                values.append(feature[key])
                control_medians.append(median(baseline))
            summary["event_lead_comparison"][str(lag)][key] = {
                "entrant_median": median(values), "matched_control_median": median(control_medians),
                "entrants_above_same_time_control_median": sum(v > c for v, c in zip(values, control_medians)),
                "positive_count": sum(v > 0 for v in values), "count": len(values)}
    summary["funnel_counts"] = dict(Counter(x["original"]["funnel_category"] for x in new_cases))
    diagnostics = json.loads((EVIDENCE / "2026-11-10-frozen-replay-diagnostics.json").read_text())
    cases_by_symbol = {c["symbol"]: c for c in cases}
    reproduction = []
    for event in diagnostics["events"]:
        if event["arm"] != "control" or event["phase"] != "no_derivatives":
            continue
        symbol, values = event["symbol"], event["values"]
        feature = cases_by_symbol[symbol]["at_scan"]
        assert abs(feature["close"] - float(values["close"])) < 1e-12
        assert abs(feature["truncated239h_base_volume_ratio_recent168_to239"] - float(values["vol_contraction_ratio"])) < 1e-12
        assert abs(feature["rvol_1h_base_inclusive20"] - float(values["rvol_4h"])) < 1e-12
        ema_error = change(feature["ema50_4h"], values["ema50_4h"])
        assert abs(ema_error) < 0.1
        reproduction.append({"symbol": symbol, "scan_close_matches": True,
                             "legacy_volume_contraction_matches": True,
                             "legacy_rvol_field_matches_actual_1h": True,
                             "ema50_4h_relative_difference_pct": ema_error})
    summary["archived_feature_cross_checks"] = reproduction
    result = {"study_id": study["study_id"], "scan_issued_at": study["issued_at"],
              "anchor": study["anchor"], "historical_request_start": "2026-10-25T00:00:00+00:00",
              "historical_request_end": iso(end), "symbols": len(data),
              "method": "Retrospective closed-hour features; first certified rank sample is an upper-bound observation time. No production replay or causal attribution.",
              "comparison_sample": controls, "summary": summary, "cases": cases}
    output_path = EVIDENCE / f"{PREFIX}-analysis.json"
    output_path.write_text(json.dumps(result, indent=2) + "\n")
    with (EVIDENCE / f"{PREFIX}-matrix.csv").open("w", newline="") as stream:
        fields = ["symbol", "funnel_category", "first_observed_top10_at", "endpoint_return_pct", "sampled_max_return_pct", "sampled_min_return_pct"]
        feature_fields = [f"{time}_{key}" for time in ["at_scan", "before_first_observed_entry"] for key in keys]
        writer = csv.DictWriter(stream, fieldnames=fields + feature_fields)
        writer.writeheader()
        for case in cases:
            row = {key: case["original"].get(key) for key in fields}
            for time in ["at_scan", "before_first_observed_entry"]:
                row.update({f"{time}_{key}": (case[time] or {}).get(key) for key in keys})
            writer.writerow(row)
    manifest_output = {"original_summary_sha256": hashlib.sha256(study_path.read_bytes()).hexdigest(),
                       "cohort_summary_sha256": hashlib.sha256(cohort_path.read_bytes()).hexdigest(),
                       "controls_sha256": hashlib.sha256(controls_path.read_bytes()).hexdigest(),
                       "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                       "analysis_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
                       "raw_files": manifest}
    (EVIDENCE / f"{PREFIX}-manifest.json").write_text(json.dumps(manifest_output, indent=2) + "\n")
    print(json.dumps({"symbols": len(data), "rows": {key: sum(len(v[key]) for v in data.values()) for key in ["klines", "klines_4h", "oi", "funding"]},
                      "problems": [x for x in manifest if x["problems"]], "summary": summary}, indent=2))


if __name__ == "__main__":
    main()
