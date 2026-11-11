# Historical research on the November 8–10 prospective study

## Scope and decision

Study `71346822-f686-4bb1-99ec-0f56ef8f0ea1` ran from November 8 at 13:50 UTC through
November 10 at 13:50 UTC. The original scan was issued November 8 at 13:45:09 UTC.
Its 24h and 48h reports share one cohort and one initial forecast.

This investigation recovers historical market paths, checks the original selection
decisions, compares later gainers with a reproducible comparison sample, and evaluates
which screener changes the evidence supports. Production strategy, services, study
enrollment and collection controls received no changes during this investigation.

The evidence supports improvements to coverage, feature definitions and temporal discovery.
It provides exploratory evidence for research on relative-strength transitions. It leaves
the predictive performance of a revised screener unestablished.

## Data recovered and verified

- A bounded, checksummed outcome summary for all 512 frozen cohort members was read from
  the approved alfa2 VM. Definition SHA256 is
  `ba2422c1ca5ed43e25e79b1b27faa48baede81588998681929dc3029af147a70`.
  The verified 48h report SHA256 remains
  `c193f41b281428f463f9a69770e0288823357a82bb427ad3313a3bb40404c287`.
- Historical inputs cover 134 symbols. They include all 55 initially-outside later
  top-ten entrants, all ten momentum-reference members, the three unique publications,
  64 reproducibly sampled non-entrants, and BTC/ETH benchmarks.
- Public Binance histories contain 53,332 hourly candles, 27,068 four-hour candles,
  53,332 hourly OI observations, and 12,780 funding settlements.
- Hourly/OI/funding requests cover October 25 through November 10 at 13:50 UTC.
  Four-hour requests start October 8 to provide a longer EMA warm-up.
- All 268 raw response files have SHA256 entries in the manifest. Response text and
  structured representations agree. Hourly timestamps are unique, ordered and continuous.
  Complete overlapping 4H candles agree with aggregated hourly OHLC and base volume.
- Eleven archived diagnostic cases reproduce their original closed scan price,
  legacy contraction ratio and legacy RVOL field within floating-point tolerance.
  The reconstructed 4H EMA50 differs from the archived values by at most 0.033% because
  this research uses a shorter history than the production 480-bar request.

The comparison sample uses the 64 lowest SHA256 values of
`atlas-nov8-controls-v1|SYMBOL` among 433 members that were initially certified outside,
had no observed top-ten entry, and were separate from the BTC/ETH benchmarks. Sampling
does not filter for negative returns. Unknown initial memberships are excluded from this
comparison population. The sample is descriptive and belongs to the same market episode.

These histories were retrieved retrospectively on November 11. Their historical timestamps
allow closed-bar analysis, while original acquisition/availability receipts exist only
for the captured study inputs. Historical OI publication latency is unarchived. Calculations
therefore include a one-hour-lagged OI sensitivity field. Funding histories contain realized
settlements, which differ from the contemporaneous predicted funding used by the engine.

## How performance before entry was measured

For every symbol, the analysis retains features at the original scan time and hourly
observations throughout the study. Event comparisons use the start of the hour containing
the first certified top-ten sample, and offsets of 1, 4, 6, 12 and 24 hours before that
boundary. Every candle used ends before the feature cutoff. The overlapping entry-hour
candle is excluded. Controls are measured at each entrant's same UTC cutoff.

The first certified sample gives an upper-bound observation time. Unknown ranks and the
single missed sample prevent certifying the exact moment of first membership. The hourly
cutoffs also provide coarser timing than the original five-minute samples.

Six-hour volume acceleration divides mean quote turnover in the last six closed hours by
mean turnover in the preceding 18 hours. Six-hour relative strength subtracts BTC's return
over the same window. Taker imbalance uses actual taker-buy quote volume divided by
remaining quote volume. OI changes use contract units, avoiding the mechanical price effect
in dollar OI. These are explicitly defined research features.

## 1. Selection coverage has two separate constraints

Exact offline execution of the original selection stage reproduced the 48-symbol selection.
Execution stopped before any candle request. The larger-budget result also reproduces
the earlier selection-only counterfactual.

|Budget ceiling|Core allocation|Discovery allocation|Actually selected|Later new entrants selected|
|---|---:|---:|---:|---:|
|48|38|10|48|10/55|
|320|259|61|270|38/55|

The original `selection_limit` journal label groups several mechanisms together. At the
48 ceiling, 42 later entrants were liquid but unselected, three failed earlier liquidity
eligibility, and ten were evaluated but unpublished. Increasing the ceiling admits 28 more
later entrants to evaluation. Fourteen remain outside the core allocation and fail the
discovery tier's separate $1.5m 24h turnover requirement. None of those fourteen breaches
the discovery tier's 25-bps spread threshold in the recorded quotes.

