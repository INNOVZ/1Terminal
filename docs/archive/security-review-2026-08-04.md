# Atlas security review — 2026-08-04

## Executive assessment

Atlas has a strong fail-closed foundation for **paper and single-host testnet use**. The review
found no direct secret disclosure, injection, arbitrary-order API, unsafe retry, or known vulnerable
locked dependency. The implementation now also has owner-only state files, a durable execution
lease, hash-pinned container dependencies, fresh-signal revalidation, and restart detection for
order intents whose result was never persisted.

Atlas is **not approved for unattended live capital yet**. Two high-impact operational controls
remain outside the current implementation: continuous exchange order/protection reconciliation and
verified least-privilege Binance key configuration. The appropriate current posture is paper or
testnet, one host, localhost-only operations API, and manual supervision.

## Scope and verification performed

- Reviewed all Python application, API, exchange adapter, state, configuration, deployment, and
  test code.
- Searched for unsafe subprocess execution, dynamic evaluation, insecure deserialization, disabled
  TLS verification, permissive CORS, unprotected privileged routes, unparameterized SQL, and secret
  output paths. None were found in reachable application code.
- Ran `pip-audit 2.10.1` against exact `uv.lock` runtime versions on 2026-08-04: **no known
  vulnerabilities found**. The local package itself was correctly skipped because it is not a PyPI
  dependency.
- Confirmed Starlette is pinned to 1.3.1, beyond the patched releases for the historical
  `FileResponse` and multipart advisories. Atlas does not expose file or multipart endpoints.
- Ran Ruff, strict MyPy across production and test code, and the full 96-test suite.
- Ran a live-public-data, closed-candle BTCUSDT USD-M 30-day replay smoke test using the new hourly
  discovery/eight-hour rolling cooldown path; it completed successfully and produced no eligible
  trades under the strict gates.
- Docker image execution was not tested because the local Docker daemon was not running. The
  Dockerfile and build context were reviewed statically.

## Positive controls verified

- Live mode requires production environment, an explicit enable flag, and an exact acknowledgement
  (`src/algotrader/config.py`). Paper remains the default.
- Every privileged `/v1/*` route uses constant-time bearer-token authentication. Host headers are
  allowlisted, CORS is absent, debug is off, security headers are centralized, and API docs are
  disabled in production (`src/algotrader/api/app.py`). API startup now rejects a missing or short
  operator token.
- Binance base URLs are code-controlled HTTPS endpoints. Signed requests use HMAC-SHA256, a
  five-second receive window, clock synchronization, bounded HTTP timeouts, and TLS verification by
  default (`src/algotrader/adapters/binance/client.py`). Unknown order submission outcomes are never
  blindly retried.
- Entry intent is persisted before submission, client IDs are deterministic, and inconclusive order
  recovery activates the durable kill switch (`src/algotrader/application/execution.py`). On restart,
  an intent without a result permits automatic continuation only after a confirmed terminal
  zero-fill exchange result (`src/algotrader/application/service.py`).
- A durable SQLite lease serializes exchange order authority across local processes sharing the
  state database. A process-local lock also prevents overlapping calls in one process.
- Final entry orders are IOC limits bounded by both quote tolerance and the exact 0.5-ATR
  signal-close boundary. Expiry is checked before account work and immediately before intent
  persistence.
- SQL statements are parameterized. SQLite uses WAL plus `synchronous=FULL`; the database, WAL, and
  shared-memory files are forced to mode `0600`.
- `.env` is git-ignored and Docker-ignored. The reviewed local `.env` was changed from `0644` to
  `0600` without reading or printing secret values. The example token is now blank instead of a
  reusable public value.
- Container dependencies are exact-version and hash pinned in `requirements.lock`; the runtime
  process runs as non-root UID 10001.

## Open findings

### SEC-001 — High — No continuous proof that exchange protection remains active

**Location:** `src/algotrader/application/service.py:343`,
`src/algotrader/application/execution.py:133`, `docs/architecture.md` recovery roadmap.

**Evidence:** Atlas now journals intent, entry fill, stop confirmation, target confirmation, and
tracked-position stages; detects interrupted execution lifecycles after restart; compares local and
exchange positions in both directions including quantity; and refuses unsafe kill-switch clearing.
It does not yet continuously reconcile exchange open orders,
verify that every live position still has the intended stop and target, consume/replay the Binance
user-data stream, or fail closed on every exchange position/order absent from local state.

**Impact:** A canceled/rejected protection order, manual exchange action, account reset, or missed
fill event could leave exposure that the worker does not understand. New trades might remain halted
in some failure paths, but continuous protection cannot currently be proven.

**Required fix:** Build a startup and recurring reconciliation worker using REST snapshots plus the
Binance user-data stream. Maintain an explicit order state machine; map every exchange position and
open order to a durable trade; verify stop quantity, trigger, side, and target; halt and alert on any
orphan, mismatch, stream gap, or stale snapshot. Add crash-at-every-write-boundary integration tests.

**Current mitigation:** Exchange-side brackets, stop-first Futures placement, durable execution
stages, unknown-status kill switch, and bidirectional position checks. Restrict use to testnet/manual
supervision until the required fix exists.

**False-positive note:** Exchange-side orders reduce process-outage risk but do not prove an order
was accepted, remained active, or still matches the intended quantity.

### SEC-002 — High — Binance credential scope and network restriction are not verifiable in code

**Location:** `src/algotrader/infrastructure/runtime.py:69-78`, deployment configuration.

