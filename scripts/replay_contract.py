"""Strict research replay admission. Does not alter screener eligibility or old archives.

Provenance sidecars are operator-supplied attestations, not reconstructed facts or signatures.
Never manufacture one from a legacy tape whose arrival/coverage metadata was not captured.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from algotrader.adapters.external.models import MacroSentimentContext
from algotrader.application.scan_replay import ReplayReport, replay_scan
from algotrader.domain.models import (
    Candle,
    DerivativesSnapshot,
    Instrument,
    MarketKind,
    Quote,
    Venue,
)
from algotrader.domain.scan_observations import RecordedCall, ScanRecord
from algotrader.infrastructure.scan_archive import canonical_bytes

SCHEMA = "atlas-verified-replay-v1"


class ReplayUnavailable(ValueError):
    """Insufficient evidence; never count as a negative forecast."""


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def timestamp(value: Any) -> datetime:
    try:
        result = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.astimezone(UTC)
    except (TypeError, ValueError) as exc:
        raise ReplayUnavailable("invalid_aware_timestamp") from exc


def finite(value: Any, *, positive: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or (positive and value <= 0):
        raise ReplayUnavailable("missing_or_invalid_numeric_input")


def fresh(value: Any, decision: datetime, ttl: int) -> datetime:
    at = timestamp(value)
    if not 0 <= (decision - at).total_seconds() <= ttl:
        raise ReplayUnavailable("future_or_stale_input")
    return at


def _candles(
    call: RecordedCall, observed: datetime, *, minimums: dict[str, int] | None = None
) -> None:
    interval = call.arguments.get("interval")
    specifications = {"1h": (3600, 240), "4h": (14400, 480), "1w": (604800, 15)}
    if interval not in specifications:
        raise ReplayUnavailable("unsupported_candle_interval")
    seconds, minimum = specifications[interval]
    if minimums is not None:
        minimum = minimums[interval]
    bars = call.result
    if not isinstance(bars, (tuple, list)) or len(bars) < minimum:
        raise ReplayUnavailable("incomplete_warmup")
    previous = None
    for bar in bars:
        if not isinstance(bar, Candle) or not bar.closed:
            raise ReplayUnavailable("unclosed_or_invalid_candle")
        start, end = timestamp(bar.opened_at), timestamp(bar.closed_at)
        if end > observed or abs((end - start).total_seconds() - seconds) > 1:
            raise ReplayUnavailable("future_or_invalid_candle_duration")
        if previous is not None and start - previous != timedelta(seconds=seconds):
            raise ReplayUnavailable("duplicate_or_gapped_candles")
        previous = start
        for field in (bar.open, bar.high, bar.low, bar.close):
            finite(field, positive=True)
        if not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
            raise ReplayUnavailable("invalid_ohlc")
        for volume in (
            bar.volume,
            bar.quote_volume,
            bar.taker_buy_volume,
            bar.taker_buy_quote_volume,
        ):
            finite(volume)
            if volume < 0:
                raise ReplayUnavailable("invalid_volume")
        if bar.taker_buy_volume > bar.volume or bar.taker_buy_quote_volume > bar.quote_volume:
            raise ReplayUnavailable("invalid_taker_volume")
    anchor = (
        datetime(1970, 1, 5, tzinfo=UTC) if interval == "1w" else datetime(1970, 1, 1, tzinfo=UTC)
    )
    expected = anchor + timedelta(
        seconds=int((observed - anchor).total_seconds() // seconds) * seconds
    )
    if abs((timestamp(bars[-1].closed_at) - expected).total_seconds()) > 1:
        raise ReplayUnavailable("stale_candle_window")


def check_call(call: RecordedCall, proof: dict[str, Any], decision: datetime) -> None:
    decision = timestamp(decision)
    if proof.get("origin") != "recorded_public" or call.error is not None:
        raise ReplayUnavailable("unverified_or_failed_provider_input")
    if proof.get("call_sha256") != digest(call):
        raise ReplayUnavailable("input_provenance_digest_mismatch")
    observed, available = timestamp(proof.get("observed_at")), timestamp(proof.get("available_at"))
    if not observed <= available <= decision:
        raise ReplayUnavailable("input_not_available_at_decision")
    value = call.result
    if call.method == "instruments":
        fresh(observed, decision, 3600)
        if not isinstance(value, (tuple, list)) or any(
            not isinstance(x, Instrument) for x in value
        ):
            raise ReplayUnavailable("invalid_universe")
        if len({x.id for x in value}) != len(value):
            raise ReplayUnavailable("duplicate_instruments")
        if any(
            x.id.venue is not Venue.BINANCE_USDM or x.market_kind is not MarketKind.FUTURES
            for x in value
        ):
            raise ReplayUnavailable("unexpected_market_universe")
    elif call.method == "quotes":
        fresh(observed, decision, 60)
        if not isinstance(value, (tuple, list)) or any(not isinstance(x, Quote) for x in value):
            raise ReplayUnavailable("invalid_quotes")
        expected = {x.id for x in call.arguments.get("instruments", ())}
        if (
            len({q.instrument_id for q in value}) != len(value)
            or {q.instrument_id for q in value} != expected
        ):
            raise ReplayUnavailable("incomplete_quote_universe")
        for quote in value:
            fresh(quote.observed_at, observed, 60)
            for number in (quote.bid, quote.ask, quote.last):
                finite(number, positive=True)
            if quote.bid > quote.ask:
                raise ReplayUnavailable("crossed_quote")
            for number in (quote.quote_volume_24h, quote.funding_rate, quote.basis_pct):
                finite(number)
            if quote.quote_volume_24h < 0:
                raise ReplayUnavailable("invalid_quote_volume")
    elif call.method == "candles":
        fresh(observed, decision, 60)
        _candles(call, observed)
    elif call.method == "derivatives_snapshot":
        fresh(observed, decision, 60)
        if not isinstance(value, DerivativesSnapshot) or value.degraded_telemetry:
            raise ReplayUnavailable("missing_or_degraded_derivatives")
        fresh(value.observed_at, observed, 60)
        for number in (
            value.funding_rate,
            value.basis_rate,
            value.open_interest_change_1h,
            value.open_interest_change_4h,
            value.open_interest_change_24h,
            value.open_interest_change_7d,
        ):
            finite(number)
        finite(value.open_interest, positive=True)
        for number in (
            value.taker_buy_sell_ratio,
            value.taker_buy_sell_ratio_4h,
            value.taker_buy_sell_ratio_24h,
        ):
            finite(number, positive=True)
        for number in (value.bid_depth_notional, value.ask_depth_notional):
            finite(number)
            if number < 0:
                raise ReplayUnavailable("invalid_depth")
        funding = proof.get("funding", {})
        interval = funding.get("interval_seconds")
        if type(interval) is not int or not 3600 <= interval <= 86400:
            raise ReplayUnavailable("unknown_funding_interval")
        fresh(funding.get("settled_at"), observed, interval + 60)
        try:
            if Decimal(funding["rate"]) != value.funding_rate:
                raise ReplayUnavailable("funding_value_mismatch")
        except (KeyError, TypeError, ArithmeticError) as exc:
            raise ReplayUnavailable("funding_value_unverified") from exc
        windows = proof.get("oi_windows", {})
        latest_pair = None
        for hours in (1, 4, 24, 168):
            window = windows.get(str(hours), {})
            latest = fresh(window.get("latest_at"), observed, 3660)
            baseline = timestamp(window.get("baseline_at"))
            if latest - baseline != timedelta(hours=hours):
                raise ReplayUnavailable("incomplete_oi_horizon")
            try:
                current_quantity = Decimal(window["latest_quantity"])
                baseline_quantity = Decimal(window["baseline_quantity"])
                finite(current_quantity, positive=True)
                finite(baseline_quantity, positive=True)
            except (KeyError, TypeError, ArithmeticError) as exc:
                raise ReplayUnavailable("oi_quantities_unverified") from exc
            pair = (latest, current_quantity)
            if latest_pair is not None and pair != latest_pair:
                raise ReplayUnavailable("inconsistent_oi_reference")
            latest_pair = pair
            expected_delta = (current_quantity - baseline_quantity) / baseline_quantity
            fields = {
                1: "open_interest_change_1h",
                4: "open_interest_change_4h",
                24: "open_interest_change_24h",
                168: "open_interest_change_7d",
            }
            if current_quantity != value.open_interest or expected_delta != getattr(
                value, fields[hours]
            ):
                raise ReplayUnavailable("oi_calculation_mismatch")
    elif call.method == "macro":
        fresh(observed, decision, 3600)
        if not isinstance(value, MacroSentimentContext):
            raise ReplayUnavailable("invalid_macro")
        fresh(value.observed_at, observed, 3600)
        for component, ttl in (
            (value.fear_greed, 86460),
            (value.stablecoin_supply, 86460),
            (value.market_breadth, 3660),
        ):
            if component is None:
                raise ReplayUnavailable("missing_macro_component")
            fresh(component.observed_at, observed, ttl)
        if type(value.fear_greed.value) is not int or not 0 <= value.fear_greed.value <= 100:
            raise ReplayUnavailable("invalid_fear_greed")
        for number in (
            value.stablecoin_supply.total_circulating_usd,
            value.stablecoin_supply.change_1d_pct,
            value.stablecoin_supply.change_7d_pct,
            value.stablecoin_supply.change_30d_pct,
            value.market_breadth.btc_dominance_pct,
            value.market_breadth.btc_dominance_24h_delta,
            value.market_breadth.total_market_cap_usd,
            value.market_breadth.total_volume_24h_usd,
        ):
            finite(number)
    else:
        raise ReplayUnavailable("unsupported_recorded_method")


async def verify_record(record: ScanRecord, manifest: dict[str, Any]) -> ReplayReport:
    if not isinstance(manifest, dict):
        raise ReplayUnavailable("invalid_manifest")
    if (
        manifest.get("schema") != SCHEMA
        or manifest.get("scan_id") != record.scan_id
        or manifest.get("record_sha256") != digest(record)
    ):
        raise ReplayUnavailable("replay_manifest_mismatch")
    if (
        manifest.get("universe_scope") != "full_provider_response"
        or manifest.get("selection_applied") is not False
    ):
        raise ReplayUnavailable("historical_universe_unverified")
    if (
        record.request.get("enforce_quality") is not True
        or record.request.get("derivatives_enabled") is not True
    ):
        raise ReplayUnavailable("quality_policy_not_enabled")
    proofs = manifest.get("calls")
    if not isinstance(proofs, list) or len(proofs) != len(record.inputs):
        raise ReplayUnavailable("incomplete_input_provenance")
    if any(not isinstance(p, dict) or not isinstance(p.get("call_sha256"), str) for p in proofs):
        raise ReplayUnavailable("invalid_input_provenance")
    if len({p.get("call_sha256") for p in proofs}) != len(proofs):
        raise ReplayUnavailable("duplicate_input_provenance")
    proof_by_digest = {p.get("call_sha256"): p for p in proofs}
    for call in record.inputs:
        proof = proof_by_digest.get(digest(call))
        if proof is None:
            raise ReplayUnavailable("missing_input_provenance")
        check_call(call, proof, record.completed_at)
    # The existing implementation enforces source/config/dependency identity, consumes the exact
    # input tape through the actual production screen(), and compares outputs AND decision journal.
    return await replay_scan(record)


def reconstruction_check(raw: dict[str, Any]) -> dict[str, Any]:
    """Assess the supplied v1 reconstruction; never invent strict provenance for it."""
    from scripts.diagnostics.legacy_faithful_48h import _deserialize_candle

    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("metadata"), dict)
        or raw["metadata"].get("schema") != "faithful-point-in-time-replay-v1"
    ):
        raise ReplayUnavailable("unsupported_reconstruction_schema")

    symbols = ("HOTUSDT", "ICNTUSDT", "BMTUSDT", "MEGAUSDT", "TRUSTUSDT", "BEATUSDT")
    anchor = timestamp(raw.get("metadata", {}).get("anchor_end_utc"))
    if anchor != datetime(2026, 11, 3, 11, 59, 59, 999000, tzinfo=UTC):
        raise ReplayUnavailable("unsupported_reconstruction_anchor")
    checks = []
    for hours in (44, 40, 36, 32, 28, 24, 20, 16, 12, 8, 4, 0):
        signal = anchor - timedelta(hours=hours)
        decision = signal + timedelta(seconds=60)
        for symbol in symbols:
            errors = []
            for source in (symbol, "BTCUSDT"):
                for interval in ("1h",) if source == "BTCUSDT" else ("1h", "4h", "1w"):
                    try:
                        bars = [
                            _deserialize_candle(c)
                            for c in raw["symbols"][source]["candles_" + interval]
                            if timestamp(c["closed_at"]) <= signal
                        ]
                        _candles(RecordedCall("candles", 0, {"interval": interval}, bars), decision)
                    except (KeyError, TypeError, ValueError, ArithmeticError):
                        errors.append(source + ":" + interval + ":invalid_candle_window")
            try:
                funding_at(raw["symbols"][symbol], decision)
            except (KeyError, TypeError, ValueError, ArithmeticError):
                errors.append("missing_stale_or_invalid_funding")
            try:
                rows = raw["symbols"][symbol]["open_interest_hist_1h"]
                samples = {}
                for row in rows:
                    ms = row["timestamp"]
                    if type(ms) is not int or ms in samples:
                        raise ReplayUnavailable("duplicate_or_invalid_oi")
                    quantity = Decimal(row["sumOpenInterest"])
                    finite(quantity, positive=True)
                    samples[ms] = quantity
                current = int((signal + timedelta(milliseconds=1)).timestamp() * 1000)
                if any(current - h * 3600000 not in samples for h in (0, 1, 4, 24, 168)):
                    raise ReplayUnavailable("missing_oi_horizon")
            except (KeyError, TypeError, ValueError, ArithmeticError):
                errors.append("missing_or_invalid_oi_horizon")
            checks.append(
                {
                    "symbol": symbol,
                    "signal_cutoff": signal.isoformat(),
                    "decision_at": decision.isoformat(),
                    "data_errors": sorted(set(errors)),
                    "strict_status": "unavailable",
                }
            )
    return {
        "schema": SCHEMA,
        "strict_status": "unavailable",
        "total_observations": len(checks),
        "strict_eligible_observations": 0,
        "negative_forecasts": 0,
        "strict_unavailable_reasons": [
            "historical_universe_not_recorded",
            "synthetic_spread",
            "neutral_macro",
            "unknown_basis_and_depth",
            "arrival_provenance_not_recorded",
            "original_source_and_configuration_not_frozen",
        ],
        "observations": checks,
    }


def funding_at(data: dict[str, Any], decision: datetime) -> Decimal:
    """Diagnostic-only interval inference; strict records require captured interval metadata."""
    rows = data["funding_rate_hist"]
    if len({r["fundingTime"] for r in rows}) != len(rows):
        raise ReplayUnavailable("duplicate_funding")
    valid = sorted(
        (
            r
            for r in rows
            if timestamp(datetime.fromtimestamp(r["fundingTime"] / 1000, UTC)) <= decision
        ),
        key=lambda r: r["fundingTime"],
    )
    if len(valid) < 2:
        raise ReplayUnavailable("funding_interval_unavailable")
    interval = round((valid[-1]["fundingTime"] - valid[-2]["fundingTime"]) / 1000)
    if not 3600 <= interval <= 86400:
        raise ReplayUnavailable("invalid_funding_interval")
    fresh(datetime.fromtimestamp(valid[-1]["fundingTime"] / 1000, UTC), decision, interval + 60)
    rate = Decimal(valid[-1]["fundingRate"])
    finite(rate)
    return rate