|Symbol|Recorded 24h turnover|Recorded spread|Liquidity rank|Remaining 320-budget exclusion|
|---|---:|---:|---:|---|
|MERLUSDT|$1,388,157.59|6.86 bps|288|Discovery turnover below $1.5m|
|TSTUSDT|$830,609.65|8.59 bps|368|Discovery turnover below $1.5m|
|1000RATSUSDT|$1,049,962.98|7.18 bps|328|Discovery turnover below $1.5m|
|BSBUSDT|$1,343,494.25|3.41 bps|293|Discovery turnover below $1.5m|

The other ten are ACU, AVAAI, ENJ, FIGHT, GPS, LUMIA, NXPC, SCR, SQD and USUAL.
JOE, KERNEL and PHAROS were below the earlier $500k turnover floor, at approximately
$332k, $364k and $443k respectively.

This establishes a discovery coverage constraint even when the configured budget has
spare capacity. Tight quoted spreads alone do not establish adequate depth or affordable
execution. The archive lacks historical depth and executable impact estimates.

## 2. Most entrants had weak initial structure, and their signals evolved

With the longer four-hour history, 36/55 later entrants were below reconstructed 4H EMA50
at the initial scan. This refines the preliminary 37/55 count computed from aggregated
hourly history. Nineteen were above the average, compared with 6/64 comparison symbols.

|Condition at original scan|Later entrants|Comparison symbols|
|---|---:|---:|
|Above reconstructed 4H EMA50|19/55|6/64|
|Positive six-hour strength relative to BTC|21/55|13/64|
|Six-hour turnover acceleration above 1x|27/55|31/64|
|Six-hour turnover acceleration at least 2x|8/55|3/64|
|Positive six-hour OI change|24/55|31/64|
|Six-hour taker-buy/sell ratio above 1|21/55|21/64|

At the scan, median six-hour return was −0.83%, median relative strength versus BTC was
−1.01 percentage points, and median OI change was −0.14%. Median time from scan issuance
to first observed top-ten entry was about 25.9 hours. A single initial frame misses substantial
subsequent evolution.

Short-horizon strength becomes clearer closer to membership.

|Hours before entry-hour boundary|Median entrant 6h strength versus BTC|Median same-time control strength|Entrants above same-time control median|
|---|---:|---:|---:|
|24|−0.98 pp|−0.54 pp|18/55|
|12|−0.04 pp|−0.44 pp|33/55|
|6|+1.11 pp|−0.26 pp|37/55|
|4|+0.93 pp|−0.45 pp|37/55|
|1|+2.49 pp|+0.43 pp|40/55|
|Entry-hour boundary|+4.52 pp|+0.59 pp|50/55|

Immediately before the entry-hour boundary, 49/55 had positive six-hour BTC-relative
strength and 47/55 had positive six-hour price returns. However, the median preceding
24h return was already +6.54%. Strength measured close to the leaderboard is partly
related to the return-based label itself. The six-hour lead result merits prospective
testing, while the 24h lead result shows little advance separation in this episode.

The controls are reused across event times. These comparisons are correlated observations.
They provide exploratory feature evidence without an independent validation period.

## 3. Volume, OI and buying pressure were heterogeneous

Before the entry-hour boundary, 39/55 entrants had rising six-hour turnover, 15/55 reached
2x acceleration, 34/55 had increasing OI, and 27/55 had more taker buys than taker sells.
A strict conjunction would exclude many entrants. At the initial scan, rising OI occurred
more frequently in the comparison sample than among later entrants.

Price rising with increasing OI is compatible with new leveraged participation. Price
rising with decreasing OI can include position closure. Negative funding describes a
contract's financing imbalance. None independently identifies the buyer, proves a short
squeeze, or establishes beneficial accumulation. Historical liquidation records and full
cross-venue spot flows were unavailable in this study.

Two examples show how quickly the evidence can change.

- CYBER's largest positive hour began November 10 at 11:00 UTC. It gained 26.42% during
  that hour and OI units rose 54.08%. Before the hour, six-hour turnover acceleration was
  0.95x and six-hour OI change was −0.64%. The first certified membership sample was
  11:45, inside the impulse hour. The large contemporaneous OI change cannot be used as
  an advance feature at 11:00.
