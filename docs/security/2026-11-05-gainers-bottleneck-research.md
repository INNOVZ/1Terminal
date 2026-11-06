# November 5 gainers audit: discovery bottlenecks and evidence limits

Research only. No production gates, score weights, deployments, scans, research sessions or collection anchors changed. The existing engineering-research workflow was used. This report builds on the November 4 five-token study rather than replacing it with a new winner-fitting exercise.

## Decision

There is evidence for a discovery-timing problem and an evidence/publication problem. There is not yet evidence for a particular replacement strategy or a promise to capture future leaders.

The immediate strategy work should define honest feature contracts and test a separately versioned, stateful discovery/watch track across the full population. The current entry-quality core should remain the benchmark. Fix publication freshness and ensure normal scheduled scans actually retain their already-implemented versioned receipts/journals so exclusions can be attributed. Another unchanged frozen-cohort observation is not a strategy improvement.

## Markets, dates and source quality

- Current snapshot: November 5, 2026, approximately 09:30:38 UTC / 10:30 Rome; recorded receipt 09:30:40 UTC.
- Universe: 522 returned Binance TRADING, USDT-quoted, PERPETUAL contracts. This is a defined derivatives cohort, not the global Binance spot list. Returned exchange metadata has serverTime 06:46:24.913 UTC; its response does not attest every listing change after that time.
- Eight ticker rows exceeded the 60-second relative freshness rule. Current observed ranks are among 514 fresh rows, with rank bounds r through r+8. Only the first two names have certified top-ten membership under those conservative bounds. All eight follow-up ticker reads showed modest changes below 3.71%, but later reads do not repair an earlier point-in-time snapshot.
- Yesterday means November 4, 00:00–24:00 Europe/Rome: November 3 23:00–November 4 23:00 UTC. These are reconstructed fixed calendar-day returns, not an archived rolling leaderboard.
- Common-time current proxy: November 4 09:00–November 5 09:00 UTC, ending at the last complete hourly boundary before the ticker capture. It differs from the 09:30 live rolling window.
- Full-population raw evidence: 62,640 closed hourly candles and 62,640 hourly OI samples, 120 of each per contract. One OI gap exists for ALLUSDT at November 1 10:00 UTC; it is retained and not interpolated. It does not affect the exact OI endpoints in either comparison, which are available for all 522 members.
- Finer selected-event evidence: 16,800 closed five-minute candles for the 14-symbol union of the two observed winner lists. That is retrospective winner selection, not a five-minute full-universe predictive test.
- All historical downloads occurred after the studied events. Event timestamps and a conservative one-hour OI lag do not establish historical API arrival latency.
- Current-universe survivorship, two overlapping windows and missing historical publication records limit inference.

## What the lists and actual candidate evidence say

| Observed current leader | Rolling 24h change | Yesterday calendar-day leader | Fixed-day change |
|---|---:|---|---:|
| JCT | +41.819% | ELSA | +55.11% |
| ELSA | +38.234% | CAP | +31.82% |
| CAP | +30.482% | JCT | +26.98% |
| AIO | +20.228% | CLANKER | +25.86% |
| FLUID | +20.211% | FLUID | +20.77% |
| FOGO | +15.868% | BAT | +14.59% |
| CLANKER | +13.900% | TRUST | +13.78% |
| LYN | +13.836% | RAYSOL | +13.45% |
| UAI | +12.486% | INIT | +12.90% |
| INIT | +12.412% | FOLKS | +11.76% |

Names denote the exact USDT perpetual symbols. AIO and AIOT are different contracts. Current membership/rank uncertainty is described above.

Retained local November 4 08:01:18 UTC longs: MEGA, ZRO, STX, HOME, TAG, EDEN.
Retained local November 5 08:01:19 UTC longs: 1000CAT, UNI, ACT.
Neither list overlaps either displayed winner list. Shorts are not counted as long-discovery successes.

This establishes absence from those two saved snapshots. It does NOT establish absence from every intervening scan or alert. The November 4 scan is already eight hours into the Rome calendar day; it is not a forecast made at that day's start. A current recommendation cannot prove or disprove an earlier alert.

