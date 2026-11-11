import asyncio,json,io
from contextlib import redirect_stdout
from pathlib import Path
from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.application.scan_recording import screening_identity
from algotrader.application.scan_replay import ReplayMarket,ReplayTape
from algotrader.domain.scan_observations import ScanDecisionJournal
from algotrader.config import Settings
from scripts.research_evidence_stream import read_bundle
from scripts.research_prospective import load
from scripts.research_verified_replay import offline
class SelectionComplete(Exception):pass
class SelectionMarket(ReplayMarket):
 async def candles(self,*args,**kwargs):raise SelectionComplete()
async def main():
 with offline(),redirect_stdout(io.StringIO()):
  record=read_bundle(Path('/evidence/capture.jsonl'))['record'];d=load(Path('/evidence/study/definition.json'));r=load(Path('/evidence/study/report-48h.json'))
  assert record.scan_id==d['scan_id']
  current=screening_identity()
  assert all(record.identity[k]==v for k,v in current.items())
  new=set(r['arms']['control_engine']['missed_new_observed_gainers'])
  original={e['symbol'] for e in record.decisions if e['stage']=='selection' and e['reason']=='selected'}
  outputs={}
  for budget in (48,320):
   market=SelectionMarket(ReplayTape(record.inputs));journal=ScanDecisionJournal();engine=CryptoOpportunityScreener(Settings.model_construct(**record.identity['configuration']),market,evaluated_at=record.started_at,journal=journal)
   request=dict(record.request);enabled=request.pop('derivatives_enabled');request['max_symbols']=budget
   try:await engine.screen(market,derivatives_port=market if enabled else None,**request)
   except SelectionComplete:pass
   selected={e['symbol'] for e in journal.snapshot() if e['stage']=='selection' and e['reason']=='selected'}
   if budget==48:assert selected==original
   outputs[budget]={'selected_count':len(selected),'new_gainers_selected_count':len(selected&new),'previously_omitted_new_gainers_now_selected':sorted((selected-original)&new),'still_unselected_new_gainers':sorted(new-selected)}
 print(json.dumps({'mode':'selection_only_counterfactual_not_candidate_prediction','original_selection_reproduced':True,'deep_evaluation_performed':False,'results':outputs}),flush=True)
asyncio.run(main())
