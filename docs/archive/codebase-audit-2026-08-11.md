# Atlas codebase audit — security, robustness, duplication and logic

Date: 2026-08-11

## Remediation update

The following changes were implemented after the original review and rechecked:

- **ROB-001 closed:** SQLite transactions now deterministically close every connection. The test
  suite permanently treats `ResourceWarning` and Pytest unraisable exceptions as failures.
- **ROB-002 closed:** schema version 2 performs additive upgrades for existing version-1 volumes,
  records the new version, rejects future/unsupported schemas, and has an upgrade-fixture test.
- **ROB-003 substantially mitigated:** research/event retention and report lookback are configurable
  and bounded; forward-price lookup changed from quadratic scanning to indexed binary search.
- **TRD-002 risk contained:** the train/holdout boundary uses unique timestamps plus a purge covering
  the 24-hour label and two-hour tolerance. Empty result sets fail the gate, and human attestation can
  only nominate governed review—`eligible_for_promotion` remains false until an immutable experiment
  registry exists.
- **TRD-003 partially closed:** research profiles fail closed without BTC beta/correlation, and the
  candle replay now receives aligned historical BTC candles. Aggregate shared-capital portfolio beta
  and pairwise-cluster replay remain future work.
- **ROB-004/005/006 and MAINT-002 closed:** `Retry-After` is parsed defensively and capped, shutdown
  attempts all component closes, Futures protection uses an explicit invariant instead of `assert`,
  and screener capability detection no longer depends on exception text.

Post-remediation verification: 223 tests passed with resource warnings promoted to errors; Ruff and
strict MyPy passed. Bandit reported no issues, `pip-audit` found no known third-party dependency
vulnerabilities, source coverage is 79%, and both the source distribution and wheel build. The
live-capital decision remains **not approved** because TRD-001, SEC-001 and SEC-002 remain open, and
TRD-002/TRD-003 still require institutional research infrastructure.

Recreated checks on the final tree:

- pytest: 223 passed in 1.46 seconds.
- Ruff: passed.
- strict MyPy: passed for all 38 source files.
- Coverage: 79% for production source (86% when tests are included in the measured tree).
- Bandit: clean, exit 0.
- `pip-audit`: no known vulnerable third-party dependency; the local package was skipped because it
  is not published on PyPI.
- Vulture: only four false positives for required Pydantic validator `cls` parameters.
- Radon: overall average A (3.99); two F hotspots remain in screener and trend-quality scoring.
- Pylint duplicate-code: eight localized similarities, primarily venue adapter symmetry and small
  orchestration/serialization patterns; no security or correctness defect was identified.
- Packaging: `atlas_algotrader-0.1.0.tar.gz` and `atlas_algotrader-0.1.0-py3-none-any.whl` built.

## Executive decision

Atlas has a strong safety-oriented foundation for local paper trading and a supervised,
single-instance GCP testnet deployment. Authentication, live-mode interlocks, conservative order
retry behavior, exchange-side protection, parameterized SQL values, non-root container execution,
strict configuration validation and a durable kill switch are all implemented well.

It is **not approved for unattended live capital**. The SQLite lifecycle, versioned migration,
bounded-storage, split-leakage and false-promotion defects found in the original review are now
remediated or contained. The remaining high-risk gaps are continuous venue-side protection
reconciliation, externally verified API-key least privilege, an immutable experiment registry,
shared-capital portfolio replay, and demonstrated positive strategy expectancy.

Recommended release status:

- Local paper: approved at the code-quality level; continue measuring forward paper results.
- Single-node GCP paper/testnet: conditionally approved; keep the API private, use one replica and
  retain operator supervision.
- Automated strategy promotion: not approved until TRD-001, TRD-002 and TRD-003 are closed.
- Live capital: not approved until every High finding is closed and the strategy passes a governed,
  portfolio-level out-of-sample gate.
- Multiple replicas/HA: not approved until the SQLite lease is replaced by a fenced authority model.

## Scope and evidence

