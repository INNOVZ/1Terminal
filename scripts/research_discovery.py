"""Versioned discovery challenger, used exclusively by public-data research CLIs."""
from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

from algotrader.domain.discovery_features import (
    DiscoveryFeatureUnavailable,
    discovery_volume_metrics,
    discovery_watch_selection,
)
from scripts.replay_contract import ReplayUnavailable
from scripts.research_pipeline_comparison import FundingCarryScreener

POLICY = {
    "version": "discovery-v2-full336-rvol4h-watch20",
    "feature_contract": "hourly360-full336-rvol4h-inclusive20-v2",
    "hourly_history_limit": 360,
    "volume_history_closed_hours": 336,
    "watch_limit": 20,
    "watch_min_quote_volume": "500000",
    "watch_max_quote_volume_exclusive": "1500000",
    "watch_max_spread_bps": "25",
    "watch_order": "sha256(version|four_hour_utc_bucket|symbol)",
    "readiness": "existing_L1_to_L5_pass; activation_and_publication_recorded_separately",
    "emerging": "positive_6h_BTC_relative_return_and_turnover_acceleration_gt1_and_ret24h_le9pct",
    "production_publication_gates": "unchanged",
}


class DiscoveryScreener(FundingCarryScreener):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.watch_symbols = set()
        self.gate_evidence = {}
        self.feature_evidence = {}
        self.unavailable_symbols = {}

    def _extend_selection(self, selected, liquid, quotes, max_symbols):
        if self._evaluated_at is None or max_symbols is None:
            raise ValueError("discovery research requires frozen time and explicit budget")
        result = discovery_watch_selection(selected, liquid, quotes, max_symbols, self._evaluated_at)
        prior = {i.id.symbol for i in selected}
        self.watch_symbols = {i.id.symbol for i in result} - prior
        return result

    def _hourly_history_limit(self):
        return POLICY["hourly_history_limit"]

    def _accumulation_volume_metrics(self, hourly, four_hour, hourly_snapshot):
        return discovery_volume_metrics(hourly, four_hour)

    def _note(self, symbol, stage, reason, **details):
        super()._note(symbol, stage, reason, **details)
        if reason == "feature_contract_unavailable":
            self.unavailable_symbols[symbol] = str(details["detail"])
        if stage in ("accumulation_gates", "accumulation_rejection"):
            self.gate_evidence[symbol] = {"stage": stage, "reason": reason, **details}

    async def _evaluate_single(self, *args, **kwargs):
        symbol = kwargs["inst"].id.symbol
        try:
            result = await super()._evaluate_single(*args, **kwargs)
        except DiscoveryFeatureUnavailable as exc:
            self.unavailable_symbols[symbol] = str(exc)
            self._note(symbol, "accumulation_rejection", "feature_contract_unavailable", detail=str(exc))
            result = []
        cached = kwargs.get("preloaded_candles") or (kwargs.get("candle_cache") or {}).get(symbol)
        if cached:
            hourly = sorted((c for c in cached[0] if c.closed), key=lambda c: c.closed_at)
            btc = sorted((c for c in kwargs["btc_candles"] if c.closed), key=lambda c: c.closed_at)
            self.feature_evidence[symbol] = temporal_features(hourly, btc)
        return result

    def observations(self):
        rows = []
        for symbol in sorted(self.gate_evidence.keys() | self.feature_evidence.keys()):
            evidence = self.gate_evidence.get(symbol, {})
            gates = evidence.get("gates", {})
            ready = all(gates.get(f"L{i}") is True for i in range(1, 6))
            enriched = evidence.get("enriched") is True
            state = "structural_rejection"
            if symbol in self.unavailable_symbols:
                state = "feature_unavailable"
            if ready:
                state = "readiness_activation_unverified"
                if enriched:
                    state = "activated_gates_passed" if gates.get("L6") else "ready_waiting_activation"
            rows.append({"symbol": symbol, "state": state,
                         "watch_selected": symbol in self.watch_symbols,
                         "evidence": json.loads(json.dumps(evidence, default=str, allow_nan=False)),
                         "temporal_features": self.feature_evidence.get(symbol, {"available": False}),
                         "actionable": False})
        return tuple(rows)


