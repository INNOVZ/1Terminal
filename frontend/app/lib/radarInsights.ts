export interface RadarQuote {
  price: string | number;
  observed_at: string;
  source?: string;
}
export interface RadarSignal {
  version: number;
  episode_id: string;
  published_at: string;
  price: string | null;
  quote_observed_at: string | null;
}

export function timestamp(value?: string | null): number | null {
  // Require an explicit timezone; never silently interpret local timestamps as UTC.
  if (!value || !/(Z|[+-]\d\d:\d\d)$/.test(value)) return null;
  const n = Date.parse(value);
  return Number.isFinite(n) ? n : null;
}

export function radarInsights(
  opportunity: {
    direction: string; radar_signal?: RadarSignal; radar_quote?: RadarQuote | null;
    radar_triggered_at?: string; radar_trigger_basis?: string;
    radar_trigger_entry?: string; radar_trigger_stop?: string;
  },
  liveQuote: RadarQuote | undefined,
  now: number,
) {
  const sign = opportunity.direction.toLowerCase() === "short" ? -1 : 1;
  const quote = [opportunity.radar_quote, liveQuote]
    .filter((q): q is RadarQuote => !!q && timestamp(q.observed_at) !== null
      && Number.isFinite(Number(q.price)) && Number(q.price) > 0)
    .sort((a, b) => timestamp(b.observed_at)! - timestamp(a.observed_at)!)[0];
  const observed = timestamp(quote?.observed_at);
  const fresh = observed !== null && now - observed >= -5000 && now - observed <= 60000;
  const price = quote ? Number(quote.price) : null;
  const signal = opportunity.radar_signal;
  const published = timestamp(signal?.published_at);
  const baselineTime = timestamp(signal?.quote_observed_at);
  const baseline = Number(signal?.price);
  const validSignal = signal?.version === 1 && published !== null && published <= now
    && baselineTime !== null && baselineTime <= now + 5000
    && published - baselineTime >= -5000 && published - baselineTime <= 60000
    && Number.isFinite(baseline) && baseline > 0;
  const signalChange = fresh && price !== null && validSignal && observed! >= baselineTime!
    ? (price / baseline - 1) * 100 : null;
  const triggered = timestamp(opportunity.radar_triggered_at);
  const entry = Number(opportunity.radar_trigger_entry);
  const stop = Number(opportunity.radar_trigger_stop);
  const validTrigger = triggered !== null && triggered <= now
    && ["breakout", "retest"].includes(opportunity.radar_trigger_basis || "")
    && Number.isFinite(entry) && entry > 0 && Number.isFinite(stop) && stop > 0
    && sign * (entry - stop) > 0;
  const triggerChange = fresh && price !== null && validTrigger && observed! >= triggered!
    ? sign * (price / entry - 1) * 100 : null;
  return { quote, fresh, price, signalChange, directionalChange: signalChange === null ? null : sign * signalChange,
    validTrigger, triggerChange, entry, stop, triggered,
    riskMultiple: triggerChange === null || price === null ? null : sign * (price - entry) / Math.abs(entry - stop) };
}

/** The server quote adapter uses the same bid/ask midpoint, never last-trade price. */
export function bookQuote(bid: unknown, ask: unknown, time: unknown, now: number): RadarQuote | null {
  const b = Number(bid), a = Number(ask);
  if (!Number.isFinite(b) || !Number.isFinite(a) || b <= 0 || a < b
    || typeof time !== "number" || !Number.isFinite(time) || now - time < -5000 || now - time > 60000) return null;
  return { price: (b + a) / 2, observed_at: new Date(time).toISOString(), source: "binance_usdm_bid_ask_midpoint" };
}