Reviewed the Python 3.12/3.13, FastAPI, Pydantic, httpx, asyncio, SQLite, Binance adapter,
screening/alpha, risk, execution, reconciliation, backtest, Docker/Compose and GCP-oriented paths.
The local `.env` was not read; only its permission mode was checked (`0600`). No production code was
changed as part of this review.

Original pre-remediation verification performed:

- Ruff: passed.
- strict MyPy: passed for all 38 source files.
- pytest: 208 passed.
- Coverage: 78% total; important gaps are detailed in TEST-001.
- `pip-audit`: no known vulnerable third-party dependency; the local project package is naturally
  not present on PyPI and was skipped.
- Bandit: no High finding. Its three SQL warnings are false positives because interpolated column
  names are a code-owned constant and all values remain bound parameters.
- Vulture: no confirmed dead production code. Its four findings are required Pydantic validator
  `cls` parameters.
- Radon: average cyclomatic complexity A (3.91), with several severe function-level hotspots.
- Pylint duplicate-code: one small Spot/USD-M order-parameter serialization clone.
- Package verification: source distribution and wheel built successfully.
- Secret/sink search: no committed-looking credential assignment, command execution, dynamic
  evaluation, pickle use, arbitrary file serving, permissive CORS, user-controlled outbound URL,
  unsafe redirect, or unparameterized SQL value was found.

## High findings

### ROB-001 — SQLite connections are not explicitly closed

- **Status:** Closed by the remediation update above.
- **Rule ID:** PYTHON-SQLITE-LIFECYCLE-001
- **Severity:** High
- **Location:** `src/algotrader/infrastructure/sqlite_state.py:52-58`; 30 usages including
  `src/algotrader/infrastructure/sqlite_state.py:71-72` and `:947-956`
- **Evidence:** `_connect()` returns a raw `sqlite3.Connection`, and every repository operation uses
  `with self._connect() as connection`. A SQLite connection's context manager commits or rolls back;
  it does not close the connection. The complete coverage run emitted 433 resource warnings, and a
  focused state-repository run repeatedly reported `ResourceWarning: unclosed database`.
- **Impact:** Long-running hourly automation creates many connections and relies on garbage
  collection for cleanup. File-descriptor pressure, delayed WAL cleanup, intermittent lock behavior,
  or state-store failure can halt the trader at an unsafe operational moment.
- **Required fix:** Make `_connect()` a real closing context manager, or wrap each connection in
  `contextlib.closing`, while retaining transactional commit/rollback semantics. Add a test that
  treats unraisable/resource warnings as failures and exercises repeated repository operations.
- **Mitigation:** The repository serializes operations with an asyncio lock and uses a five-second
  busy timeout, but neither closes resources.
- **False-positive notes:** CPython garbage collection eventually closes many connections. Eventual
  cleanup is not deterministic lifecycle management and the warnings prove the defect is real.

### TRD-001 — No strategy profile has demonstrated positive net expectancy

- **Rule ID:** ATLAS-TRADING-EDGE-001
- **Severity:** High
- **Location:** `docs/research-baseline.md`; `src/algotrader/config.py:104-125`
- **Evidence:** Existing recorded diagnostics for legacy momentum, regime breakout, and trend-quality
  profiles are negative after modeled costs. The more recent AVAX doubled-cost diagnostic also
  remained negative. The code correctly calls newer profiles research-only, but configuration can
  still select them.
- **Impact:** Enabling unattended/live trading would automate a hypothesis that has not earned the
  right to use capital. A target win rate is not evidence; expectancy, profit factor, drawdown and
  stability after costs are the approval measures.
- **Required fix:** Keep gathering forward observations, then use point-in-time universes,
  delisting-aware data, shared-capital portfolio replay, walk-forward/purged validation, and cost
  stress. Promotion requires positive expectancy and profit factor with acceptable drawdown across
  regimes and untouched holdouts.
- **Mitigation:** Paper is the default and automation is disabled by default.

### TRD-002 — “Untouched holdout” is not technically enforced or independent