- INIT's largest positive hour began November 10 at 04:00 UTC and gained 17.34%. OI
  rose 9.21% during the hour. Before it, six-hour price return was −7.40%, turnover was
  3.42x its prior baseline and OI was up 12.25%. The last settled funding was about
  −101.76 bps. INIT had already appeared in the observed top ten on November 9 at 08:05.
  These figures help describe a volatile continuation, while its initial discovery is a
  separate event.

The script also records every case's largest up hour. This event is chosen retrospectively
for explanation. Across the 55 entrants, 30 such hours ended before first observed
membership, 12 overlapped it, and 13 began afterward.

## 4. Individual paths require different interpretations

|Symbol|At original scan|Before first observed entry|48h anchor return|Implication|
|---|---|---|---:|---|
|INIT|6h +2.05%, turnover 1.53x, OI −0.82%, below 4H EMA50|6h +2.82%, turnover 2.37x, OI −1.60%|+82.66%|Recovery/transition deserves study alongside compression patterns|
|ARIA|6h −4.14%, already about 24% above 4H EMA50|6h +1.41%, turnover 1.19x, OI −3.13%|+25.98%|Extended continuation has different entry risk from a quiet coil|
|EDGE|6h −0.60%, turnover 4.04x, OI +1.20%|6h +7.17%, turnover 1.03x, OI +2.77%|+19.35%|Activity was present at scan, with stronger price confirmation later|
|MERL|6h +1.02%, turnover 0.79x, OI −0.84%|6h +8.92%, turnover 5.81x, OI +12.75%|+10.61%|Static discovery liquidity eligibility blocked evaluation before activity expanded|
|NIGHT|Structural setup passed, enriched L6 ignition failed|6h +6.30%, turnover 0.67x, OI −1.39%|+6.39%|Structural readiness and activation deserve distinct research states|
|IDOL|6h −10.22%, already about 34% above 4H EMA50, crowded funding veto|6h +13.04%, OI +0.96%|+0.77%|Expansion/re-entry timing matters, with limited lasting endpoint gain|
|ON|6h +2.49%, turnover 0.86x|6h +3.95%, turnover 1.55x|−34.42%|Large temporary gains can reverse sharply|

The table's features use closed-hour research cutoffs. The exact archived gate diagnoses
remain those in the November 10 replay report. In particular, NIGHT's archived legacy
RVOL field was 1.1923 and its enriched ignition policy failed. A comparable actual 4H
inclusive-20-bar RVOL is about 0.6561. Switching timeframe definitions would materially
change its interpretation.

Only 37/55 newly observed entrants ended positive at 48h. ON was +54.00% at the 24h
anchor endpoint and −34.42% at 48h. AIN peaked +12.32% in sampled prices, had a −46.69%
sampled minimum and finished −28.35%. Their exclusion cannot automatically be counted
as a missed desirable long setup.

## 5. Catalyst research and chronology

Primary-source searches concentrated on INIT, NIGHT, IDOL, PIEVERSE, ARIA, EDGE, MERL,
CYBER, ON and exchange-wide conditions. A verified dated cause was not established for
every entrant. The numerical analysis covers every entrant, while catalyst attribution
remains incomplete.

- Midnight published its Ledger 9 mainnet upgrade announcement on November 6, before
  the scan. This establishes a contemporaneous project event. It does not establish
  that the upgrade caused NIGHT's later return. [Midnight announcement](https://midnight.network/blog/ledger-9-upgrade-on-mainnet)