Neither saved payload includes a scan ID, immutable archive receipt or full per-symbol rejection journal. The configured current-checkout archive directory is data/research and was absent; the inspected alternate local default roots also had no archive. The old October 30 research cohort cannot establish November 4/5 discovery. No exact gate-level attribution is claimed for today's winners.

The current source contains scan recording and published receipt fields. Their absence in the observed payload is a runtime/source-alignment question, not proof of a particular deployment revision or cause.

## Confirmed publication discrepancy

Read-only production API checks returned:
- Status: last run November 5 08:01:00 UTC, 320 evaluated, four-hour cadence.
- Live candidate response: October 24 00:17:40 UTC, zero candidates.

Localhost status and candidates agree on today's 08:01 scan, with six published items: three longs and three shorts.

Responses and HTTP headers are retained separately. No refresh parameter, restart, remote SSH, rescan or deployment was performed. GET live was requested only after status indicated populated scan state. The mismatch verifies stale/inconsistent publication, not whether a proxy, cache, fallback, old process or another cause produced it. No frontend-wide deployment claim is made.

A system with stale published results can appear to miss an opportunity independently of its internal strategy. This must be separated from selection/gate misses.

## Observed moves and scan timing

Descriptive five-minute transition: a completed candle closes above the prior 288 five-minute highs and has quote volume at least twice the preceding 20-bar mean. It is an exploratory timing marker, NOT the production entry rule or a validated predictor. All features use earlier bars; the current bar is used only once closed.

Hourly leaderboard proxy: rank the whole 522-member cohort on fixed 24h open-to-close returns at hourly boundaries. First observed membership is not exact first entry. Earlier membership is unknown for left-censored names. Time differences below are not executable alert lead times.

| Token | First five-minute transition in study window UTC | Volume ratio | First observed hourly top-ten boundary | Next nominal scheduled scan |
|---|---|---:|---|---|
| ELSA | Nov 4 07:00 | 3.10x | Nov 4 11:00 | Nov 4 08:01 |
| JCT | Nov 4 11:25 | 5.67x | Nov 4 12:00 | Nov 4 12:01 |
| CAP | Nov 4 10:25 | 5.58x | Nov 4 13:00 | Nov 4 12:01 |
| FOGO | Nov 5 04:05 | 30.31x | Nov 5 05:00 | Nov 5 08:01 |
| LYN | Nov 5 02:15 | 3.14x | Nov 5 03:00 | Nov 5 04:01 |
| UAI | Nov 5 02:50 | 5.56x | Nov 5 07:00 | Nov 5 04:01 |

Interpretation:
- FOGO's transition occurred four minutes after the nominal 04:01 scan. Waiting until 08:01 is later than its 05:00 observed top-ten boundary. JCT and LYN similarly had their next nominal scan after the observed entry boundary. This demonstrates a cadence limitation for these markers; it does not prove a production setup would have passed sooner.
- ELSA had a marker before the retained 08:01 scan and was absent from that snapshot. There was time to evaluate it. Cadence alone cannot explain that snapshot omission. Its admission, archetype, gates and publication path need the actual journal.
- CAP offered a marker 96 minutes before the next nominal scan, with its largest hourly advance later at 12:00–13:00. Whether the engine admitted it at 12:01 is unknown; that candidate history was not retained.
- Hourly first-entry boundaries can lag true entry by an unknown fraction of an hour and cannot detect all enter/exit transitions. These markers do not certify positive lead relative to exact rolling-list entry.

## Precursors were heterogeneous

Before the strongest positive hour in the current closed-hour window:

| Token | Strongest hour starts UTC | That hour | Prior 6h price | Prior volume ratio, 6h versus prior 18h | Lagged 6h OI units |
|---|---|---:|---:|---:|---:|
| JCT | Nov 5 05:00 | +12.58% | +11.65% | 1.10x | +5.52% |
| ELSA | Nov 5 08:00 | +20.64% | -1.01% | 2.03x | +8.52% |
| CAP | Nov 4 12:00 | +12.65% | +3.32% | 1.47x | +0.27% |
| FLUID | Nov 5 00:00 | +4.59% | +9.52% | 1.95x | +19.63% |
| FOGO | Nov 5 04:00 | +6.37% | +2.21% | 0.86x | +8.83% |
| LYN | Nov 4 23:00 | +16.07% | +0.65% | 0.20x | -1.26% |
| UAI | Nov 5 02:00 | +38.51% | -7.82% | 0.48x | -1.04% |