- **Status:** Leakage and false automated approval are closed; immutable holdout registry remains.
- **Rule ID:** ATLAS-RESEARCH-GOVERNANCE-001
- **Severity:** High
- **Location:** `src/algotrader/application/research.py:178-224`, `:259-266`;
  `src/algotrader/cli.py:347-386`, `:552-555`
- **Evidence:** The feature report splits by labelled row count, not by a unique timestamp boundary,
  and applies no purge/embargo for the 24-hour forward label. Long/short and cross-venue observations
  from the same market timestamp can therefore fall on opposite sides of the split. The backtest
  promotion gate accepts `--untouched-holdout` as a human Boolean assertion; there is no immutable
  experiment registry, holdout identifier, prior-access record, or one-time unlock. Programmatic
  callers can also pass an empty interval tuple, for which `all([])` makes the positivity check true.
- **Impact:** Correlated or previously inspected data can be labelled “untouched,” producing false
  confidence and potentially promoting an overfit strategy to live execution.
- **Required fix:** Split on unique decision timestamps, purge at least the maximum forward horizon
  around the boundary, add an embargo, and persist immutable dataset/feature/config/code hashes.
  Make holdout access a controlled final evaluation, require a non-empty result set, and record every
  evaluation so a viewed holdout cannot be reused as untouched.
- **Mitigation:** The current split is chronological and does not tune a sign inside the holdout.
  That reduces leakage but does not establish independence.

### SEC-001 — Open-position protection is not continuously proven against venue state

- **Rule ID:** ATLAS-EXECUTION-RECONCILIATION-001
- **Severity:** High
- **Location:** `src/algotrader/application/service.py:310-370`, `:936-1007`;
  `src/algotrader/application/manage.py:85-120`, `:389-393`
- **Evidence:** Every cycle compares local trades with venue positions, but it does not verify that
  every open position still has the exact required stop/target quantity and geometry unless a
  management change triggers protection replacement. There is no sequence-aware Binance user-data
  replay.
- **Impact:** Manual cancellation, a missed venue event, partial fill, account reset, or protection
  cancellation can leave exposure whose safety is not proven.
- **Required fix:** Add startup and recurring REST snapshots plus sequence-aware user-stream replay.
  Reconcile positions, open orders, partial fills and protective geometry on every cycle and fail
  closed on any orphan or gap.
- **Mitigation:** Initial protection is stop-first, exchange-side, validated and durable; position
  quantity mismatches activate the kill switch.

### SEC-002 — API-key least privilege cannot be verified by application code

- **Rule ID:** ATLAS-SECRETS-LEAST-PRIVILEGE-001
- **Severity:** High
- **Location:** `src/algotrader/infrastructure/runtime.py:86-152`; deployment/account configuration
- **Evidence:** Startup verifies that signed account endpoints accept the keys, but cannot establish
  that withdrawals are disabled, IP restrictions exist, keys are isolated to a limited-balance
  subaccount, or Secret Manager rotation is configured.
- **Impact:** Theft of an over-privileged key could permit unauthorized trades and possibly direct
  asset withdrawal.
- **Required fix:** Use GCP Secret Manager, reserved-egress IP allowlisting, trade-only permissions,
  withdrawals disabled, limited-balance account isolation, key rotation and a recorded operational
  control check.
- **Mitigation:** Secrets use `SecretStr`, `.env` is excluded from Docker context and owner-only, no
  withdrawal endpoint exists, and credentials are not persisted to SQLite.

## Medium findings

### ROB-002 — `schema_version` is decorative; persistent-state upgrades are unmanaged

- **Status:** Closed by schema version 2 and the version-1 upgrade test.
- **Rule ID:** ATLAS-SCHEMA-MIGRATION-001
- **Severity:** Medium
- **Location:** `src/algotrader/infrastructure/sqlite_state.py:71-83`, `:151-193`
- **Evidence:** Startup runs only `CREATE TABLE IF NOT EXISTS` and always inserts schema version `1`.
  It never reads the current version or performs ordered migrations. `CREATE TABLE IF NOT EXISTS`
  cannot add a newly required column to an existing table. Tests create fresh databases and do not
  verify an upgrade from an older schema.
