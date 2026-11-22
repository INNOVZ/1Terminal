import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from scripts.research_setup_backfill import PublicHistory
from scripts.research_setup_backfill_verify import rederive_minutes
from tests.test_setup_backfill import BASE, LIMITS, row


def evidence(tmp_path, monkeypatch):
    monkeypatch.setattr('scripts.research_setup_backfill.time.sleep', lambda _: None)
    start = datetime.fromtimestamp(BASE/1000, UTC)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[row(), row(BASE+60000)]))) as client:
        return PublicHistory(tmp_path, LIMITS, client).history('TESTUSDT', start, start+timedelta(minutes=2))


def test_raw_receipts_rederive_minutes_and_reject_normalized_only_changes(tmp_path, monkeypatch):
    doc = evidence(tmp_path, monkeypatch)
    assert len(rederive_minutes(doc)) == 2
    doc['candles'][0]['close'] = '100.5'
    with pytest.raises(ValueError, match='differs from raw'):
        rederive_minutes(doc)


def test_wrong_historical_window_or_mutated_http_body_cannot_attest(tmp_path, monkeypatch):
    doc = evidence(tmp_path, monkeypatch)
    wrong = {**doc, 'symbol':'OTHERUSDT'}
    with pytest.raises(ValueError, match='binding mismatch'):
        rederive_minutes(wrong)
    Path(doc['raw_http_receipts'][0]['path']).write_text(json.dumps([]))
    with pytest.raises(ValueError, match='binding mismatch'):
        rederive_minutes(doc)
