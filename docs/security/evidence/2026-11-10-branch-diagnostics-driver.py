import asyncio,sys,json,io
from contextlib import redirect_stdout
from pathlib import Path
from scripts.research_evidence_stream import read_bundle
from scripts.research_evidence_contract import verify_bundle
from scripts.research_verified_replay import offline
from scripts.research_pipeline_comparison import run_arm
from algotrader.domain.screening_gates import evaluate_long_gates
from algotrader.domain.archetypes import classify_long_archetype
rows=[];gatecode=evaluate_long_gates.__code__;archcode=classify_long_archetype.__code__
def trace(frame,event,arg):
 if frame.f_code not in (gatecode,archcode):return None
 if event=='return':
  parent=frame.f_back.f_locals;inst=parent.get('inst');symbol=inst.id.symbol if inst else None
  if frame.f_code==gatecode and symbol=='NIGHTUSDT':
   loc=frame.f_locals;inputs=loc['inputs']
   rows.append({'symbol':symbol,'phase':'derivatives_present' if parent.get('derivatives') is not None else 'no_derivatives','kind':'long_gates','returned':arg,'branches':{k:loc[k] for k in ('flow_confirmed','ignition_confirmed','has_ignition','stealth_coil_qualified','coil_maturity','volume_vacuum','mature_squeeze','has_oi_metrics','universal_oi_ok','is_acute_oi_liquidation','dist_to_ceiling') if k in loc},'inputs':{k:getattr(inputs,k) for k in ('rvol_4h','oi_delta_1h','oi_delta_4h','oi_delta_7d','taker_buy_ratio_1h','has_kinetic_ignition','pocket_pivot_1h','micro_expansion_1h','of_absorption_1h')}})
  if frame.f_code==archcode and symbol=='ARBUSDT':
   loc=frame.f_locals;f=loc['features']
   rows.append({'symbol':symbol,'kind':'archetype','returned':arg,'branches':{k:loc[k] for k in ('is_mom_up','is_compressed','is_vcp_compressed','has_supply_exhaustion','is_val_support_holding','taker_4h','holding_structure') if k in loc},'features':{k:getattr(f,k) for k in ('range_height_30d','pos_in_range_30d','dist_to_ceiling','close','ema50_4h','poc_30d','val_30d','bb_bandwidth_4h','vcr_4h','macd_hist_4h','macd_hist_4h_prev')}})
 return trace
async def main():
 with offline(),redirect_stdout(io.StringIO()):
  bundle=read_bundle(Path('/evidence/capture.jsonl'));record=await verify_bundle(bundle)
  sys.settrace(trace)
  try:r=await run_arm(record,False)
  finally:sys.settrace(None)
 print(json.dumps({'mode':'offline_unchanged_control_replay','record_request':record.request,'status':r['status'],'diagnostics':rows},default=str))
asyncio.run(main())