- **Impact:** A container update against a persistent GCP volume can start with an old table shape
  and fail later during execution, management, or reconciliation.
- **Required fix:** Add transactional, monotonic migrations with pre/post schema assertions, backup
  and rollback instructions, and tests that upgrade every supported prior schema fixture.
- **Mitigation:** Newly added whole tables are created safely; the current local database has the
  latest expected columns.

### ROB-003 — Research/event storage and reporting are unbounded

- **Status:** Bounded retention/lookback and indexed labelling implemented; external analytics
  storage remains recommended.
- **Rule ID:** ATLAS-STATE-RETENTION-001
- **Severity:** Medium
- **Location:** `src/algotrader/infrastructure/sqlite_state.py:97-103`, `:167-187`, `:243-284`,
  `:947-956`; `src/algotrader/application/research.py:192-224`;
  `src/algotrader/cli.py:450-455`
- **Evidence:** Events and hourly research opportunities have no TTL, archive, partition or pruning
  operation. `research-report` reads every opportunity into memory. Labelling then scans each
  instrument's observation list from its beginning for every row, making the forward-label lookup
  quadratic. At the configured universe size, annual observations can approach one million rows.
- **Impact:** The state disk and report memory/runtime grow without bound; a full disk can make the
  same SQLite database used for execution safety unavailable.
- **Required fix:** Separate operational state from analytical storage; stream/archive research rows
  to Parquet/object storage or PostgreSQL, add retention and disk-watermark alarms, page database
  reads, and replace the nested search with indexed/two-pointer timestamp lookup.
- **Mitigation:** Opportunity writes are hourly-idempotent and indexed by time/symbol.

### TRD-003 — BTC exposure controls fail open and are not validated by portfolio replay

- **Status:** Missing data now fails closed and single-symbol replay receives BTC history; true
  shared-capital portfolio replay remains open.
- **Rule ID:** ATLAS-PORTFOLIO-BETA-001
- **Severity:** Medium
- **Location:** `src/algotrader/application/screener.py:98-105`, `:151-184`, `:343-363`;
  `src/algotrader/application/risk.py:73-130`;
  `src/algotrader/application/backtest.py:281-357`
- **Evidence:** Benchmark-fetch failure yields `btc_beta=None` and `btc_correlation=None`; the risk
  manager rejects only when those values are present. The “BTC-correlated” position count is all
  same-direction crypto positions, not measured aggregate portfolio beta or pairwise correlation.
  With default values, the generic same-direction cap of 2 fires before the BTC-specific cap of 2,
  making the specific default control operationally redundant. Backtest accounts contain no open
  positions and reset daily realized P&L to zero, so beta/correlation, concurrent exposure and daily
  loss gates are not tested.
- **Impact:** A benchmark-data outage or changed configuration can silently remove the intended beta
  constraint, while a single-symbol replay can overstate portfolio readiness.
- **Required fix:** Fail closed for configured exposure features, or explicitly degrade to a tighter
  fallback limit. Persist exposure estimates with the signal; compute aggregate absolute/net BTC
  beta and pairwise clusters; validate them in chronological shared-capital portfolio replay.
- **Mitigation:** The generic same-direction cap, max-open-position cap and gross-exposure cap still
  limit default exposure.

### TEST-001 — Deployment and governance paths are under-tested

- **Rule ID:** ATLAS-TEST-COVERAGE-001
- **Severity:** Medium
- **Location:** `src/algotrader/cli.py`; `src/algotrader/infrastructure/runtime.py`;
  `src/algotrader/application/scheduler.py`; `src/algotrader/application/backtest.py`
- **Evidence:** Measured coverage is 78% overall, but CLI is 0%, runtime 40%, scheduler 44%, and
  backtest 53%. The untouched-holdout/promotion JSON, runtime assembly/cleanup, recurring failure
  behavior and most end-to-end replay are not covered. SQLite ResourceWarnings did not fail the
  normal suite.
