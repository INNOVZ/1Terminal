import asyncio,sys,json,linecache,io
from pathlib import Path
from contextlib import redirect_stdout
from scripts.research_evidence_stream import read_bundle
from scripts.research_evidence_contract import verify_bundle
from scripts.research_verified_replay import offline
from scripts.research_pipeline_comparison import run_arm
from algotrader.application.crypto_screener import CryptoOpportunityScreener
wanted={'CYBERUSDT','NIGHTUSDT','METUSDT','IDOLUSDT','SIGNUSDT','ACTUSDT','ONEUSDT','ARBUSDT','ICNTUSDT','AINUSDT','AIOTUSDT'}
fields=('archetype','close','ema50_4h','ema200_4h','poc_price','val_price','bandwidth_current','rvol_4h','vol_contraction_ratio','oi_change_7d','oi_delta_4h_val','funding','ret_24h','ret_7d','exc_72h','rsi_4h','rsi_1d','rsi_1w','gate_details','dist_to_ceiling','is_deriv_flow_ok')
events=[];arm=''
code=CryptoOpportunityScreener._score_accumulation_setup.__code__
def tracer(frame,event,arg):
 if frame.f_code!=code:return None
 if event=='return':
  loc=frame.f_locals; inst=loc.get('inst');symbol=inst.id.symbol if inst else None
  if symbol in wanted:
   events.append({'symbol':symbol,'arm':arm,'phase':'derivatives_present' if loc.get('derivatives') is not None else 'no_derivatives','returned_opportunity':arg is not None,'return_line':frame.f_lineno,'source_line':linecache.getline(frame.f_code.co_filename,frame.f_lineno).strip(),'values':{k:loc[k] for k in fields if k in loc},'score':str(arg.score) if arg is not None else None})
 return tracer
async def main():
 global arm
 with offline(),redirect_stdout(io.StringIO()):
  bundle=read_bundle(Path('/evidence/capture.jsonl'))
  record=await verify_bundle(bundle)
  outputs={}
  for challenger in (False,True):
   arm='funding_carry' if challenger else 'control'
   sys.settrace(tracer)
   try: r=await run_arm(record,challenger,bundle['supplemental'])
   finally:sys.settrace(None)
   outputs[arm]={'status':r['status'],'counts':r.get('candidate_results',{}).get('counts')}
 print(json.dumps({'mode':'offline_original_inputs_unchanged_rules','verified_bundle':True,'replay_results':outputs,'events':events},default=str),flush=True)
asyncio.run(main())