The strongest hour is selected retrospectively; these are explanations of measured sequences, not a deployable event selector. OI comparisons use quantities, not dollar notional, with the latest endpoint one hour before event open. OI growth does not identify institutions or long versus short demand. Negative OI does not prove short liquidations.

At the common November 4 09:00 cutoff, ELSA already had a 6.16x six-hour volume ratio and +10.73% prior-day price change, but lagged OI grew only 1.68%. JCT had -13.39% prior-day price change, 1.71x volume and -2.69% OI. CAP was near flat in price, with 0.91x volume and -0.53% OI. Their future outcomes arose from different starting states.

For the retained November 4 08:01 scan, candle-derived 24h quote-volume ranks at 08:00 were ELSA 319, JCT 29 and CAP 132 within this USDT cohort. These are approximate historical liquidity comparisons, not the actual all-venue deduplicated ILI/admission ranking: historical spreads, funding, basis and source-matched selection receipts are missing. ELSA's lower liquidity makes admission worth tracing; it does not prove selection-limit rejection.

## Full-population sanity checks

Features are measured at common timestamps, not at each winner's hindsight-selected best hour. Outcomes are the next fixed 24h return ranking among all 522 contracts. These simple research references are not Atlas production outputs or a fair matched-budget engine comparison.

| Exploratory reference | Selections | Next fixed-24h top-ten overlaps: Nov 3 23:00 start | Next fixed-24h top-ten overlaps: Nov 4 09:00 start |
|---|---:|---:|---:|
| Prior 24h price momentum | 10 | 2 | 3 |
| Prior 6h quote-volume acceleration | 10 | 1 | 3 |
| Lagged prior 6h OI-unit growth | 10 | 3 | 3 |
| Volume ratio >=1.5 and lagged OI growth >=3% | 4 / 24 respectively | 2 | 2 |

Thus a universal volume-plus-OI conjunction would miss eight of ten endpoint winners in each window. In the second window, 22 of its 24 selections did not finish top ten; they are not necessarily losing trades. Different filters' selection counts differ. These exploratory references are neither preregistered nor independent holdouts, and they do not demonstrate a superior challenger.

Eligibility sensitivity: restricting the population to 512 tokens using the inspected engine policy/current configuration leaves every displayed overlap count and both combined-filter selection counts unchanged. It removes stablecoins, benchmark exclusions and non-ASCII bases; the target top-ten lists also remain unchanged. This is a sensitivity check, not attestation of historical deployment configuration. The frozen exclusions, outputs and an offline reproduction script are retained as 2026-11-05-eligible-reference-sensitivity.json and 2026-11-05-eligible-sensitivity-reproduce.py.

All-symbol features and outcomes, including non-winners, are retained. No threshold sweep was used to pick a winning parameter, and performance_claims_permitted is false.

## Where the system can fall behind

| Finding | Evidence status | Consequence / next test |
|---|---|---|
| Published freshness disagrees with scan status | Confirmed on the supplied production endpoint | Validate successful scan-to-published-result identity and freshness before judging user-visible discovery. |
| Candidate history and source-matched exclusion receipts are unavailable in inspected local evidence | Confirmed evidence gap | Retain every normal scan's version/config, universe, selected symbols, gate reasons, archive receipt and actual publication time. Do not invent old journals. |
| Four-hour scan cadence | Confirmed in both status endpoints; concrete timing examples above | Test a bounded faster watch/re-evaluation layer separately from full entry screening. Quantify added API cost, coverage and false alerts. |
| Full derivatives enrichment is gated behind initial setups / limited anomaly probes | Confirmed in inspected code; current local log reports 22 Stage2 candidates | Missing historical OI observations are not the same as flat OI. Test whether early transitions deserve enrichment before a full archetype, using equal resource budgets. Not proven to explain a particular omitted winner. |
| Admission is bounded before full evaluation | Current local log: 504 liquid, 320 selected, 259 core plus 61 discovery | Attribute each miss to not-selected versus evaluated-and-rejected before modifying either stage. Increasing the cap alone is not established as the remedy. |
| Timeframe feature contracts disagree | Current inspected source assigns hourly ATR/RVOL to four-hour-labelled fields | Define intended semantics, retain legacy core, and test explicitly versioned alternative inputs. Do not silently substitute thresholds or claim this caused today's omissions. |
| Entry-readiness and future-leader discovery are different targets | Source archetype/gate path plus heterogeneous observed sequences; architectural inference | Add research watch states and measure transition lead time, persistence, rank capture, risk excursions and false positives rather than treating a readiness score as a calibrated leader probability. |

