"""Versioned, offline adapter-fidelity checks for newly recorded public evidence.

V2 is a faithful-input contract, not certification that legacy features are well calibrated.
Use in an isolated CLI process: adapter clocks are scoped and restored during reconstruction.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from algotrader.adapters.binance import base, futures
from algotrader.application.scan_replay import replay_scan
from algotrader.domain.errors import VenueError
from algotrader.infrastructure.scan_archive import canonical_bytes
from scripts.replay_contract import (
    ReplayUnavailable,
    _candles,
    check_call,
    digest,
    fresh,
    timestamp,
)

SCHEMA = "atlas-public-evidence-v2"
ACQUISITION_SCHEMA = "atlas-public-evidence-v3"
DISCOVERY_SCHEMA = "atlas-public-evidence-v4"
MAX_CAPTURE_SECONDS = 600


@contextmanager
def adapter_clock(at):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.astimezone(tz) if tz else at.replace(tzinfo=None)

    previous = base.datetime, futures.datetime
    base.datetime = futures.datetime = Clock
    try:
        yield
    finally:
        base.datetime, futures.datetime = previous


class ReceiptClient:
    def __init__(self, rows):
        self.rows = defaultdict(list)
        self.missing = False
        for row in rows:
            self.rows[digest((row["path"], row["params"]))].append(row)

    async def request(self, method, path, *, params=None):
        key = digest((path, {k: str(v) for k, v in (params or {}).items()}))
        group = self.rows.get(key)
        if method != "GET" or not group:
            self.missing = True
            raise VenueError("missing_evidence_response")
        row = group.pop(0)
        if row.get("error") or row.get("status") != 200:
            raise VenueError("recorded_evidence_failure")
        return row["payload"]

    def assert_consumed(self):
        if self.missing or any(self.rows.values()):
            raise ReplayUnavailable("http_receipts_did_not_match_adapter")


def normalized(value):
    # Arrival time is checked independently; it is not a market-derived number.
    if isinstance(value, (list, tuple)):
        return [normalized(x) for x in value]
    if hasattr(value, "observed_at"):
        return replace(value, observed_at=datetime(2000, 1, 1, tzinfo=UTC))
    return value


async def verify_call(call, frame, receipts, decision, *, acquisition_freshness=False):
    if frame.get("call_sha256") != digest(call) or call.error or frame.get("error"):
        raise ReplayUnavailable("failed_or_unbound_evidence_call")
    start, end = timestamp(frame["started_at"]), timestamp(frame["available_at"])
    if not start <= end <= decision:
        raise ReplayUnavailable("invalid_evidence_call_bounds")
    rows = [r for r in receipts if r.get("scope") == frame["scope"]]
    if not rows:
        raise ReplayUnavailable("missing_http_receipts")
    for row in rows:
        if not start <= timestamp(row["started_at"]) <= timestamp(row["available_at"]) <= end:
            raise ReplayUnavailable("http_receipt_outside_call")
        if "payload" in row and row.get("payload_sha256") != digest(row["payload"]):
            raise ReplayUnavailable("http_payload_digest_mismatch")
    client = ReceiptClient(rows)
    adapter = futures.BinanceUsdmAdapter(client)
    with adapter_clock(end):
        derived = await getattr(adapter, call.method)(**call.arguments)
    client.assert_consumed()
    if canonical_bytes(normalized(derived)) != canonical_bytes(normalized(call.result)):
        raise ReplayUnavailable("raw_response_adapter_result_mismatch")
    for item in call.result if isinstance(call.result, (list, tuple)) else (call.result,):
        if hasattr(item, "observed_at") and not start <= timestamp(item.observed_at) <= end:
            raise ReplayUnavailable("value_arrival_outside_call")
    if call.method == "candles":
        # Production returns at most one current partial bar; the engine consumes only closed bars.
        partial = [i for i, c in enumerate(call.result) if not c.closed]
        if partial and partial != [len(call.result) - 1]:
            raise ReplayUnavailable("non_tail_partial_candle")
        closed = tuple(c for c in call.result if c.closed)
        _candles(replace(call, result=closed), end, minimums={"1h": 239, "4h": 479, "1w": 15})
    elif call.method in ("quotes", "instruments"):
        proof = {
            "origin": "recorded_public",
            "call_sha256": digest(call),
            "observed_at": end,
            "available_at": end,
        }
        check_call(call, proof, end if acquisition_freshness else decision)
    elif call.method == "derivatives_snapshot":
        # Re-derivation checks the actual current-OI and hourly-OI calculations independently.
        # Degraded telemetry remains explicit; do not infer missing values or certify forecasts.
        fresh(end, decision, MAX_CAPTURE_SECONDS if acquisition_freshness else 60)


def metadata_payload(bundle, path, params):
    candidates = [
        r
        for r in bundle["receipts"]
        if r.get("scope") is None
        and r.get("path") == path
        and r.get("params") == {k: str(v) for k, v in params.items()}
        and r.get("status") == 200
        and not r.get("error")
    ]
    if len(candidates) != 1:
        raise ReplayUnavailable("funding_metadata_receipt_missing_or_ambiguous")
    row = candidates[0]
    if row.get("payload_sha256") != digest(row["payload"]):
        raise ReplayUnavailable("funding_metadata_digest_mismatch")
    fresh(
        row["available_at"],
        timestamp(bundle["completed_at"]),
        MAX_CAPTURE_SECONDS if bundle["schema"] in (ACQUISITION_SCHEMA, DISCOVERY_SCHEMA) else 60,
    )
    return row["payload"]


def funding_manifest(bundle):
    """Bind selected-symbol rates to premium data; intervals to explicit policy or schedule."""
    record = bundle["record"]
    selected = {
        e["symbol"]
        for e in record.decisions
        if e["stage"] == "selection" and e["reason"] == "selected"
    }
    if bundle["schema"] == DISCOVERY_SCHEMA:
        selected.update(bundle["research_selected_symbols"])
    infos = metadata_payload(bundle, "/fapi/v1/fundingInfo", {})
    if not isinstance(infos, list):
        raise ReplayUnavailable("invalid_funding_interval_metadata")
    intervals = {r["symbol"]: r.get("fundingIntervalHours") for r in infos}
    if len(intervals) != len(infos):
        raise ReplayUnavailable("duplicate_funding_interval_metadata")
    calls = []
    for call in record.inputs:
        if call.method != "quotes":
            continue
        scope = f"{digest((call.method, call.arguments))}:{call.index}"
        premiums = [
            r
            for r in bundle["receipts"]
            if r.get("scope") == scope
            and r.get("path") == "/fapi/v1/premiumIndex"
            and r.get("status") == 200
        ]
        if len(premiums) != 1 or not isinstance(premiums[0]["payload"], list):
            raise ReplayUnavailable("bulk_premium_receipt_unavailable")
        receipt = premiums[0]
        by_symbol = {p["symbol"]: p for p in receipt["payload"]}
        if len(by_symbol) != len(receipt["payload"]):
            raise ReplayUnavailable("duplicate_premium_symbol")
        rows = {}
        for quote in call.result:
            symbol = quote.instrument_id.symbol
            if symbol not in selected:
                continue
            p = by_symbol.get(symbol)
            if not p or Decimal(str(p.get("lastFundingRate"))) != quote.funding_rate:
                raise ReplayUnavailable("premium_rate_not_bound_to_quote")
            at = datetime.fromtimestamp(int(p["time"]) / 1000, UTC)
            fresh(at, timestamp(receipt["available_at"]), 60)
            fresh(
                at,
                timestamp(bundle["completed_at"]),
                MAX_CAPTURE_SECONDS if bundle["schema"] in (ACQUISITION_SCHEMA, DISCOVERY_SCHEMA) else 60,
            )
            hours = intervals.get(symbol)
            if hours is not None:
                if type(hours) is not int:
                    raise ReplayUnavailable("invalid_funding_interval")
                interval = hours * 3600
                source = "fundingInfo"
            else:
                history = metadata_payload(
                    bundle, "/fapi/v1/fundingRate", {"symbol": symbol, "limit": 1}
                )
                if (
                    not isinstance(history, list)
                    or len(history) != 1
                    or history[0].get("symbol") != symbol
                ):
                    raise ReplayUnavailable("funding_schedule_unknown")
                settled, next_at = int(history[0]["fundingTime"]), int(p["nextFundingTime"])
                if (
                    not settled <= int(at.timestamp() * 1000) < next_at
                    or (next_at - settled) % 1000
                ):
                    raise ReplayUnavailable("funding_schedule_crossed_or_invalid")
                interval = (next_at - settled) // 1000
                source = "last_settlement_to_recorded_next_funding_time"
            if not 3600 <= interval <= 86400:
                raise ReplayUnavailable("funding_interval_out_of_bounds")
            rows[symbol] = {
                "origin": "premium_index",
                "interval_seconds": interval,
                "interval_origin": source,
                "rate": str(quote.funding_rate),
                "observed_at": at.isoformat(),
                "available_at": timestamp(receipt["available_at"]).isoformat(),
            }
        calls.append(
            {
                "call_sha256": digest(call),
                "quote_funding": rows,
                "observed_at": receipt["available_at"],
                "available_at": bundle["completed_at"],
            }
        )
    return {"schema": bundle["schema"], "calls": calls, "selected_symbols": sorted(selected)}


async def verify_bundle(bundle):
    if (
        bundle.get("schema") not in (SCHEMA, ACQUISITION_SCHEMA, DISCOVERY_SCHEMA)
        or bundle.get("status") != "captured"
        or bundle.get("enrolled") is not False
        or bundle.get("outcome_anchor") is not None
    ):
        raise ReplayUnavailable("unsupported_or_incomplete_evidence_bundle")
    record = bundle["record"]
    end = timestamp(bundle["completed_at"])
    if not timestamp(record.started_at) <= timestamp(record.completed_at) <= end:
        raise ReplayUnavailable("invalid_bundle_bounds")
    acquisition_freshness = bundle["schema"] in (ACQUISITION_SCHEMA, DISCOVERY_SCHEMA)
    if (
        acquisition_freshness
        and (end - timestamp(record.started_at)).total_seconds() > MAX_CAPTURE_SECONDS
    ):
        raise ReplayUnavailable("capture_window_exceeds_v3_limit")
    for row in bundle["receipts"]:
        if (
            not timestamp(record.started_at)
            <= timestamp(row["started_at"])
            <= timestamp(row["available_at"])
            <= end
        ):
            raise ReplayUnavailable("invalid_http_receipt_bounds")
        if "payload" in row and row.get("payload_sha256") != digest(row["payload"]):
            raise ReplayUnavailable("http_payload_digest_mismatch")
    frames = {f["scope"]: f for f in bundle["frames"]}
    if len(frames) != len(bundle["frames"]):
        raise ReplayUnavailable("duplicate_evidence_scope")
    calls = (*record.inputs, *bundle["supplemental"])
    expected_frames = {
        f"{digest((c.method, c.arguments))}:{c.index}" for c in calls if c.method != "macro"
    }
    if (
        len(expected_frames) != sum(c.method != "macro" for c in calls)
        or set(frames) != expected_frames
    ):
        raise ReplayUnavailable("incomplete_or_duplicate_evidence_frames")
    if (
        record.request.get("enforce_quality") is not True
        or record.request.get("derivatives_enabled") is not True
    ):
        raise ReplayUnavailable("quality_policy_not_enabled")
    indices = defaultdict(list)
    for call in calls:
        indices[digest((call.method, call.arguments))].append(call.index)
    if any(sorted(group) != list(range(len(group))) for group in indices.values()):
        raise ReplayUnavailable("noncontiguous_evidence_call_indices")
    if bundle["schema"] == DISCOVERY_SCHEMA:
        from scripts.research_discovery import POLICY as DISCOVERY_POLICY

        if bundle.get("research_policy") != DISCOVERY_POLICY or len(bundle["supplemental"]) > 420:
            raise ReplayUnavailable("invalid_discovery_capture_policy")
        if sum(c.method == "derivatives_snapshot" for c in bundle["supplemental"]) > 60:
            raise ReplayUnavailable("discovery_derivative_budget_exceeded")
        cohort_ids = {i.id for i in record.cohort}
        for call in bundle["supplemental"]:
            if call.arguments.get("instrument") is None or call.arguments["instrument"].id not in cohort_ids:
                raise ReplayUnavailable("discovery_input_outside_frozen_cohort")
            if call.method == "candles" and (call.arguments["interval"], call.arguments["limit"]) in (("1h", 360), ("4h", 480), ("1w", 40)):
                continue
            if call.method != "derivatives_snapshot":
                raise ReplayUnavailable("unexpected_discovery_supplemental_method")
        selected = {e["symbol"] for e in record.decisions if e["stage"] == "selection" and e["reason"] == "selected"}
        selected.update(c.arguments["instrument"].id.symbol for c in bundle["supplemental"])
        if tuple(sorted(selected)) != tuple(bundle.get("research_selected_symbols", ())):
            raise ReplayUnavailable("discovery_selected_symbols_mismatch")
    elif any(c.method != "derivatives_snapshot" for c in bundle["supplemental"]):
        raise ReplayUnavailable("unexpected_supplemental_method")
    for call in calls:
        if call.method == "macro":
            proof = {
                "origin": "recorded_public",
                "call_sha256": digest(call),
                "observed_at": call.result.observed_at,
                "available_at": record.completed_at,
            }
            check_call(call, proof, end)
        else:
            key = f"{digest((call.method, call.arguments))}:{call.index}"
            frame = frames[key]
            if (
                any(call is c for c in record.inputs)
                and timestamp(frame["available_at"]) > record.completed_at
            ):
                raise ReplayUnavailable("original_input_not_available_at_control")
            try:
                await verify_call(
                    call,
                    frame,
                    bundle["receipts"],
                    end,
                    acquisition_freshness=acquisition_freshness,
                )
            except ReplayUnavailable as exc:
                raise ReplayUnavailable(f"{call.method}:{exc}") from exc
    # Exact original tape/source/dependency/configuration guards remain mandatory.
    await replay_scan(record)
    return record
