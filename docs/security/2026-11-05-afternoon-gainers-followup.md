# Afternoon gainers follow-up — November 5, 2026

This is a separate checkpoint after the rate-limit interruption. The original 09:30 study, its raw inputs, verified output and review manifest remain unchanged. No strategy, scoring, deployment, scan schedule or research worker was changed.

## Latest observed market and normal scan

Snapshot: approximately 14:19:36 UTC / 15:19 Rome, receipt 14:19:43 UTC. Returned active USDT-perpetual cohort remains the same 522 contracts. All 522 ticker rows satisfy the relative 60-second freshness rule in this snapshot.

| Observed rank | Contract | Rolling 24h change |
|---|---|---:|
|1|LYNUSDT|+33.630%|
|2|AIOUSDT|+24.894%|
|3|FOGOUSDT|+22.893%|
|4|FLUIDUSDT|+21.674%|
|5|ARIAUSDT|+20.032%|
|6|SOLVUSDT|+17.122%|
|7|ONUSDT|+17.073%|
|8|CAPUSDT|+15.545%|
|9|ELSAUSDT|+14.413%|
|10|TRUTHUSDT|+13.181%|

ARIA, SOLV, ON and TRUTH entered this observed list relative to the morning observed ten; JCT, CLANKER, UAI and INIT left. The morning list had eight stale-ticker uncertainties, so this is observed-list turnover rather than a certified exact turnover rate for the entire market.

A normal scheduled local scan completed at 12:01:22 UTC while the chat was interrupted. It published TAG long, AIOT short and TRUST short. TAG is absent from the afternoon top ten. As with the earlier snapshots, no scan ID or immutable archive receipt is present in the returned payload. This is absence in a particular saved snapshot, not proof of never having alerted in other scans.

Production again reports a current scan start (12:01:00 UTC) but returns the October 24 00:17:40 UTC live payload and zero candidates. Localhost returns the 12:01 scan. Both responses and headers were preserved. No refresh, restart, remote SSH, deployment or special observation cohort was started.

## Following the original winners beyond the screenshot

These are changes between two recorded ticker last-price marks roughly 4h49m apart, not executed trades, entry returns, best/worst excursions or evidence about hypothetical stop/target fills.

| Morning observed leader | Mark-to-mark price change | Rolling 24h statistic, morning to afternoon |
|---|---:|---:|
|JCT|-35.27%|+41.819% to -26.926%|
|ELSA|-16.42%|+38.234% to +14.413%|
|LYN|+14.05%|+13.836% to +33.630%|
|AIO|+8.49%|+20.228% to +24.894%|
|CAP|+8.29%|+30.482% to +15.545%|
|INIT|-5.22%|+12.412% to -0.638%|

This changes how a missed-winner investigation must be judged:
- Capturing a token on a gainers screenshot is insufficient. A versioned alert needs a timestamp, state, achievable availability, risk geometry and subsequent path evaluation.
- JCT's reversal does not establish that any particular earlier rejection was correct. An earlier alert could have preceded its rally. Missing alert history prevents that inference.
- CAP's price rose while its rolling return statistic fell: the moving comparison base matters. Leaderboard membership and forward price return are different outcomes.
- Evaluate first-entry timing, persistence, forward endpoints, MFE/MAE and costs/availability separately. Do not tune a system to catch the peak screenshot.

## The new names have different histories too

Four additional histories were retrieved through the last complete 14:00 hourly boundary: 480 hourly candles, 480 hourly OI samples and 4,800 five-minute candles. These are selected-winner descriptive histories, not new full-population five-minute controls.

Before each name's strongest positive hour in the closed 24h window ending 14:00:

| Token | Strongest hour starts UTC | Hour return | Prior 6h price | Prior volume ratio | Lagged 6h OI units |
|---|---|---:|---:|---:|---:|
|ARIA|Nov5 13:00|+4.56%|+10.58%|2.12x|+0.94%|
|ON|Nov5 12:00|+7.76%|+15.53%|12.24x|+7.22%|
|SOLV|Nov4 16:00|+8.25%|-7.55%|1.85x|-1.53%|
|TRUTH|Nov5 11:00|+4.22%|+3.00%|1.28x|-0.88%|

SOLV's strongest hour was yesterday; entering the afternoon list does not mean the underlying move began this afternoon. OI uses units and a one-hour lag, not dollar notional or an assertion of institutional demand. Neither rising nor falling OI identifies liquidation causality.

Applying the same exploratory closed-five-minute transition marker as the morning study produces first markers at ARIA Nov5 07:45, ON Nov5 07:25, SOLV Nov4 22:35 and TRUTH Nov4 16:20. These are not production signals or proven actionable entries. Actual first rolling-list entry times are not established for these four by this follow-up.

## Previously captured morning features versus the afternoon list

Using only the original full-population histories ending 09:00 (captured before this afternoon checkpoint):

| Token newly in the afternoon observed ten | Prior24h price at09:00 | Six-hour volume acceleration | Lagged six-hour OI growth |
|---|---:|---:|---:|
|ARIA|+6.46%|0.90x|+0.69%|
|ON|+2.05%|3.42x|+0.64%|
|SOLV|+11.68%|0.80x|-0.82%|
|TRUTH|+4.98%|1.09x|-1.11%|

ON is a concrete hypothesis for volume expansion without strong concurrent OI growth. ARIA, SOLV and TRUTH do not share that pattern. This does not establish that the engine had access to the same historical measurements or why it omitted them.

The same simple top-ten feature references applied at09:00 across all522 contracts gave:
- Prior24h price momentum: six names in the afternoon observed list, none of the four new entrants.
- Six-hour volume acceleration: two afternoon-list names, including ON as the only new entrant.
- Lagged six-hour OI growth: two afternoon-list names, none of the four new entrants.

This is an exploratory checkpoint, not a preregistered prospective strategy trial. No reference selections were sealed or published at09:00, and the afternoon rolling ranking partly includes price movement before that feature cutoff. It is not a pure forward-five-hour return target, production replay, matched-budget comparison, or demonstrated challenger edge. The output retains performance_claims_permitted=false.

## Implementation implications

The original priority remains defensible: reliable scan/version/publication receipts; explicit timeframe contracts; source-matched rejection diagnostics; and separately tested watch/re-evaluation states before any strategy promotion.

The new evidence adds two constraints:
1. Assess the later path of apparent winners. Avoid treating every excluded top-gainer name as a bad rejection.
2. Test state transitions and multiple feature horizons across full non-winner populations. Avoid making strong OI growth a universal admission prerequisite because it would miss ON's earlier expansion state and other observed recovery paths. This is a testable hypothesis, not approval to remove an existing gate.

The first implementation should not be a blanket threshold relaxation or a larger unconditional scan. Resource budgets, false alerts and target lead time need explicit comparison.

## Reproduction, verification and boundaries

Research script: scripts/research_gainers_afternoon_followup.py.
Verified output: docs/security/evidence/2026-11-05-afternoon-analysis-verified.json.
Raw afternoon market, API, local candidate and new-entrant histories are retained alongside it.

Offline reproduction (new output path required):

    python3 scripts/research_gainers_afternoon_followup.py --output /private/tmp/new-afternoon-check.json

The script reads fixed files, has a main guard and constructs no market/trading runtime or network connection. It does not load credentials or ambient screener settings. New closed-bar timestamps, positive ordered OHLC, volume/taker constraints and exact OI timestamps are validated. Quote-volume calculations are cross-checked with float/math.fsum; mark changes are independently cross-checked against the raw ticker prices. Full-population morning features are derived from the previously verified data, not new retrospective inputs.

These checks establish reproducibility and measured sequences, not institutional causality, a complete historical alert record, validated economic edge or production deployment readiness.