- **Impact:** The highest operational and research-governance risks can regress while all 208 tests
  remain green.
- **Required fix:** Add CLI subprocess/argument tests, runtime contract tests with fake clients,
  scheduler failure/kill-switch tests, upgrade fixtures, full backtest assertions, and CI that fails
  on unraisable/resource warnings with risk-weighted per-module coverage thresholds.
- **Mitigation:** Execution, risk, strategy, repository and reconciliation modules have materially
  stronger coverage (81–98%).

### MAINT-001 — Decision policy is concentrated in oversized, high-complexity functions

- **Rule ID:** ATLAS-MAINTAINABILITY-001
- **Severity:** Medium
- **Location:** `src/algotrader/application/screener.py:62-149`, `:236-365`, `:557-762`;
  `src/algotrader/application/alpha.py:316-427`; `src/algotrader/application/service.py:631-806`;
  `src/algotrader/cli.py:261-395`
- **Evidence:** Radon reports complexity F(52) for `TrendQualityAlpha._quality`, F(41) for
  `MultiFactorScreener._score`, E(31) for CLI `_backtest`, D(28) for screener history evaluation,
  and D(27) for orphan-position adoption. `service.py`, `sqlite_state.py`, and `screener.py` are each
  roughly 950–1,100 lines. Screening, strategy construction and risk intentionally repeat several
  gates, but ownership is not expressed as a single decision policy.
- **Impact:** A change can update live screening but not replay/risk/revalidation, creating subtle
  policy drift that type checking and unit tests may miss.
- **Required fix:** Decompose by use case: discovery, research recording, execution authority,
  incident recovery, and position lifecycle. Represent gates as named policy results shared by live
  and replay, with explicit defense-in-depth invariants where duplication is intentional.
- **Mitigation:** The average codebase complexity is A(3.91), strict typing is clean, and most pure
  indicator/precision functions are small.

### SEC-003 — Network-edge limits and continuous supply-chain checks are external/manual

- **Rule ID:** FASTAPI-PROXY-001 / FASTAPI-LIMITS-001 / FASTAPI-SUPPLY-001
- **Severity:** Medium
- **Location:** `Dockerfile:1-20`; `compose.yaml:5-17`; `pyproject.toml:11-26`; no CI workflow
- **Evidence:** The application provides bearer authentication and Host validation but no in-process
  request/concurrency limiter. Expensive authenticated screen/cycle endpoints can create many venue
  requests. Uvicorn terminates no TLS. The base image is tag-pinned, not digest-pinned. Dependency
  audit and package checks are manual because no CI workflow exists.
- **Impact:** An accidentally public port, leaked operator token, repeated operator request, or stale
  image/dependency can create availability or confidentiality risk.
- **Required fix:** Keep the API private behind IAP/VPN or a trusted TLS gateway; add request,
  concurrency and body-size limits; pin/scan the runtime image; automate lock verification,
  dependency audit, tests, typing, linting and image scanning in CI.
- **Mitigation:** Compose binds host localhost, the container is non-root/read-only/capability-free,
  production docs are disabled, and the current locked dependencies passed audit.

### SEC-004 — SQLite lease has no fencing token for HA

- **Rule ID:** ATLAS-SINGLE-WRITER-LEASE-001
- **Severity:** Medium
- **Location:** `src/algotrader/application/service.py:310-336`;
  `src/algotrader/infrastructure/sqlite_state.py:338-384`
- **Evidence:** Lease acquisition is atomic, but an owner paused past expiry cannot be fenced after
  another instance acquires the lease. Discovery also intentionally runs before lease acquisition,
  so duplicate API/worker instances multiply venue and research load even when order authority is
  serialized.
- **Impact:** Unsupported replicas can briefly have two order-authority holders and can duplicate
  expensive screening/storage work.
- **Required fix:** Preserve a single replica now. For HA, move to PostgreSQL advisory locking or a
  renewable lease with a monotonically increasing fencing token checked when reserving every intent.
- **Mitigation:** Docker specifies one Uvicorn worker; deterministic client IDs and durable intents
  limit duplicate order submission.