The local log also reports estimated request weight 1382 against its displayed budget_cap 1300, below the displayed exchange ceiling 2400. This is an estimated-accounting discrepancy to audit, not proof of exchange throttling or an exclusion cause. Log timestamps are naive local times; the 09:01 block is aligned to the independently observed 08:01 UTC scan, not treated as a cryptographically linked journal.

Source references: application/crypto_screener.py selection and Stage2 admission; its _score_accumulation_setup hourly ATR/RVOL mappings; api/portal.py published receipt fields; infrastructure/runtime.py archive writes. Fingerprints of the six inspected current-checkout files are retained. Those fingerprints do not attest the running deployment's revision.

## External causes

Bounded searches checked Janction, Elsa and Cap around November 4/5, then restricted project/exchange sources. No event-specific first-party announcement was verified as causing these moves. This is an evidence gap, not proof of no news.

Cap's first-party October 26 zero-deposit-fee post is background, not demonstrated November 4/5 causality:
https://www.cap.app/blog/introducing-zero-deposit-fees

Janction's official tokenomics and Binance's November 10, 2025 listing announcement establish context, not a fresh November 2026 catalyst:
https://docs.janction.ai/token/tokenomics
https://www.binance.com/en/support/announcement/detail/94c59a9ac9e34879ab618f8f34f8ba01

User posts on Binance Square and AI-written causal summaries were not accepted as first-party proof. No liquidation tape, historical spot flow, insider identity or authenticated institutional buying evidence was captured.

## Implementation order justified by this research

1. Close the scan-to-publication and runtime-version evidence gap using normal scheduled scan receipts; preserve existing history and verify the public endpoint. This is not another unchanged cohort presented as a strategy upgrade.
2. Complete the explicit feature-contract audit and regression suite, keeping legacy and intended alternatives separate.
3. Implement source-matched per-stage counterfactual diagnostics, then a versioned experimental watch track for early expansion, reclaim/recovery, and continuation/squeeze states. A watch must remain distinct from a qualified entry.
4. Compare frozen variants across broader, disjoint development and holdout periods with full non-winner denominators, consistent availability, equal API/selection budgets, liquidity, false-alert burden, lead time, MFE/MAE and publication delay.
5. Only then use prospective comparison as an acceptance test for a defined challenger. Do not promote rules because they would include these 14 winners.

This study establishes operational defects and testable architectural weaknesses. It does not establish that all moves were predictable, that a specific gate should be removed, or that an upgraded model will catch tomorrow's leaders.

## Reproduction and verification

Primary raw datasets are retained under docs/security/evidence/2026-11-05-*.
Verified analysis: 2026-11-05-gainers-bottleneck-analysis-verified.json.
Independent checks: 2026-11-05-gainers-independent-check.py and verification.json.
Publication responses: production-api-verified.json and local-api-verified.json.
Inspected sources and local budget/admission log excerpt are retained separately.

Run offline:

    python3 scripts/research_gainers_bottleneck_audit.py --evidence docs/security/evidence --output /private/tmp/new-gainers-audit.json

The output path must not exist; original outputs are never overwritten. The script makes no network calls and does not instantiate the screener, trading runtime or environment configuration.

Verification: all 62,640 hourly bars, 62,640 OI samples and 16,800 five-minute bars validated; the OI gap is explicit. Independent float/math.fsum calculations cross-checked 1,044 full-population feature/outcome rows and all 14 five-minute transition histories, reference selections and nominal scan timing. All 26 consumed-input hashes and the analysis script fingerprint matched. A second offline execution produced byte-identical output with SHA256 abef42c7b8af82dfac872eef146b16da7787b730339648461126d23230ccc655.

Ruff checks passed for the new research script. These are research/data checks, not a new full application suite, security audit, deployment validation, strict historical production replay or demonstrated predictive edge.

Official metric semantics:
https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data