- KuCoin's IDOL announcement specifies spot trading from November 3 at 10:00 UTC.
  That listing preceded the study by five days. It provides event context without
  identifying a new November 9 trigger. [KuCoin listing](https://www.kucoin.com/announcement/fr-meet48-idol-listed-on-kucoin)
- Binance specified a BULLA mark-price protection transition at November 10 12:30 UTC
  and a subsequent funding cap change. BULLA first appeared in the observed top ten at
  06:20 that day. The later effective change cannot explain that earlier appearance.
  The notice supports recording contract-parameter history when interpreting funding.
  [Binance notice](https://www.binance.com/en/support/announcement/detail/f7e499194e0748f593f973f1d1959ea5)
- Binance's November 6 recovery notice scheduled progressive withdrawal reopening for
  several assets, including CYBER and HAEDAL. This was an unusual venue operating
  environment. The notice establishes context, while the effect on each token's price
  remains unmeasured. [Binance recovery update](https://www.binance.com/en/support/announcement/detail/a2dd10b860a14c35a856fe9d66d6032c)
- INIT statements attributed to the project describe a November 9 rollup infrastructure
  issue and a November 10 L1 halt. Direct X retrieval returned 403. A mirror exposes
  attributed text and dates, with inconsistent timing across secondary reporting.
  Its initial observed top-ten entry at November 9 08:05 predates the mirror's evening
  announcement and the November 10 halt. These later events cannot be treated as
  advance evidence for that initial entry. They also leave the rally's precise cause
  unresolved. [Attributed November 9 post](https://teapawt.net/initia/status/2119875372183703612), [Attributed November 10 post](https://teapawt.net/initia/status/2120050740626628972)
- PIEVERSE's reported AI-workspace teaser could not be confirmed through a dated project
  announcement. The inspected November 10 CMC analysis itself identifies the confirmation
  gap and was published after the study's 48h endpoint. The teaser is excluded from
  accepted advance catalyst evidence. [Secondary claim and stated uncertainty](https://coinmarketcap.com/cmc-ai/pieverse/price-analysis/)
- edgeX publishes a revenue-funded buyback mechanism. Its current page provides no
  verified November 8–10 purchase schedule used here. A mechanism alone cannot explain
  EDGE's specific rally. [edgeX tokenomics](https://pro.edgex.exchange/en-US/tokenomics)

No dated, primary-source trigger was established here for ARIA, MERL or CYBER's impulse.
News narratives, whale purchases and manipulation claims remain outside confirmed causes.

## 6. Changes supported by this investigation

### Established implementation and measurement work

1. Repair sampler/report coupling, lightweight health and observed/missed/failed accounting.
   This follows the actual missed study slot and report delays.
2. Make the capture budget explicit and persist separate core/discovery selected counts.
   Preserve typed exclusions for base liquidity, discovery liquidity, anomaly qualification,
   quota and downstream gates. The current shared selection label hides actionable causes.
3. Version and verify feature semantics before score calibration. The legacy RVOL field
   contains 1H RVOL. Its 7/14-day contraction request uses about 239 closed hourly candles.
   With complete 336-hour history, membership in the illustrative 0.45–0.85 contraction
   interval changes for 16/55 entrants and 12/64 controls. This calculation describes
   a feature boundary change and does not reproduce full publication decisions.
4. Archive periodic stage decisions and feature trajectories so subsequent four-hour scans
   can be evaluated directly. This completed trial records one frozen initial forecast.

### Strategy hypotheses to implement as controlled research

1. Give the existing discovery track a bounded research watch pool for qualifying emerging
   activity below its $1.5m turnover floor. Track turnover acceleration and liquidity
   transitions while retaining explicit liquidity/risk requirements for actionable setups.
   Compare against the unchanged selector. Do not choose the floor from MERL's exact value.
2. Test transitions in relative strength, volume and structure across repeated scans.
   Relative strength showed exploratory separation around six hours before observed entry.
   Fix lead horizons and independent event labels before evaluation to reduce leaderboard
   label circularity. Score a whole eligible comparison cohort prospectively.
3. Represent structural readiness separately from activation within discovery research.
   NIGHT motivates that hypothesis. INIT motivates examining recovery transitions.
   Keep crowding, invalidation and anti-chasing evidence explicit. A readiness observation
   should not imply an executable trade.
4. Evaluate interactions and regimes before enforcing rising OI or taker buying as universal
   conditions. These single conditions lacked broad standalone separation in this episode.
5. Add dated venue/project events as research metadata with first-seen timestamps and
   source confidence. Measure whether they add information before assigning score weight.

Validation should retain current core and funding-carry arms as reproducible controls,
freeze candidate budgets and alert rules, and measure lead time, evaluation/publication
recall, alert load, adverse excursion, time to invalidation, and 24h/48h outcomes across
multiple periods. The primary top-ten label and positive opportunity quality should each
have explicit definitions. Fees, depth and fills remain unmeasured in this screener study.

## Reproduction and remaining evidence gaps

Run `python3 scripts/research_study_pre_gainer_analysis.py` from the repository root.
The script reads retained responses, validates them, cross-checks archived features and
rebuilds JSON, CSV and the checksummed manifest without network access.

The analysis JSON includes all case trajectories and event-lag features. The CSV provides
initial/pre-entry comparisons. Selection diagnostics reproduce the initial selection at
both budgets using the original frozen image and capture, with networking disabled.
Original archives stayed on the VM. Temporary OS Login keys were removed successfully
after both bounded reads, and local private/public key files were deleted.

The evidence cannot recover historical order-book depth, complete liquidation paths,
wallet-level buyer intent, original publication latency for retrospectively retrieved OI,
or decisions from unarchived later production scans. Exact causal attribution for every
gainer remains incomplete. Numerical historical reconstruction supplies hypotheses and
verified implementation defects. A revised engine's predictive improvement requires
evaluation against prospectively frozen rules.
