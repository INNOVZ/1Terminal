import httpx,json,signal,time
from datetime import datetime,UTC,timedelta
from decimal import Decimal
signal.alarm(180)
anchor=datetime(2026,10,30,4,30,tzinfo=UTC);start=int(anchor.timestamp()*1000);end=start+48*3600000
symbols=("SOONUSDT","IDOLUSDT","MERLUSDT","UAIUSDT","BEAMXUSDT","LSKUSDT")
calls=0;result=[]
with httpx.Client(timeout=10,follow_redirects=False,trust_env=False) as client:
 for symbol in symbols:
  rows=[];cursor=start;error=None
  try:
   for page in range(3):
    calls+=1;assert calls<=18
    r=client.get("https://fapi.binance.com/fapi/v1/klines",params={"symbol":symbol,"interval":"1m","startTime":cursor,"endTime":end-1,"limit":1000})
    if r.status_code in (418,429):raise RuntimeError("exchange_throttle_no_retry")
    r.raise_for_status();batch=r.json();assert isinstance(batch,list)
    if not batch:break
    rows.extend(batch);cursor=int(batch[-1][0])+60000
    if cursor>=end:break
   by={int(r[0]):r for r in rows};assert len(by)==len(rows)==2880
   assert all(start+i*60000 in by for i in range(2880))
   prices=[]
   for r in rows:
    o,h,l,c=map(Decimal,(r[1],r[2],r[3],r[4]));assert all(v.is_finite() and v>0 for v in (o,h,l,c)) and l<=min(o,c)<=max(o,c)<=h
   first=Decimal(by[start][1])
   for h in (24,48):
    path=[by[start+i*60000] for i in range(h*60)];last=Decimal(path[-1][4])
    result.append({"symbol":symbol,"horizon_hours":h,"baseline":str(first),"end_price":str(last),"raw_return_pct":str((last/first-1)*100),"high_excursion_pct":str((max(Decimal(r[2]) for r in path)/first-1)*100),"low_excursion_pct":str((min(Decimal(r[3]) for r in path)/first-1)*100),"minutes":len(path)})
  except Exception as exc:
   result.append({"symbol":symbol,"error":type(exc).__name__,"detail":str(exc)[:160]})
   if "throttle" in str(exc):break
print(json.dumps({"source":"Binance USD-M public historical 1m klines","retrieved_at":datetime.now(UTC).isoformat(),"retrospective_supplement_only":True,"frozen_reports_unchanged":True,"http_attempts":calls,"anchor":anchor.isoformat(),"rows":result}),flush=True)

