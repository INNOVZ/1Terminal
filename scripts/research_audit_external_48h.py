"""Offline audit of the supplied experiment; writes only a new audit directory."""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import socket
from datetime import datetime
from decimal import Decimal
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False)
    raw_path = args.source / "raw_inputs_20261103T1200Z.json"
    original_path = args.source / "experiment_results_20261103T1200Z.json"
    script_path = args.source / "reproduce_48h_experiment.py"
    raw = json.loads(raw_path.read_text())
    original = json.loads(original_path.read_text())
    logs = args.source.parent / ".system_generated/tasks"
    source_files = [
        script_path,
        raw_path,
        original_path,
        logs / "task-279.log",
        logs / "task-286.log",
    ]
    hashes_before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}

    spec = importlib.util.spec_from_file_location("external_experiment", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def deny(*args, **kwargs):
        raise RuntimeError("Network disabled by independent audit")

    socket.socket = deny
    socket.create_connection = deny
    socket.getaddrinfo = deny
    module.RESULTS_PATH = args.output / "replay.json"
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        module.run_offline_reproduction()
    (args.output / "stdout.log").write_text(stdout.getvalue())
    replay = json.loads(module.RESULTS_PATH.read_text())
    assert replay == original, "Saved reproduction mismatch"

    independent = json.loads((args.evidence / "2026-11-03-rome-hourly.json").read_text())["results"]
    oi_independent = {}
    for filename in ["2026-11-04-pre-move-data.json", "2026-11-04-yesterday-pre-move-data.json"]:
        oi_independent.update(json.loads((args.evidence / filename).read_text())["results"])
    quality, overlaps, short_oi, future_weekly = [], [], [], []
    for symbol, data in raw["symbols"].items():
        for key, seconds in [("candles_1h", 3600), ("candles_4h", 14400), ("candles_1w", 604800)]:
            candles = data[key]
            opens = [datetime.fromisoformat(c["opened_at"]).timestamp() for c in candles]
            assert all(b - a == seconds for a, b in zip(opens, opens[1:], strict=False))
            for candle in candles:
                op, high, low, close = [
                    Decimal(candle[k]) for k in ["open", "high", "low", "close"]
                ]
                assert all(x.is_finite() and x > 0 for x in [op, high, low, close])
                assert low <= min(op, close) <= max(op, close) <= high
                assert candle["closed"]
                assert Decimal(candle["volume"]) >= 0
                assert (
                    0
                    <= Decimal(candle["taker_buy_quote_volume"])
                    <= Decimal(candle["quote_volume"])
                )
                assert (
                    round(
                        (
                            datetime.fromisoformat(candle["closed_at"]).timestamp()
                            - datetime.fromisoformat(candle["opened_at"]).timestamp()
                        )
                        * 1000
                    )
                    == seconds * 1000 - 1
                )
            quality.append(
                {"symbol": symbol, "dataset": key, "continuous_valid_rows": len(candles)}
            )
        oi = data["open_interest_hist_1h"]
        assert all(
            b["timestamp"] - a["timestamp"] == 3600000 for a, b in zip(oi, oi[1:], strict=False)
        )
        assert all(
            Decimal(row["sumOpenInterest"]).is_finite() and Decimal(row["sumOpenInterest"]) > 0
            for row in oi
        )
        index = {
            int(datetime.fromisoformat(c["opened_at"]).timestamp() * 1000): c
            for c in data["candles_1h"]
        }
        count = 0
        for bar in independent[symbol]["result"]:
            if bar[0] not in index:
                continue
            count += 1
            for key, col in [
                ("open", 1),
                ("high", 2),
                ("low", 3),
                ("close", 4),
                ("volume", 5),
                ("quote_volume", 7),
                ("taker_buy_volume", 9),
                ("taker_buy_quote_volume", 10),
            ]:
                assert Decimal(index[bar[0]][key]) == Decimal(str(bar[col]))
        oi_index = {
            r["timestamp"]: r
            for r in oi_independent.get(symbol, {}).get("oi", {}).get("result", [])
        }
        oi_count = 0
        for row in oi:
            if row["timestamp"] in oi_index:
                oi_count += 1
                assert Decimal(row["sumOpenInterest"]) == Decimal(
                    oi_index[row["timestamp"]]["sumOpenInterest"]
                )
        overlaps.append(
            {"symbol": symbol, "identical_hourly_bars": count, "identical_oi_samples": oi_count}
        )
        for h in [44, 40, 36, 32, 28, 24, 20, 16, 12, 8, 4, 0]:
            end = int(
                datetime.fromisoformat(data["candles_1h"][-1 - h]["closed_at"]).timestamp() * 1000
            )
            valid = [r for r in oi if r["timestamp"] <= end + 60000]
            lag_hours = (
                valid[-1]["timestamp"] - valid[max(0, len(valid) - 169)]["timestamp"]
            ) / 3600000
            if lag_hours != 168:
                short_oi.append(
                    {"symbol": symbol, "horizon": h, "claimed_7d_actual_hours": lag_hours}
                )
            if any(
                int(datetime.fromisoformat(c["closed_at"]).timestamp() * 1000) > end
                for c in data["candles_1w"]
            ):
                future_weekly.append({"symbol": symbol, "horizon": h})

    comparable = {}
    for symbol, baseline in replay["task_279_reproduction"].items():
        common = [r for r in baseline["steps"] if r["horizon_hours_ago"] <= 36]
        comparable[symbol] = {
            "observations_per_model": len(common),
            "baseline_signals": sum(r["base_passed"] for r in common),
            "patched_signals": sum(r["patched_passed"] for r in common),
            "challenger_signals": replay["task_286_reproduction"][symbol]["continuous_hits"],
        }
    original_lines = (logs / "task-279.log").read_text().splitlines()
    replay_lines = stdout.getvalue().splitlines()

    def extract(lines):
        return [
            line.strip() for line in lines if line.strip().startswith("T-") or ">>> SUMMARY" in line
        ]

    assert extract(original_lines) == extract(replay_lines)
    task286 = {}
    for line in (logs / "task-286.log").read_text().splitlines():
        if "Continuous-Factor Hits=" in line:
            task286[line.split()[0]] = ast.literal_eval(line.split(" -> ")[1])
    differences = []
    for symbol, hits in task286.items():
        actual = replay["task_286_reproduction"][symbol]["hits"]
        assert len(hits) == len(actual)
        for old, new in zip(hits, actual, strict=True):
            assert list(old[:2]) == new[:2], "Original signal time/price differs"
            if list(old) != new:
                differences.append({"symbol": symbol, "original": old, "reproduction": new})
    assert hashes_before == {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files
    }
    result = {
        "source_hashes_unchanged": hashes_before,
        "saved_json_exact_match": True,
        "original_task279_log_rows_exact_match": True,
        "original_task286_signal_times_prices_counts_match": True,
        "original_task286_value_differences": differences,
        "raw_quality": quality,
        "independent_crosschecks": overlaps,
        "truncated_7d_oi_windows": short_oi,
        "unfiltered_future_weekly_inputs": future_weekly,
        "common_ten_scan_comparison": comparable,
    }
    (args.output / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "matched_saved_results": True,
                "common_comparison": comparable,
                "hourly_crosschecks": sum(r["identical_hourly_bars"] for r in overlaps),
                "oi_crosschecks": sum(r["identical_oi_samples"] for r in overlaps),
                "original_log_value_differences": len(differences),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
