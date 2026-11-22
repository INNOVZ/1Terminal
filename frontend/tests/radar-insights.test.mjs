import { test } from 'node:test';
import assert from 'node:assert/strict';
import { radarInsights, timestamp, bookQuote } from '../app/lib/radarInsights.ts';
const now = Date.parse('2026-11-22T10:00:00Z');
const signal = { version: 1, episode_id: 'a', published_at: '2026-11-22T09:00:00Z', price: '100', quote_observed_at: '2026-11-22T09:00:00Z' };
const quote = { price: '110', observed_at: new Date(now).toISOString() };
const base = { direction: 'long', radar_signal: signal, radar_quote: quote };
const close = (a,b) => assert.ok(Math.abs(a-b)<1e-9);
test('long and short distinguish raw change from directional result', () => {
  close(radarInsights(base, undefined, now).signalChange, 10);
  close(radarInsights({...base,direction:'short'}, undefined, now).directionalChange, -10);
});
test('price above planned entry alone cannot infer a trigger', () => {
  assert.equal(radarInsights({...base,suggested_entry:'90'}, undefined, now).triggerChange, null);
});
test('observed entry model uses locked direction and risk, never later plan', () => {
  const row = {...base, radar_triggered_at:'2026-11-22T09:30:00Z',radar_trigger_basis:'breakout', radar_trigger_entry:'100',radar_trigger_stop:'95',suggested_entry:'90'};
  close(radarInsights(row, undefined, now).triggerChange,10);
  close(radarInsights(row, undefined, now).riskMultiple,2);
  close(radarInsights({...row,direction:'short',radar_trigger_stop:'105'}, undefined, now).triggerChange,-10);
  assert.equal(radarInsights({...row,radar_trigger_stop:'105'}, undefined,now).validTrigger,false);
});
test('stale, future, invalid and pre-signal quotes suppress returns', () => {
  for(const q of [{...quote,observed_at:new Date(now-61000).toISOString()}, {...quote,observed_at:new Date(now+6000).toISOString()}, {...quote,price:'NaN'}]) {
    assert.equal(radarInsights({...base,radar_quote:q},undefined,now).signalChange,null);
  }
  assert.equal(radarInsights({...base,radar_signal:{...signal,price:null}},undefined,now).signalChange,null);
  assert.equal(radarInsights({...base,radar_signal:undefined},undefined,now).signalChange,null);
  assert.equal(timestamp('2026-11-22T10:00:00'),null);
});
test('newer timestamp wins and live refresh cannot change frozen baseline', () => {
  const result = radarInsights(base,{price:120,observed_at:new Date(now+1000).toISOString()},now+1000);
  close(result.signalChange,20);
  assert.equal(base.radar_signal.price,'100');
  assert.equal(radarInsights(base,{price:120,observed_at:new Date(now-2000).toISOString()},now).quote.price,'110');
});
test('stale trigger and future trigger do not report model profit', () => {
  const row={...base,radar_triggered_at:new Date(now+1000).toISOString(),radar_trigger_basis:'retest',radar_trigger_entry:'100',radar_trigger_stop:'95'};
  assert.equal(radarInsights(row,undefined,now).triggerChange,null);
});

test('book midpoint rejects malformed, crossed and stale markets', () => {
  assert.equal(bookQuote('99','101',now,now).price,100);
  for (const args of [[101,99,now], [0,100,now], ['NaN',101,now], [99,101,now-61000], [99,101,undefined]]) {
    assert.equal(bookQuote(...args,now),null);
  }
});
