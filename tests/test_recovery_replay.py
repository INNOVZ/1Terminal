from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from algotrader.application.scan_replay import ReplayMismatch
from scripts.replay_contract import ReplayUnavailable
from scripts.research_evidence_contract import verify_bundle, verify_bundle_inputs
from scripts.research_recovery_replay import compare
from tests.test_research_discovery import discovery_bundle


async def test_counterfactual_checks_receipts_and_preserves_strict_source_guard():
    bundle = await discovery_bundle()
    record = bundle["record"]
    bundle["record"] = replace(record, identity={**record.identity, "source_hash": "older-source"})
    assert await verify_bundle_inputs(bundle) == bundle["record"]
    with pytest.raises(ReplayMismatch, match="identity mismatch"):
        await verify_bundle(bundle)
    result = await compare(bundle)
    assert result["mode"] == "counterfactual_current_source_pair"
    assert result["performance_claims_permitted"] is False
    assert result["source_identity"]["source_hash"] != "older-source"
    assert result["core"]["counts"]["selected"] == result["recovery"]["counts"]["selected"]


async def test_counterfactual_rejects_modified_receipt_and_wrong_study_binding():
    bundle = await discovery_bundle()
    corrupted = copy.deepcopy(bundle)
    corrupted["receipts"][0]["payload"] = {"changed": True}
    with pytest.raises(ReplayUnavailable, match="digest_mismatch"):
        await compare(corrupted)
    with pytest.raises(ReplayUnavailable, match="capture_study_binding_changed"):
        await compare(bundle, {"scan_id": "other-study", "bundle_sha256": "wrong"})


async def test_counterfactual_rejects_source_change_during_comparison(monkeypatch):
    bundle = await discovery_bundle()
    identities = iter(({"source": "before"}, {"source": "after"}))
    monkeypatch.setattr("scripts.research_recovery_replay.replay_identity", lambda: next(identities))
    with pytest.raises(ReplayUnavailable, match="source_changed_during_run"):
        await compare(bundle)
