"""Read-only VM diagnostic, executed in the frozen research image with no network."""
import asyncio
import io
import json
from contextlib import redirect_stdout
from decimal import Decimal
from pathlib import Path

from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.application.scan_recording import screening_identity
from algotrader.application.scan_replay import ReplayMarket, ReplayTape
from algotrader.config import Settings
from algotrader.domain.liquidity import SymbolLiquidityMetrics, compute_ili_ranks
from algotrader.domain.scan_observations import ScanDecisionJournal
from scripts.research_evidence_stream import read_bundle
from scripts.research_prospective import load
from scripts.research_verified_replay import offline


class SelectionComplete(Exception):
    pass


class SelectionMarket(ReplayMarket):
    async def quotes(self, instruments):
        result = await super().quotes(instruments)
        self.captured_quotes = result
        return result

    async def candles(self, *args, **kwargs):
        raise SelectionComplete()


async def main():
    with offline(), redirect_stdout(io.StringIO()):
        record = read_bundle(Path('/evidence/capture.jsonl'))['record']
        definition = load(Path('/evidence/study/definition.json'))
        report = load(Path('/evidence/study/report-48h.json'))
        assert record.scan_id == definition['scan_id']
        assert all(record.identity[k] == v for k, v in screening_identity().items())
        gainers = {r['symbol'] for r in report['symbols'] if r['initial_inside'] is False and r['first_observed_top10_at'] is not None}
        original = {e['symbol'] for e in record.decisions if e['stage'] == 'selection' and e['reason'] == 'selected'}
        outputs = {}
        for budget in [48, 320]:
            market = SelectionMarket(ReplayTape(record.inputs))
            journal = ScanDecisionJournal()
            engine = CryptoOpportunityScreener(Settings.model_construct(**record.identity['configuration']), market, evaluated_at=record.started_at, journal=journal)
            request = dict(record.request)
            enabled = request.pop('derivatives_enabled')
            request['max_symbols'] = budget
            try:
                await engine.screen(market, derivatives_port=market if enabled else None, **request)
            except SelectionComplete:
                pass
            selected = {e['symbol'] for e in journal.snapshot() if e['stage'] == 'selection' and e['reason'] == 'selected'}
            if budget == 48:
                assert selected == original
            quotes = {q.instrument_id.symbol: q for q in market.captured_quotes}
            liquid = [i.id.symbol for i in journal.cohort if i.id.symbol in quotes and quotes[i.id.symbol].quote_volume_24h >= Decimal('500000') and quotes[i.id.symbol].spread_bps <= Decimal('50')]
            ranked = compute_ili_ranks([SymbolLiquidityMetrics(symbol=s, quote_vol_24h=quotes[s].quote_volume_24h) for s in liquid])
            ranks = {r.symbol: index for index, r in enumerate(ranked, 1)}
            target_core = min(budget, max(1, int(budget * (215 / 265))))
            details = []
            for symbol in sorted(gainers):
                quote = quotes.get(symbol)
                row = {'symbol': symbol, 'selected': symbol in selected, 'liquidity_rank': ranks.get(symbol), 'discovery_blockers': []}
                if quote is not None:
                    row.update(quote_volume_24h=quote.quote_volume_24h, spread_bps=quote.spread_bps, raw_funding_rate=quote.funding_rate, basis_pct=quote.basis_pct, change_pct_24h=quote.price_change_pct_24h, range_24h_pct=quote.range_24h_pct)
                    if symbol not in selected and ranks.get(symbol, 0) > target_core:
                        if quote.quote_volume_24h < Decimal('1500000'):
                            row['discovery_blockers'].append('discovery_quote_volume_below_1500000')
                        if quote.spread_bps > Decimal('25'):
                            row['discovery_blockers'].append('discovery_spread_above_25bps')
                        row['remaining_reason'] = 'qualification_or_anomaly_rank_budget' if not row['discovery_blockers'] else 'discovery_hygiene'
                details.append(row)
            outputs[str(budget)] = {'target_core': target_core, 'target_discovery': budget - target_core, 'selected_count': len(selected), 'new_gainers_selected': len(gainers & selected), 'details': details}
    print(json.dumps({'original_selection_reproduced': True, 'deep_evaluation_performed': False, 'budgets': outputs}, default=str), flush=True)


asyncio.run(main())