**Evidence:** Startup verifies that signed account endpoints accept the key, but Binance does not
return enough information through these calls for Atlas to prove that withdrawals are disabled,
the key is IP restricted, or a dedicated limited-balance subaccount is being used. Binance documents
separate endpoint permissions and warns that API keys and secrets are sensitive; it recommends
least privilege and IP restrictions.

**Impact:** A stolen over-privileged key could permit unauthorized trading and, if withdrawal access
was enabled, direct asset loss.

**Required fix:** Operationally verify and record that each production key has only the permissions
Atlas needs, has withdrawal disabled, is IP allowlisted, is isolated to a low-balance subaccount,
and is rotated. Prefer separate monitoring and trading credentials where the deployment permits it.
Store production secrets in a secret manager rather than a plaintext project `.env`.

**Current mitigation:** `SecretStr`, HTTPS, no secret output, `.env`/Docker exclusion, owner-only
local permissions, and no wallet/withdrawal endpoint in Atlas. See the official
[Binance request-security documentation](https://developers.binance.com/en/docs/products/spot/rest-api).

**False-positive note:** The actual keys may already be correctly restricted, but that condition
cannot be established from this repository and was deliberately not probed.

### SEC-003 — Medium — Remote API edge security depends on deployment configuration

**Location:** `Dockerfile:19`, `src/algotrader/cli.py` serve configuration,
`src/algotrader/api/app.py`.

**Evidence:** Local CLI serving binds to `127.0.0.1`, but the container binds Uvicorn to `0.0.0.0`.
The application provides bearer authentication but not public-edge TLS, distributed rate limiting,
per-operator identities, or token rotation/revocation records.

**Impact:** If port 8000 is published directly, the bearer token can be intercepted over plaintext
HTTP and expensive authenticated endpoints can be abused. One shared token weakens attribution.

**Required fix:** Keep the port private or place it behind a trusted TLS reverse proxy/API gateway
with network ACLs, request/body/concurrency limits, access logs, and ideally mTLS or short-lived
identity tokens. Do not forward untrusted proxy headers without an explicit trusted-proxy policy.

**Current mitigation:** localhost default, 256-bit recommended token, constant-time comparison,
TrustedHost middleware, no CORS, no arbitrary-order route, and production docs disabled.

**False-positive note:** This is low risk while Atlas remains localhost-only; severity becomes
medium when the container port is reachable from another host.

### SEC-004 — Medium — SQLite lease has no fencing token or renewal

**Location:** `src/algotrader/application/service.py:127-152`,
`src/algotrader/infrastructure/sqlite_state.py:199-245`.

**Evidence:** The lease is atomic and the exchange-authority section is deliberately kept short,
but an owner that pauses beyond the configured five-minute expiry is not technically fenced from
submitting after another owner acquires the lease. SQLite is also not a suitable distributed
consensus store across unreliable network filesystems.

**Impact:** Under a long process pause or unsupported multi-host deployment, two workers could
temporarily believe they have order authority.

**Required fix:** For production HA, use PostgreSQL advisory locking or a renewable lease with a
monotonic fencing token. Validate the token in the same durable transaction that reserves an order
intent immediately before submission. Alert on lease loss and never deploy SQLite over NFS.

**Current mitigation:** one-host scope, one Uvicorn worker, lease acquired only after discovery,
bounded exchange timeouts, deterministic order IDs, and durable cooldown/trade records.

**False-positive note:** This is adequately controlled for the documented one-host testnet model.

### SEC-005 — Medium — Dependency vulnerability scanning is manual

**Location:** `pyproject.toml`, `uv.lock`, `requirements.lock`; no CI workflow is present.

**Evidence:** The exact lockfile passed the current audit and container installs are hash pinned, but
there is no CI job or dependency-update automation to catch a vulnerability published tomorrow.
The GitHub Advisory Database is continuously updated; for example, historical Starlette DoS issues
required patched releases ([advisory](https://github.com/advisories/GHSA-7f5h-v6xp-fcq8)).

**Impact:** A newly disclosed framework or HTTP-stack vulnerability could remain deployed until the
next manual review.

**Required fix:** Add CI that runs `uv lock --check`, `pip-audit` against an exported production
requirements file, tests, Ruff, and strict MyPy. Enable reviewed dependency update PRs and rebuild
the image regularly.

**Current mitigation:** exact `uv.lock`, hash-pinned `requirements.lock`, narrow top-level version
ranges, and a clean manual audit on this review date.

### SEC-006 — Low — Durable trading state is not encrypted at the application layer

**Location:** `src/algotrader/infrastructure/sqlite_state.py`.

**Evidence:** The database contains signals, order intents/results, positions, PnL, and events in
plaintext JSON/columns. It does not contain Binance secrets. File permissions are owner-only, but
backups or a copied disk can still expose trading activity.

**Impact:** Loss of the host or an unprotected backup exposes operational trading data and allows
offline tampering. Host compromise already implies process-level access, so application encryption
has limited benefit without external key management.

**Required fix:** Use full-disk/volume encryption, encrypted authenticated backups, protected backup
credentials, restore drills, and integrity monitoring. For HA, use encrypted PostgreSQL storage and
TLS connections.

**Current mitigation:** no API credentials in the database, `0600` database/WAL/SHM permissions,
parameterized writes, and synchronous durability.

## Recommended release decision

- **Paper:** approved for local development.
- **Binance Testnet:** approved for a single host with operator supervision and recovery drills.
- **Tiny live canary:** not approved until SEC-001 and SEC-002 are closed and SEC-003 deployment
  controls are demonstrated.
- **Unattended or HA live trading:** not approved until SEC-001 through SEC-004 are closed and the
  validation gates in `README.md` pass.
