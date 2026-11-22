from datetime import UTC, datetime, timedelta

import httpx
import pytest

from scripts.research_setup_backfill import PublicHistory, validate_rows

BASE = 1794759420000
LIMITS = {'max_requests': 3, 'max_seconds': 60, 'max_bytes': 1_000_000,
          'per_response_bytes': 512 * 1024}


def row(at=BASE):
    return [at, '100', '102', '99', '101', '20', at + 59999, '2000', 4, '10', '1000', '0']


def test_exact_historical_bounds_and_no_latest_substitution():
    assert validate_rows([row()], BASE, BASE + 60000, 1000)
    for values in ([row(BASE - 60000)], [row(BASE + 60000)], [row(), row()], [row(BASE+1)]):
        with pytest.raises(ValueError):
            validate_rows(values, BASE, BASE + 60000, 1000)
    bad = row()
    bad[2] = '98'
    with pytest.raises(ValueError, match='OHLCV'):
        validate_rows([bad], BASE, BASE + 60000, 1000)


def test_raw_receipt_cache_reproduces_normalized_minutes_and_rejects_tamper(tmp_path, monkeypatch):
    monkeypatch.setattr('scripts.research_setup_backfill.time.sleep', lambda _: None)
    calls = []
    def response(request):
        calls.append(request)
        assert request.url.host == 'fapi.binance.com'
        assert request.url.params['interval'] == '1m'
        assert int(request.url.params['startTime']) == BASE
        assert int(request.url.params['endTime']) == BASE + 119999
        return httpx.Response(200, json=[row(), row(BASE+60000)])
    start = datetime.fromtimestamp(BASE/1000, UTC)
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        public = PublicHistory(tmp_path, LIMITS, client)
        first = public.history('TESTUSDT', start, start + timedelta(minutes=2))
        second = public.history('TESTUSDT', start, start + timedelta(minutes=2))
        assert first == second and public.requests == 1 and len(calls) == 1
        assert len(first['candles']) == 2
        raw = next(p for p in (tmp_path/'raw').glob('*.json') if not p.name.endswith('.receipt.json'))
        raw.write_text('[]')
        with pytest.raises(ValueError, match='receipt mismatch'):
            public.history('TESTUSDT', start, start + timedelta(minutes=2))


def test_throttle_stops_without_retry_or_host_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr('scripts.research_setup_backfill.time.sleep', lambda _: None)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(429, json={'code': -1003}))) as client:
        public = PublicHistory(tmp_path, LIMITS, client)
        with pytest.raises(RuntimeError, match='acquisition stopped'):
            public.page({'symbol': 'TESTUSDT', 'interval':'1m', 'startTime': BASE, 'endTime': BASE+59999, 'limit': 1000})
        assert public.requests == 1


def test_exhausted_budget_or_invalid_symbol_cannot_request(tmp_path):
    def forbidden(_):
        pytest.fail('network operation exceeded approved contract')
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        public = PublicHistory(tmp_path, {**LIMITS, 'max_requests': 0}, client)
        with pytest.raises(RuntimeError, match='budget exhausted'):
            public.page({})
        with pytest.raises(ValueError, match='symbol'):
            public.history('../credentials', datetime.now(UTC), datetime.now(UTC))
