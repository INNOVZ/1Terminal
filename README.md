# Alfa Terminal — Atlas Crypto Screener

Atlas is a public-market cryptocurrency screener with charts, setup diagnostics, macro context,
and a persistent watchlist. Binance supplies public Spot and USD-M data. The application has no
exchange account access, API-key loading, order submission, paper broker, or position-management
runtime.

Institutional quality is the engineering and research target. The current scoring model is
uncalibrated research: grades, model EV, and legacy win-rate fields are not validated probabilities
or demonstrated performance.

## Run locally

Requirements: Python 3.12+, uv, and Node.js 22 for the frontend.

```bash
uv sync --frozen --extra dev
uv run atlas doctor
uv run atlas crypto-screen --mode all --max-symbols 250
uv run atlas portal
```

In a separate terminal:

```bash
cd frontend
npm ci
npm run dev
```

Copy .env.example if you need configuration overrides. No Binance keys are needed.
The public REST client permits only explicitly listed GET market-data endpoints.
Legacy trading settings in an existing .env file are ignored and cannot enable execution.

## Product and API

- `GET /api/screener/live`: current scan results; supports on-demand refresh.
- `POST /api/screener/rescan`: request a scan.
- `GET /api/chart/{symbol}`: public candles and analytical levels.
- `/api/watchlist`: user-managed research watchlist, including live audits.
- `GET /api/macro` and `GET /api/macro/overextension`: macro context.
- `GET /api/screener/cron/status`: configured schedule and observed scan count.
- `/healthz` and `/readyz`: process and runtime readiness.

Both `algotrader.api.portal_app:app` and the former `algotrader.api.app:app` now serve
the screener. Trading `/v1/*`, `/api/testnet/*`, and trading CLI commands are removed.
The default scan schedule is every four hours, one minute after the UTC candle boundary.
Entry/stop/target fields describe hypothetical research levels; they do not create orders.

The package/import name remains `algotrader` to avoid unnecessary deployment/import churn.
Offline simulations and their pure strategy/risk calculations remain available as research code,
without exchange execution adapters. Their results have not validated this screener.

## Reproducible scans and forward observations

New runtime scans archive normalized decision inputs, universe/stage decisions, configuration and
source fingerprints under `data/research/scans/SCAN_UUID/`. API, CLI and cron results include `scan_id`
and `scan_archive.status`; `saved` means the checksummed archive was published. `unavailable` means
recording failed: candidates are not changed, but that result must not be treated as durably archived.
Fingerprint/copy failures preserve screening results and may leave an explicitly incomplete input
archive; replay and outcome collection reject incomplete records. Provider errors are recorded
separately from recording errors and exhausted fetches are not reported as strategy rejections.
Cached reads do not create new scans. The existing watchlist and latest-results file are separate.
Set `ATLAS_RESEARCH_ARCHIVE_DIR` to change the research directory; it must be writable and backed up.

Replace `SCAN_UUID` below with the actual result's scan ID:

```bash
uv run atlas replay-scan --scan-id SCAN_UUID
uv run atlas observe-scan --scan-id SCAN_UUID --horizon-hours 24 --max-symbols 100
uv run atlas observe-scan --scan-id SCAN_UUID --horizon-hours 48 --max-symbols 100
```

Both commands accept `--archive-dir PATH`. Replay is offline and requires matching source/package
identity; keep the exact release source and dependencies. A Git revision alone is insufficient for
dirty checkouts. Archives contain normalized port values, not raw HTTP payloads or executable code.

Observation is a **manual public-data job**, not another scheduled scan or a trader. Its symbol limit
defaults to 100 (maximum 1000); it refuses a larger original cohort rather than truncate it. Review
the reported cohort size and request cost before explicitly increasing the limit. Each symbol needs
up to two minute-kline requests for 24h or three for 48h, before provider retries. Automatic outcome
scheduling and measured production load remain deferred.

Outcomes use the first minute open at/after scan completion and the exact subsequent 24h/48h path.
They report raw/directional price changes, high/low excursions and separate ambiguous touch diagnostics,
not fills or P&L. Missing/gapped/delisted data remains explicit, never a zero return. Ranking is within
the original eligible canonical cohort (preferring the exact liquid pair used by selection, with
an eligible fallback for bases without one), not Binance's changing rolling leaderboard. Partial coverage
cannot prove full-cohort top-gainer recall. Outcomes are append-only under `data/research/outcomes/`;
restarting skips successful compatible observations and retries missing ones without rewriting evidence.
New outcome calculation source produces a different measurement identity.

Disk capacity/retention, backup restore, full-cohort request cost and deployed permissions need operator
verification. No automatic deletion, deployment, strategy calibration or predictive claim is implied.

## Verification

```bash
uv run ruff check src
uv run mypy
uv run pytest
cd frontend
npm run lint
npm run build
```

CI runs these checks and a credential-free container startup test before deployment.
The Docker image excludes runtime data and historical trading artifacts.

## Current priorities

1. Ship the pure-screener migration and verify the existing deployment.
2. Correct data horizons and missing-data semantics; record reproducible snapshots.
3. Consolidate universe selection, acceptance rules, and versioned strategy parameters.
4. Measure forward outcomes and compare simple baselines before calibrating probabilities.
5. Harden access control, freshness, observability, backups, and release operations.
6. Reconsider a separate execution product only after the research supports it.