def temporal_features(hourly, btc):
    if len(hourly) < 24 or len(btc) < 6 or hourly[-1].closed_at != btc[-1].closed_at:
        return {"available": False, "reason": "unaligned_or_missing_closed_history"}
    if any(b.opened_at - a.opened_at != timedelta(hours=1)
           for sequence in (hourly[-24:], btc[-6:])
           for a, b in zip(sequence, sequence[1:], strict=False)):
        return {"available": False, "reason": "noncontiguous_closed_history"}
    recent = sum((c.quote_volume for c in hourly[-6:]), Decimal("0")) / 6
    previous = sum((c.quote_volume for c in hourly[-24:-6]), Decimal("0")) / 18
    if previous <= 0 or min(hourly[-6].open, hourly[-24].open, btc[-6].open) <= 0:
        return {"available": False, "reason": "nonpositive_return_or_turnover_baseline"}
    ret6 = (hourly[-1].close / hourly[-6].open - 1) * 100
    ret24 = (hourly[-1].close / hourly[-24].open - 1) * 100
    btc6 = (btc[-1].close / btc[-6].open - 1) * 100
    return {"available": True, "last_closed_at": hourly[-1].closed_at.isoformat(),
            "return_6h_pct": str(ret6), "return_24h_pct": str(ret24),
            "btc_relative_6h_pp": str(ret6 - btc6),
            "turnover_acceleration_6h_vs_prior18h": str(recent / previous),
            "emerging": ret6 - btc6 > 0 and recent / previous > 1 and ret24 <= 9}


def validate_history_overlap(record, supplemental):
    """Corrected history must share the control's closed-bar endpoint and overlap."""
    original = {c.arguments["instrument"].id.symbol: tuple(x for x in c.result if x.closed)
                for c in record.inputs if c.method == "candles" and not c.error
                and c.arguments["interval"] == "1h" and c.arguments["limit"] == 240}
    for call in supplemental:
        if call.method != "candles" or call.error or call.arguments["interval"] != "1h":
            continue
        symbol = call.arguments["instrument"].id.symbol
        if symbol not in original:
            continue
        closed = tuple(x for x in call.result if x.closed)
        if any(c.closed_at > record.started_at for c in closed):
            raise ReplayUnavailable("discovery_closed_bar_after_control_cutoff")
        expected = original[symbol]
        if closed[-len(expected):] != expected:
            raise ReplayUnavailable("discovery_history_control_overlap_changed")


async def run_discovery(record, supplemental):
    from algotrader.application.scan_replay import ReplayMarket
    from algotrader.config import Settings
    from algotrader.domain.scan_observations import ScanDecisionJournal
    from scripts.research_pipeline_comparison import AuditedTape

    validate_history_overlap(record, supplemental)
    tape = AuditedTape((*record.inputs, *supplemental))
    market = ReplayMarket(tape)
    journal = ScanDecisionJournal()
    engine = DiscoveryScreener(Settings.model_construct(**record.identity["configuration"]), market,
                               evaluated_at=record.started_at, journal=journal)
    request = dict(record.request)
    enabled = request.pop("derivatives_enabled")
    opportunities = await engine.screen(market, derivatives_port=market if enabled else None, **request)
    if tape.missing or engine.scan_counts["failed"]:
        return {"status": "unavailable", "reason": "discovery_inputs_incomplete",
                "candidate_results": None, "missing_input_requested": tape.missing}
    return {"status": "complete", "policy": dict(POLICY), "candidate_results": {
        "opportunities": tuple(o.as_dict() for o in opportunities),
        "counts": dict(engine.scan_counts), "decisions": journal.snapshot(),
        "cohort_symbols": tuple(i.id.symbol for i in journal.cohort),
        "feature_unavailable_symbols": dict(sorted(engine.unavailable_symbols.items())),
        "watch_symbols": tuple(sorted(engine.watch_symbols)),
        "discovery_observations": engine.observations(),
    }}
