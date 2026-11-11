import asyncio,json,gc,time,resource
from pathlib import Path
from datetime import UTC,datetime
from decimal import Decimal
import httpx
from algotrader.adapters.binance.futures import BinanceUsdmAdapter
from algotrader.adapters.external.models import FearGreedReading,GlobalMarketMetrics,MacroSentimentContext,StablecoinSupplyMetric
from algotrader.domain.scan_observations import RecordedCall
from scripts.research_capture_evidence import EvidenceClient,EvidencePort,EvidenceTransport,SCHEMA,capture_evidence
from scripts.research_evidence_stream import read_bundle,write_bundle
from scripts.research_pipeline_comparison import compare_bundle
from scripts.research_verified_replay import diagnostic_settings,offline,write_report
class Macro:

    async def close(self):
        pass

    async def get_context(self):
        at = datetime.now(UTC)
        return MacroSentimentContext(FearGreedReading(50, 'Neutral', at), StablecoinSupplyMetric(Decimal('100'), Decimal('0'), Decimal('0'), Decimal('0'), at), GlobalMarketMetrics(Decimal('60'), Decimal('0'), Decimal('100'), Decimal('100'), at), at)

def public_response(request):
    path, params = (request.url.path, request.url.params)
    now = int(datetime.now(UTC).timestamp() * 1000)
    symbols = ('BTCUSDT', *(f'SYNTH{i}USDT' for i in range(320)))
    if path.endswith('exchangeInfo'):
        value = {'symbols': [{'symbol': s, 'baseAsset': s[:-4], 'quoteAsset': 'USDT', 'status': 'TRADING', 'contractType': 'PERPETUAL', 'filters': []} for s in symbols]}
    elif path.endswith('ticker/24hr'):
        value = [{'symbol': s, 'quoteVolume': '30000000' if s == 'SOLUSDT' else '15000000', 'highPrice': '101', 'lowPrice': '99', 'priceChangePercent': '0', 'closeTime': now} for s in symbols]
    elif path.endswith('ticker/bookTicker'):
        value = [{'symbol': s, 'bidPrice': '99.95', 'askPrice': '100.05', 'time': now} for s in symbols]
    elif path.endswith('premiumIndex'):

        def premium(s):
            return {'symbol': s, 'lastFundingRate': '-0.0002', 'markPrice': '100', 'indexPrice': '100', 'time': now, 'nextFundingTime': (now // 14400000 + 1) * 14400000}
        value = premium(params['symbol']) if 'symbol' in params else [premium(s) for s in symbols]
    elif path.endswith('klines'):
        interval = params['interval']
        ms = {'1h': 3600000, '4h': 14400000, '1w': 604800000}[interval]
        offset = 345600000 if interval == '1w' else 0
        last = (now - offset) // ms * ms + offset
        count = int(params['limit'])
        value = [[last - (count - 1 - i) * ms, '100', '101', '99', '100', '1000', last - (count - 1 - i) * ms + ms - 1, '100000', 1, '500', '50000', '0'] for i in range(count)]
    elif path.endswith('openInterest'):
        value = {'symbol': params['symbol'], 'openInterest': '333', 'time': now}
    elif path.endswith('openInterestHist'):
        latest = now // 3600000 * 3600000
        value = [{'timestamp': latest - (199 - i) * 3600000, 'sumOpenInterest': str(100 + i)} for i in range(200)]
    elif path.endswith('takerlongshortRatio'):
        latest = now // 3600000 * 3600000 - 3600000
        value = [{'timestamp': latest - i * 3600000, 'buyVol': '100', 'sellVol': '100'} for i in range(26)]
    elif path.endswith('depth'):
        value = {'bids': [['100', '1000']], 'asks': [['100', '1000']]}
    elif path.endswith('fundingInfo'):
        value = [{'symbol': 'SOLUSDT', 'fundingIntervalHours': 4}]
    elif path.endswith('fundingRate'):
        value = [{'symbol': params['symbol'], 'fundingTime': now // 14400000 * 14400000, 'fundingRate': '0'}]
    else:
        raise RuntimeError(f'unexpected public path {path}')
    return httpx.Response(200, json=value)
async def fixture():
    transport=EvidenceTransport(httpx.MockTransport(public_response),attempts=3000,seconds=600)
    client=EvidenceClient('https://fapi.binance.com',transport=transport,max_rate_limit_retries=0,max_transport_retries=0)
    port=EvidencePort(BinanceUsdmAdapter(client))
    try:
        record,extra=await capture_evidence(port,Macro(),diagnostic_settings(),max_symbols=320,max_supplemental=60)
        extra=list(extra)
        selected={e['symbol'] for e in record.decisions if e['stage']=='selection' and e['reason']=='selected'}
        known={c.arguments['instrument'].id.symbol for c in (*record.inputs,*extra) if c.method=='derivatives_snapshot'}
        for inst in record.cohort:
            if len(extra)>=60: break
            if inst.id.symbol not in selected or inst.id.symbol in known: continue
            arguments={'instrument':inst}
            value=await port.derivatives_snapshot(inst)
            extra.append(RecordedCall('derivatives_snapshot',0,arguments,value))
            known.add(inst.id.symbol)
        await client.request('GET','/fapi/v1/fundingInfo')
        for symbol in sorted(selected):
            await client.request('GET','/fapi/v1/fundingRate',params={'symbol':symbol,'limit':1})
        return {'schema':SCHEMA,'status':'captured','record':record,'supplemental':tuple(extra),'frames':port.frames,'receipts':transport.receipts,'completed_at':datetime.now(UTC),'enrolled':False,'outcome_anchor':None,'synthetic_capacity_only':True}
    finally:
        await client.close()

async def main():
    start=time.monotonic()
    bundle=await fixture()
    captured=time.monotonic()
    path=Path('/output/capacity.jsonl')
    write_bundle(path,bundle)
    fields={'selected':bundle['record'].counts['selected'],'supplemental':len(bundle['supplemental']),'control_counts':bundle['record'].counts,'capture_seconds':captured-start,'write_seconds':time.monotonic()-captured,'file_bytes':path.stat().st_size}
    print(json.dumps({'phase':'capture_written',**fields}),flush=True)
    del bundle
    gc.collect()
    begin=time.monotonic()
    restored=read_bundle(path)
    fields['read_seconds']=time.monotonic()-begin
    print(json.dumps({'phase':'read',**fields}),flush=True)
    with offline():
        report=await compare_bundle(restored)
    fields.update(status=report['status'],reason=report.get('reason'),challenger_reason=report.get('quote_funding_carry',{}).get('reason'),total_seconds=time.monotonic()-start,max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,synthetic=True,network='disabled',enrolled=False)
    write_report(Path('/output/capacity-result.json'),fields)
    print(json.dumps(fields),flush=True)
    assert report['status']=='complete', fields
asyncio.run(main())
