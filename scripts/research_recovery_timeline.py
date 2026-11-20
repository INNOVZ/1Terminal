"""Offline deterministic replay of every persisted recovery episode, including failures."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

from algotrader.application.scan_recording import screening_identity
from algotrader.domain.recovery_episodes import EPISODE_POLICY, RecoveryEpisode, advance_episode
from algotrader.infrastructure.recovery_episodes import read_recovery_snapshot, serialize
from scripts.research_verified_replay import offline, write_report


def replay(episodes, records):
    states = {}
    gaps = Counter()
    errors = Counter()
    for record in records:
        saved = RecoveryEpisode(**record["state"])
        evidence = record["evidence"]
        key = saved.episode_id
        if evidence["kind"] == "enrollment":
            if key in states or saved.revision != 0 or saved.triggered_at is not None:
                raise ValueError("duplicate or invalid episode enrollment")
            candidate = evidence["candidate"]
            if (candidate["setup_label"] != "RECOVERY_RECLAIM_LONG"
                    or candidate["symbol"] != saved.instrument.id.symbol
                    or not all(item["passed"] is True for item in candidate["checklist"])
                    or len(candidate["checklist"]) != 5):
                raise ValueError("unqualified enrollment evidence")
            states[key] = saved
            continue
        if evidence["kind"] != "refresh" or key not in states:
            raise ValueError("refresh without original enrollment")
        previous = states[key]
        try:
            expected = advance_episode(previous, at=evidence["at"], bars=evidence["bars"],
                                       quote=evidence["quote"], derivatives=evidence["derivatives"])
        except ValueError as exc:
            if evidence.get("validation_error") != str(exc):
                raise ValueError("observation validation mismatch") from exc
            expected = replace(previous, checked_at=evidence["at"], revision=previous.revision + 1,
                               reason="invalid_observation", coverage_complete=False)
        if expected != saved or saved.revision != previous.revision + 1:
            raise ValueError("episode transition replay mismatch")
        if not saved.coverage_complete:
            gaps[key] += 1
        errors[key] += len(evidence["errors"])
        states[key] = saved
    if states != {episode.episode_id: episode for episode in episodes}:
        raise ValueError("stored state differs from replayed observations")
    outcomes = []
    for episode in sorted(states.values(), key=lambda e: (e.first_seen, e.episode_id)):
        reference = episode.trigger_price
        endpoint = episode.last_price
        outcomes.append({"episode_id": episode.episode_id, "symbol": episode.instrument.id.symbol,
                         "first_seen": episode.first_seen.isoformat(),
                         "triggered_at": episode.triggered_at.isoformat() if episode.triggered_at else None,
                         "status": episode.status, "reason": episode.reason,
                         "coverage_complete": episode.coverage_complete,
                         "gap_observations": gaps[episode.episode_id],
                         "failed_requests": errors[episode.episode_id],
                         "endpoint_observed_at": episode.last_price_at.isoformat() if episode.last_price_at else None,
                         "sampled_return_pct": float((endpoint / reference - 1) * 100) if endpoint and reference else None,
                         "observed_peak_pct": float((episode.peak / reference - 1) * 100) if episode.peak and reference else None,
                         "observed_min_pct": float((episode.trough / reference - 1) * 100) if episode.trough and reference else None})
    triggered = [state for state in states.values() if state.triggered_at]
    return {"policy": EPISODE_POLICY, "episodes": len(states), "observations": len(records),
            "statuses": dict(Counter(state.status for state in states.values())),
            "triggered": len(triggered),
            "confirmed_then_invalidated": sum(state.status == "invalidated" for state in triggered),
            "outcomes": outcomes, "performance_claims_permitted": False,
            "limitations": ["confirmation observations and planned levels do not establish executed fills",
                            "overlapping or double-touch bars have unknown event ordering",
                            "terminal-bar extremes do not establish pre-exit excursions",
                            "intrabar extrema in the enrollment interval are excluded",
                            "lead time to top gainers needs independently matched rank observations",
                            "expired observations can have incomplete terminal outcomes"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    with offline():
        episodes, records = read_recovery_snapshot(args.database)
        result = replay(episodes, records)
        result["evidence_digest"] = hashlib.sha256(serialize(records).encode()).hexdigest()
        result["source_identity"] = screening_identity()
    write_report(args.output, result)
    print(json.dumps({key: result[key] for key in ("episodes", "triggered", "statuses")}))


if __name__ == "__main__":
    main()
