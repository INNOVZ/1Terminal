import pathlib,json,hashlib,collections,datetime,decimal,signal
signal.alarm(240)
D=decimal.Decimal;dt=datetime.datetime.fromisoformat;UTC=datetime.timezone.utc
root=pathlib.Path("/home/deploy/alfa-terminal/data/research");parent="79c0a603-20ca-4099-820d-132c8905ec14";child="bfac3bda-dc08-4832-8e7c-b741d90fae46"
def dec(x):
 if isinstance(x,list):return [dec(v) for v in x]
 if isinstance(x,dict):
  t=x.get("@")
  if t=="mapping":return {k:dec(v) for k,v in x["items"].items()}
  if t in ("tuple","list"):return [dec(v) for v in x["items"]]
  if t in ("datetime","Decimal"):return x["value"]
  if "fields" in x:return dec(x["fields"])
  return {k:dec(v) for k,v in x.items()}
 return x
def read(p):
 raw=(p/"payload.json").read_bytes();m=json.loads((p/"manifest.json").read_text());sha=hashlib.sha256(raw).hexdigest()
 assert sha==m["sha256"] and len(raw)==m["bytes"],str(p)
 return dec(json.loads(raw)),sha
errors=[];rankmap={};outmap={};focus=("SOONUSDT","IDOLUSDT","MERLUSDT","UAIUSDT","BEAMXUSDT","LSKUSDT");anchor=dt("2026-10-30T04:30:00+00:00")
for p in (root/"ranks"/parent).iterdir():
 if p.name.startswith("."):continue
 env,sha=read(p);r=env["record"];normalized=r["normalized"];symbols={i["id"]["symbol"] for i in r["cohort"]};counts=collections.Counter(v.get("symbol") for v in r["rows"]);arrival=dt(r["arrived_at"]);valid={}
 for row in r["rows"]:
  s=row.get("symbol")
  if s not in symbols:continue
  try:
   price,change,tm=(D(str(row[k])) for k in ("lastPrice","priceChangePercent","closeTime"))
   if not all(v.is_finite() for v in (price,change,tm)) or price<=0 or counts[s]!=1:continue
   cutoff=datetime.datetime.fromtimestamp(float(tm/1000),UTC)
   if not -5<=(arrival-cutoff).total_seconds()<=60:continue
   valid[s]=(change,cutoff)
  except (ValueError,KeyError,decimal.InvalidOperation,OverflowError):continue
 unknown=len(symbols)-len(valid);times=[v[1] for v in valid.values()];span=bool(times) and (max(times)-min(times)).total_seconds()<=60
 bounded=bool(valid) and span;complete=bounded and not unknown
 assert len(symbols)==512 and len(normalized["members"])==512
 assert normalized["coverage"]["valid"]==len(valid) and normalized["coverage"]["unknown_count"]==unknown and normalized["bounded"]==bounded and normalized["complete"]==complete
 for m in normalized["members"]:
  val=valid.get(m["symbol"]);lo=1+sum(v[0]>val[0] for v in valid.values()) if bounded and val else None;hi=lo+unknown if lo is not None else None
  inside=None if lo is None or lo<=10<hi else hi<=10
  if (m["rank_min"],m["rank_max"],m["inside"],m["rank"])!=(lo,hi,inside,lo if complete else None):errors.append(["rank",p.name,m["symbol"]])
 rankmap[p.name]={"sha":sha,"at":r["arrived_at"],"complete":complete,"focus":{m["symbol"]:m for m in normalized["members"] if m["symbol"] in focus}}
for h in (24,48):
 for symbolpath in (root/"shadow-outcomes"/child/str(h)).iterdir():
  if symbolpath.name.startswith("_"):continue
  for p in symbolpath.iterdir():
   if p.name.startswith("."):continue
   a,sha=read(p);bars=sorted(a["candles"],key=lambda b:b["opened_at"]);o=a["observation"]
   assert o["status"]=="complete" and len(bars)==h*60
   for j,b in enumerate(bars):
    assert b["closed"] and dt(b["opened_at"])==anchor+datetime.timedelta(minutes=j)
    assert dt(b["closed_at"])<=dt(a["observed_at"])
   base=D(bars[0]["open"]);end=D(bars[-1]["close"]);high=max(D(b["high"]) for b in bars);low=min(D(b["low"]) for b in bars)
   expected={"baseline":base,"end_price":end,"raw_return_pct":(end/base-1)*100,"high_excursion_pct":(high/base-1)*100,"low_excursion_pct":(low/base-1)*100}
   for k,v in expected.items():
    if abs(D(o[k])-v)>D("1e-20"):errors.append(["outcome",h,a["symbol"],k,str(v),o[k]])
   outmap[(h,a["symbol"],a["attempt_id"])]=(sha,o)
reports=[]
for p in (root/"benchmarks"/parent).iterdir():
 if p.name.startswith("."):continue
 env,sha=read(p);r=env["record"];h=r["horizon_hours"];payload=r["payload"];manifest=r["manifest"]
 for s,ref in manifest["selected_outcomes"].items():
  actualsha,obs=outmap[(h,s,ref["attempt_id"])];assert actualsha==ref["checksum"];assert payload["outcomes"][s]==obs
 for ref in manifest["rank_snapshots"]:assert rankmap[ref["snapshot_id"]]["sha"]==ref["checksum"]
 ids=payload["timeline"]["sampling"]["accepted_ids"];rs=sorted((rankmap[i] for i in ids),key=lambda x:x["at"]);end=anchor+datetime.timedelta(hours=h)
 boundaries=[anchor]+[dt(x["at"]) for x in rs]+[end];gaps=[((b-a).total_seconds(),a.isoformat(),b.isoformat()) for a,b in zip(boundaries,boundaries[1:])]
 first={s:next(({"at":x["at"],"min":x["focus"][s]["rank_min"],"max":x["focus"][s]["rank_max"]} for x in rs if x["focus"][s]["inside"] is True),None) for s in focus}
 reports.append({"horizon":h,"checksum":sha,"outcomes":len(manifest["selected_outcomes"]),"rank_records":len(rs),"max_gap":max(gaps),"first_memberships":first})
print(json.dumps({"independent_errors":errors,"rank_records_hash_and_member_bounds_checked":len(rankmap),"forward_outcomes_hash_continuity_and_math_checked":len(outmap),"reports":reports}))