## Low findings and cleanup

### ROB-004 — External `Retry-After` is parsed without validation or a cap

- **Status:** Closed; malformed/non-finite values use bounded exponential fallback and valid values
  are capped. Regression tests cover malformed, negative, non-finite and excessive headers.
- **Location:** `src/algotrader/adapters/binance/client.py:89-103`, `:149-155`
- A malformed, negative, non-finite or very large header can raise during parsing or stall a cycle.
  Parse defensively and clamp to `[0, max_rate_limit_backoff_seconds]`.

### ROB-005 — Runtime cleanup stops after the first client-close exception

- **Status:** Closed; shutdown attempts every component and raises an aggregate error afterward.
- **Location:** `src/algotrader/infrastructure/runtime.py:52-55`
- Close all clients with `asyncio.gather(..., return_exceptions=True)` (after scheduler stop), then
  surface an aggregated cleanup error.

### MAINT-002 — Exception-text compatibility fallback can mask an internal `TypeError`

- **Status:** Closed; capability detection now inspects the callable signature.
- **Location:** `src/algotrader/application/service.py:202-209`
- Catching any `TypeError` whose message mentions `include_rejected` can misclassify a real screener
  defect. Make the protocol explicit or adapt legacy implementations at construction time.

### MAINT-003 — Small stale/duplicated policy remains

- **Location:** `src/algotrader/config.py:61-65`, `:239-245`;
  `src/algotrader/adapters/binance/spot.py:189-225`;
  `src/algotrader/adapters/binance/futures.py:179-201`
- The deprecated `screening_interval_hours` compatibility alias has no removal version/date, and
  Spot/USD-M entry parameter serialization is duplicated. Remove the alias in a declared release
  and extract only the truly common serialization logic; keep venue-specific semantics separate.

### ROB-006 — Production safety depends on an `assert`

- **Status:** Closed; the Futures adapter raises an explicit `OrderRejected` invariant failure.
- **Location:** `src/algotrader/adapters/binance/futures.py:239-244`
- `assert target_price is not None` disappears under optimized Python. Replace it with an explicit
  invariant check that raises `OrderRejected` or `SafetyInterlockError`.

### SEC-006 — Operational state is plaintext at rest from the application's perspective

- **Location:** `src/algotrader/infrastructure/sqlite_state.py:73-188`
- Credentials are not stored, and files are `0600`, but signals, orders and P&L are readable from a
  copied disk. Use encrypted GCP volumes/backups, least-privilege backup access, restore drills and
  integrity monitoring.

## False positives explicitly rejected

- The final Bandit scan is clean. Earlier B608 reports were false positives around code-owned SQL
  column names; remediation replaced those expressions with static queries while retaining bound
  parameters for every external/runtime value.
- Vulture's unused `cls` reports are required validator signatures, not dead code.
- Pylint's localized adapter/orchestration similarities are maintainability debt, not correctness or
  security vulnerabilities; venue-specific order semantics should not be force-merged.
- No evidence was found of exposed secrets, unsafe command execution, SSRF, permissive CORS,
  unauthenticated privileged routes, unsafe order retry, or committed database files in the Docker
  build context.

## Recommended remediation order

1. **Completed:** fix ROB-001 and make ResourceWarnings fail the test suite.
2. **Completed:** add schema migrations (ROB-002) for persistent GCP volumes.
3. **Substantially completed:** bound research/event retention, indexed labelling and report lookback
   (ROB-003); use an external analytical store when the dataset outgrows single-node SQLite.
4. Implement continuous protection reconciliation and verify key privilege controls (SEC-001/002).
5. Replace the holdout attestation with an immutable experiment registry (remaining TRD-002 work).
6. Build shared-capital portfolio replay for beta/correlation/daily-risk validation (TRD-003).
7. Decompose the two F-complexity hotspots and raise runtime/CLI/scheduler/backtest coverage.
8. Only then evaluate strategy promotion; positive expectancy is a release gate, not a parameter
   target.