See [the research and ordered delivery plan](docs/screener-research-roadmap.md),
[current architecture](docs/architecture.md), and
[deployment migration](docs/gcp-deployment.md).
The old [trading architecture](docs/archive/algorithmic-trading-architecture.md) is historical.
[Research baseline](docs/research-baseline.md) records earlier experiments, not evidence that
the current screener has an edge. AGENT.md and previously generated PDFs describe older strategy
versions and must not be treated as proof of production readiness.

## Manual shadow discovery research

### Opt-in prospective collection

The separate research runner enrolls one archived child cohort without rescanning or
changing live candidates. Enrollment, status, stop and health commands are explicit;
shipping the code does not enable collection. The original child anchor fixes the
24h/48h outcome windows, including across restarts. Two isolated roles collect public
rolling-rank snapshots and bounded historical outcomes; reports retain incomplete
coverage and unknown gaps rather than inventing observations.

Deployment controls, resource limits, activation and recovery procedures are in
[the dedicated research VM instructions](docs/gcp-deployment.md#dedicated-opt-in-research-vm-2026-10-26).
Local implementation and synthetic acceptance are not proof of live collection or
predictive performance. Collection requires an always-on host and explicit activation;
the live screener API and Vercel are not changed by the research-only deployment.

The opt-in shadow runner adds independently admitted long observations without changing the live
core, grades, brackets, shorts or desk slots. It is not predictive validation or automatic trading.
Use a fresh, complete, production-quality ALL scan archived by the same source/dependency version;
diagnostic scans, stale parents and mismatched identities are rejected. Run from this checkout:

```bash
uv run atlas crypto-screen --mode all --min-score 68 --max-symbols 250
uv run atlas shadow-scan --scan-id CORE_UUID --archive-dir data/research
uv run atlas replay-shadow --shadow-id CHILD_UUID --archive-dir data/research
uv run atlas observe-shadow --shadow-id CHILD_UUID --archive-dir data/research --horizon-hours 24 --max-symbols 1000
```

Start the child within 300 seconds of parent completion. Each discovery run permits at most 15
additional tokens, 150 actual HTTP attempts including retries and 90 seconds of work. Deterministic
buckets cover quote anomalies, positive-momentum controls and exploratory outside-shortlist probes.
This is bounded sampling, not a full-universe OI/depth scan. `early_candidate` is a hypothesis, not a
probability or promise; unknown/stale cohort ranks cannot earn that label.
Stale/empty macro context and degraded/stale derivatives make the run partial, not a negative result.
Macro collection must be <=5 minutes old, supplied daily components <=48 hours old, and derivatives
<=60 seconds old (five-second future tolerance). Successful empty weekly history uses the core's
resampling fallback and is labelled as derived evidence; failed provider requests remain failures.

Child records live under `shadow/`; fixed 24h/48h observations live under `shadow-outcomes/`, using
the original parent cohort and the child's own next-minute anchor. Parent outcomes, latest scan data
and published opportunities are not overwritten. Comparisons are labelled `staggered_shadow` because
the child has newer information. Price paths are not fills/P&L or leaderboard-entry lead time.
The outcome command refuses an insufficient symbol limit rather than truncating the cohort.
Nothing schedules these commands automatically. Production load, backup/restore, UI integration and
deployment remain separate follow-up work.

## Prospective ranks and offline benchmarking

Manual rank collection uses the original archived USD-M cohort, including old complete parents.
Unlike creating a shadow child, collecting later ranks does not require a fresh parent or current
scoring-source identity. It never reruns scoring or changes the published desk.
The existing replay/shadow-creation compatibility guard still hashes the entire Python package:
adding research modules changes that software identity even when the core rules are unchanged.
Old archives remain readable and rank-observable; replaying old scoring still requires its matching
source version. These commands do not weaken that guard or rewrite old archives.

```bash
uv run atlas snapshot-ranks --scan-id CORE_UUID --archive-dir data/research
uv run atlas benchmark-shadow --shadow-id CHILD_UUID --horizon-hours 24 --archive-dir data/research
# Optional fixed cutoff for reproducible reruns; include a timezone:
uv run atlas benchmark-shadow --shadow-id CHILD_UUID --horizon-hours 48 --archive-dir data/research --as-of 2026-10-25T12:00:00Z
```

Each capture permits at most three actual HTTP attempts and a 15-second acquisition deadline,
including retries/backoff, using only the public bulk rolling-24h ticker endpoint. Exchange weight
is reported separately from attempt count; independent invocations do not share a global limiter.
Capture manually near five-minute intervals if researching membership: ten-minute maximum gaps and
both horizon boundaries are required for sufficient sampled coverage. No scheduler is installed.
Incomplete cohorts, stale/malformed/duplicate rows, failed attempts and sampling gaps remain explicit.

Immutable snapshots live under `ranks/CORE_UUID/SNAPSHOT_UUID/`; report manifests live under
`benchmarks/CORE_UUID/REPORT_UUID/`. Reports are offline and use compatible existing `observe-shadow`
outcomes only. Missing paths are unavailable, never fetched silently. The three frozen sets are core
longs, qualified child longs and a volume-filtered five-symbol momentum reference. Shorts remain
separate; small sets cannot produce a retrospectively resized top-five comparison.

Rolling top10 membership (including ties, possibly negative returns) differs from fixed-forward
24h/48h cohort ranks. `first_observed_membership` is a sample observation, not actual first entry,
breakout time or guaranteed lead time. Comparisons share the child's forward anchor but disclose
older core information as `matched_forward_anchor_staggered_decisions`. No causal benefit,
predictive edge, calibrated probability or deployment readiness is established by this tooling.
