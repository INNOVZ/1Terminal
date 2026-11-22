"""Isolated, append-only local setup/outcome ledger; no runtime or provider dependency."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from algotrader.domain.setup_outcomes import OutcomePolicy, aware, freeze_plan

MAX_EPISODES = 10_000
MAX_EVIDENCE = 500
MAX_ASSESSMENTS = 20_000


def encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def checked(payload: str, checksum: str) -> Any:
    value = json.loads(payload)
    if digest(value) != checksum:
        raise ValueError("ledger checksum mismatch")
    return value


class SetupOutcomeLedger:
    def __init__(self, path: Path) -> None:
        if path.is_symlink():
            raise ValueError("ledger cannot be a symlink")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS setups (
                    episode_id TEXT PRIMARY KEY, payload TEXT NOT NULL, sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS imports (
                    sha256 TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence (
                    sha256 TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assessments (
                    assessment_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL,
                    policy_id TEXT NOT NULL, observed_at TEXT NOT NULL,
                    payload TEXT NOT NULL, sha256 TEXT NOT NULL,
                    FOREIGN KEY(episode_id) REFERENCES setups(episode_id));
            ''')
        path.chmod(0o600)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=5)
        try:
            con.execute("PRAGMA foreign_keys=ON")
            with con:
                yield con
        finally:
            con.close()

    def ingest(self, document: dict[str, Any], *, source_sha256: str) -> dict[str, Any]:
        if document.get("schema") != "atlas-radar-events-v2":
            raise ValueError("unsupported radar audit schema")
        events = document.get("events")
        if not isinstance(events, list) or len(events) > 1000:
            raise ValueError("invalid event count")
        accepted, rejected, ignored = [], [], 0
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            old = con.execute("SELECT payload FROM imports WHERE sha256=?", (source_sha256,)).fetchone()
            if old:
                return {**json.loads(old[0]), "duplicate_import": True}
            for index, event in enumerate(events):
                if not isinstance(event, dict):
                    rejected.append({"index": index, "reason": "invalid_event"})
                    continue
                if event.get("state") != "FRESH_COIL" or event.get("retained") is not True:
                    ignored += 1
                    continue
                try:
                    at = datetime.fromisoformat(event["at"])
                    plan = freeze_plan(event["candidate"], at)
                    identity = {key: plan[key] for key in ("symbol", "direction", "setup_label", "origin_at")}
                    episode_id = digest(identity)
                    payload = {"episode_id": episode_id, "plan": plan,
                               "scan_id": document.get("scan_id"), "source_audit_sha256": source_sha256,
                               "engine_identity": document.get("engine_identity"),
                               "screening_policy_version": document.get("screening_policy_version"),
                               "candidate": event["candidate"]}
                    row = con.execute("SELECT payload,sha256 FROM setups WHERE episode_id=?", (episode_id,)).fetchone()
                    if row:
                        original = checked(row[0], row[1])
                        # A later event cannot rewrite the origin, original score or bracket.
                        if original['plan'] != plan:
                            raise ValueError("immutable_episode_plan_conflict")
                        accepted.append(episode_id)
                        continue
                    if con.execute("SELECT COUNT(*) FROM setups").fetchone()[0] >= MAX_EPISODES:
                        raise ValueError("ledger_episode_capacity_reached")
                    con.execute("INSERT INTO setups VALUES(?,?,?)", (episode_id, encode(payload), digest(payload)))
                    accepted.append(episode_id)
                except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
                    rejected.append({"index": index, "reason": str(exc)[:200]})
            result = {"accepted_episode_ids": accepted, "rejected": rejected,
                      "ignored_nonfresh_events": ignored, "duplicate_import": False}
            con.execute("INSERT INTO imports VALUES(?,?)", (source_sha256, encode(result)))
        return result

    def setups(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            return [checked(p, h) for p, h in con.execute("SELECT payload,sha256 FROM setups ORDER BY episode_id")]

    def assess(self, episode_id: str, policy: OutcomePolicy, outcome: dict[str, Any],
               *, evidence: dict[str, Any], observed_at: datetime) -> str:
        observed_at = aware(observed_at)
        statuses = {'not_due', 'missing', 'invalid', 'unavailable', 'ambiguous',
                    'untriggered_expired', 'invalidated_before_entry', 'triggered_unresolved',
                    'target_before_stop', 'stop_before_target'}
        if (outcome.get('measurement_version') != policy.version
                or outcome.get('status') not in statuses
                or outcome.get('probability_calibration_permitted') is not False
                or outcome.get('is_executed_trade') is not False):
            raise ValueError('incompatible outcome contract')
        if outcome.get('status') != 'not_due' and observed_at < aware(outcome['horizon_end']):
            raise ValueError('outcome observed before horizon maturity')
        if ('cost_assumption_bps' in outcome
                and Decimal(str(outcome['cost_assumption_bps'])) != policy.round_trip_cost_bps):
            raise ValueError('outcome cost policy mismatch')
        evidence_sha, policy_id = digest(evidence), digest(policy.as_dict())
        payload = {"episode_id": episode_id, "policy": policy.as_dict(), "policy_id": policy_id,
                   "outcome": outcome, "evidence_sha256": evidence_sha}
        assessment_id = digest(payload)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT payload,sha256 FROM setups WHERE episode_id=?", (episode_id,)).fetchone()
            if row is None:
                raise ValueError("unknown episode")
            setup = checked(row[0], row[1])
            from algotrader.domain.forward_outcomes import outcome_anchor
            anchor = outcome_anchor(aware(setup['plan']['first_visible_at']))
            if (aware(outcome['anchor']) != anchor
                    or aware(outcome['horizon_end']) != anchor + timedelta(hours=policy.horizon_hours)):
                raise ValueError('outcome horizon policy mismatch')
            if (not con.execute("SELECT 1 FROM evidence WHERE sha256=?", (evidence_sha,)).fetchone()
                    and con.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] >= MAX_EVIDENCE):
                raise ValueError("ledger_evidence_capacity_reached")
            if (not con.execute("SELECT 1 FROM assessments WHERE assessment_id=?", (assessment_id,)).fetchone()
                    and con.execute("SELECT COUNT(*) FROM assessments").fetchone()[0] >= MAX_ASSESSMENTS):
                raise ValueError("ledger_assessment_capacity_reached")
            con.execute("INSERT OR IGNORE INTO evidence VALUES(?,?)", (evidence_sha, encode(evidence)))
            con.execute("INSERT OR IGNORE INTO assessments VALUES(?,?,?,?,?,?)",
                        (assessment_id, episode_id, policy_id, observed_at.isoformat(), encode(payload), digest(payload)))
        return assessment_id

    def report(self) -> dict[str, Any]:
        groups: dict[str, Any] = {}
        with self.connect() as con:
            assessments = list(con.execute("SELECT payload,sha256,observed_at FROM assessments ORDER BY observed_at"))
            by_episode: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            verified_evidence: set[str] = set()
            for p, h, _ in assessments:
                value = checked(p, h)
                if value['evidence_sha256'] not in verified_evidence:
                    evidence = con.execute("SELECT payload FROM evidence WHERE sha256=?", (value['evidence_sha256'],)).fetchone()
                    if evidence is None:
                        raise ValueError("missing ledger evidence")
                    checked(evidence[0], value['evidence_sha256'])
                    verified_evidence.add(value['evidence_sha256'])
                by_episode[(value['episode_id'], value['policy_id'])].append(value)
            by_setup: dict[str, list[Any]] = defaultdict(list)
            for episode_policy_key, values in by_episode.items():
                by_setup[episode_policy_key[0]].append((episode_policy_key, values))
            for setup in self.setups():
                matches = by_setup[setup['episode_id']]
                for (_, policy_id), attempts in matches or [((setup['episode_id'], 'none'), [])]:
                    # Never replace a mature, interpretable assessment with a later failed fetch.
                    complete = [a for a in attempts if a['outcome']['status'] not in
                                ('not_due', 'missing', 'invalid', 'unavailable')]
                    latest = (complete or attempts)[-1] if attempts else None
                    p = setup['plan']
                    key = encode([setup['engine_identity'], setup['screening_policy_version'],
                                  p['direction'], p['setup_label'], policy_id])
                    group = groups.setdefault(key, {'engine_identity': setup['engine_identity'],
                        'screening_policy_version': setup['screening_policy_version'], 'direction': p['direction'],
                        'archetype': p['setup_label'], 'outcome_policy_id': policy_id, 'counts': Counter(), 'net_positive_resolved': 0, 'episodes': []})
                    status = latest['outcome']['status'] if latest else 'awaiting_price_evidence'
                    group['counts'][status] += 1
                    if status in ('target_before_stop', 'stop_before_target') and latest is not None:
                        group['net_positive_resolved'] += Decimal(latest['outcome']['net_r']) > 0
                    group['episodes'].append({'episode_id': setup['episode_id'], 'symbol': p['symbol'], 'status': status})
        for group in groups.values():
            counts = group['counts']
            wins, losses = counts['target_before_stop'], counts['stop_before_target']
            group['resolved_barrier_positive_rate'] = wins / (wins + losses) if wins + losses else None
            group['resolved_barrier_denominator'] = wins + losses
            group['resolved_model_net_positive_rate'] = group['net_positive_resolved'] / (wins + losses) if wins + losses else None
            group['rate_semantics'] = 'resolved minute-barrier simulations only; excludes all unresolved/ambiguous/untriggered cases; not executed win rate'
        return {'schema': 'atlas-setup-outcome-ledger-report-v1', 'groups': list(groups.values()),
                'episode_count': len(self.setups()),
                'episode_policy_assessment_count': sum(sum(g['counts'].values()) for g in groups.values()),
                'calibrated_probabilities': False, 'performance_claims_permitted': False,
                'network_requests': 0, 'production_publication_affected': False}
