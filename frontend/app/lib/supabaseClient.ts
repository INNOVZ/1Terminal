import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const SUPABASE_URL =
  process.env.NEXT_PUBLIC_SUPABASE_URL || "https://utgmsaozryszatkltzym.supabase.co";
const SUPABASE_KEY =
  process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ||
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ||
  "sb_publishable_8zGEuHchaQR-e9rdXS-pDA_gfGyc9S2";

let browserClient: SupabaseClient | null = null;

export function getSupabaseClient(): SupabaseClient {
  if (!browserClient) {
    browserClient = createClient(SUPABASE_URL, SUPABASE_KEY, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
      },
    });
  }
  return browserClient;
}

export interface UserWatchlistRow {
  user_id: string;
  symbol: string;
  name: string;
  sector: string;
  stage: string;
  timeframe: string;
  current_price: number;
  trigger_price: number;
  support_price: number;
  invalidation_price: number;
  target_1: number;
  target_2: number;
  distance_to_trigger_pct: number;
  score: number;
  thesis: string;
  checklist_passed: number;
  checklist_total: number;
  priority: string;
  notes: string;
  added_at: string;
  updated_at: string;
}

export const CURATED_INSTITUTIONAL_STARTER_SEEDS: Omit<
  UserWatchlistRow,
  "user_id" | "added_at" | "updated_at"
>[] = [
  {
    symbol: "ONTUSDT",
    name: "Ontology",
    sector: "L1 / Identity",
    stage: "Phase C: Spring / Shakeout Test",
    timeframe: "1–3 Days",
    current_price: 0.06172,
    trigger_price: 0.0638,
    support_price: 0.05935,
    invalidation_price: 0.05907,
    target_1: 0.0685,
    target_2: 0.07845,
    distance_to_trigger_pct: 3.37,
    score: 63.3,
    thesis:
      "Wyckoff Phase C test holding 4H EMA50 ($0.0592) & 30d POC ($0.0593). Perfect 5/5 Institutional Grade A+ pass.",
    checklist_passed: 5,
    checklist_total: 5,
    priority: "HIGH",
    notes:
      "Watch 4H close above $0.0638 ceiling for markup ignition. Funding is cheap (+0.44 bps).",
  },
  {
    symbol: "COMPUSDT",
    name: "Compound",
    sector: "DeFi Lending",
    stage: "VCP: Volatility Compression",
    timeframe: "1–3 Days",
    current_price: 23.93,
    trigger_price: 25.8,
    support_price: 23.4,
    invalidation_price: 22.45,
    target_1: 25.8,
    target_2: 28.5,
    distance_to_trigger_pct: 7.81,
    score: 60.5,
    thesis:
      "Multi-week base holding above 30d POC ($23.40). Bollinger Bandwidth contracted with positive smart money absorption.",
    checklist_passed: 5,
    checklist_total: 5,
    priority: "HIGH",
    notes: "Orderbook shows absorption at $23.40 support. Target 1 offers 3.5:1 R:R.",
  },
  {
    symbol: "JSTUSDT",
    name: "JUST",
    sector: "TRON DeFi",
    stage: "Pre-Breakout Coil Imminent",
    timeframe: "1–2 Days",
    current_price: 0.14228,
    trigger_price: 0.144,
    support_price: 0.1365,
    invalidation_price: 0.1402,
    target_1: 0.1629,
    target_2: 0.1959,
    distance_to_trigger_pct: 1.21,
    score: 65.1,
    thesis:
      "Ultra-tight 72h range (4.9% height) riding on top of 30d VAH ($0.1356). Resting right beneath breakout trigger.",
    checklist_passed: 4,
    checklist_total: 5,
    priority: "HIGH",
    notes: "Taker aggression ratio is 2.11x (active spot buyers). Tightly coiled spring.",
  },
  {
    symbol: "APTUSDT",
    name: "Aptos",
    sector: "L1 Infra / Move",
    stage: "Phase B: Supply Absorption",
    timeframe: "3–5 Days",
    current_price: 8.45,
    trigger_price: 8.95,
    support_price: 8.15,
    invalidation_price: 7.85,
    target_1: 10.2,
    target_2: 12.4,
    distance_to_trigger_pct: 5.92,
    score: 58.2,
    thesis:
      "Phase B absorption of overhead supply. Volume contracted 35% on down days while holding 4H EMA200 support.",
    checklist_passed: 4,
    checklist_total: 5,
    priority: "MEDIUM",
    notes: "Allow base to complete one more contraction before placing limit trigger at $8.95.",
  },
  {
    symbol: "SUIUSDT",
    name: "Sui Network",
    sector: "Move L1 Ecosystem",
    stage: "POC Retest & Acceptance",
    timeframe: "3–5 Days",
    current_price: 2.14,
    trigger_price: 2.28,
    support_price: 2.02,
    invalidation_price: 1.95,
    target_1: 2.58,
    target_2: 3.1,
    distance_to_trigger_pct: 6.54,
    score: 57.5,
    thesis:
      "Retesting 30-day POC level with rising open interest and neutral funding rate. Forming higher low on 4H.",
    checklist_passed: 4,
    checklist_total: 5,
    priority: "WATCH",
    notes: "Watch for volume ignition spike on 1H chart to confirm springboard off POC.",
  },
];

