# Pure-screener deployment and migration

The existing topology remains one GCP VM, one Compose project named alfa-terminal, one API worker,
a persistent SQLite volume, and the Vercel frontend. This migration changes the deployed capability
to public-data screening. It does not require a database reset.

## Before rollout

1. Record the currently running image and Compose project name. Confirm that the production project
   really is alfa-terminal before using the workflow; a different name would select a different
   SQLite volume and could leave the previous container running.
2. Back up SQLite using its backup API or a stopped-container copy including any WAL state.
   Preserve /home/deploy/alfa-terminal/data and existing watchlists.
3. Inspect VM cron, systemd and other containers for old atlas worker/run-once or testnet-trader
   processes. Stop those specific legacy services. Replacing this API cannot stop a separate VM
   process or another deployment.
4. Review any existing exchange orders/positions directly in Binance. Removing code does not cancel
   or close them. Do not blindly cancel protective orders around an open position.
5. Revoke the previously committed testnet credentials and remove exchange keys from VM/GitHub
   environment storage. Removing source literals does not revoke credentials or erase Git history.
   This release deliberately does not rewrite shared Git history.
6. Ensure /home/deploy/alfa-terminal/data and the `alfa-terminal_atlas-state` Docker volume are
   writable by container UID 10001 before `docker compose up`. Existing deployments previously
   granted broad permissions; use explicit owner/group permissions instead. The startup path can
   fail with SQLite permission errors if either mount is still owned by root.
7. Keep existing frontend API_BACKEND_URL pointing to this backend. No trading endpoint is needed
   by the current frontend.

## What the release changes

- Docker always starts algotrader.api.portal_app:app with one worker.
- The compatibility algotrader.api.app:app path also serves only the screener.
- No exchange credentials are loaded or forwarded by Compose.
- ATLAS_SCREENER_CRON_ENABLED controls scanning only; default true.
- ATLAS_SCREENER_INTERVAL_HOURS defaults to 4 and must divide 24.
- Runtime data is not copied into the image.
- Source bind mounts are removed; the container runs the built application.
- Watchlist volume and data directory are retained.

The workflow runs backend tests, source linting, strict typing, frontend lint/build, and a
network-disabled container smoke test. Deployment waits for these jobs. It creates a detached
release worktree for the exact verified commit instead of resetting the mutable VM checkout.
Images use the commit SHA tag. Compose builds before replacing the service and waits for health.

The data bind mount uses ATLAS_DATA_DIR=/home/deploy/alfa-terminal/data. The named state volume
uses the existing alfa-terminal project prefix. Do not change those during migration.
Release worktrees/images need a later bounded retention policy; never delete the active or
rollback release.

## Verify after rollout

```bash
docker compose --project-name alfa-terminal --file /home/deploy/atlas-releases/COMMIT_SHA/compose.yaml ps
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/readyz
curl --fail http://127.0.0.1:8000/
```

Root must report mode=screener and order_execution_enabled=false.
GET /api/testnet/status, GET /api/testnet/watchlist, and former /v1 routes must return 404.
Run one controlled scan through the frontend, verify candles/watchlist persistence and scheduler
status, and watch for provider failures. No Binance account permission or API key should be needed.

The API currently retains public watchlist mutation and scan-refresh behaviour for frontend
compatibility. Public exposure still needs authentication/ownership, TLS and rate limits.
The existing direct port mapping is retained to avoid silently breaking Vercel connectivity.
Use a trusted reverse proxy or restricted ingress as the next operations milestone.

## Failure and rollback

A failed build leaves the running service untouched. A failed Compose health wait fails deployment;
the workflow does not claim success and does not automatically restore an older trading image.
Inspect container logs and readiness. Prefer rolling forward with a corrected screener build.
Once a known-good screener release exists, restore that exact image/worktree with the same state
volume and data directory. Restoring the pre-migration image can re-enable trading.

Local verification cannot establish the VM's current process inventory, exchange state, backups or
firewall configuration. Check these in the deployment environment before treating the migration
as complete.

## Research archive acceptance (local implementation, not yet rolled out)

New scans write immutable, checksummed input archives to `data/research`; the existing data bind mount
must therefore provide space and write access for the archive as well as `screener_latest.json`.
`ATLAS_RESEARCH_ARCHIVE_DIR` overrides the location; a different directory needs its own persistent
mount and backup policy. Do not put research blobs in the watchlist SQLite database.

After an explicitly approved rollout, run one bounded controlled scan and retain its `scan_id` and
`scan_archive` receipt. Verify `status=saved`, the manifest/payload checksum, and offline replay in the
same image/source/dependency release. Confirm a deliberately unwritable test archive location reports
`unavailable` without silently converting valid candidates into zero results; do this in staging, not
by removing production permissions. Preserve original archives unchanged during rollback or upgrades.

24h/48h outcome collection is manual (`atlas observe-scan`); it is not enabled by the existing cron.
It refuses cohorts above its explicit symbol limit and uses historical public minute candles only.
Measure request weight, retries, duration, disk growth and missing coverage before scheduling it.
Restore input and outcome directories together in a backup drill, then verify checksums and compatible
replay. No retention deletion is currently configured. Local tests do not verify VM disk ownership,
capacity, backups, request load or current deployment state.

## Dedicated opt-in research VM (2026-10-26)

Compatibility note: the approved fair-traversal observer extension changes its source-hashed
measurement fingerprint. Do not move an incompatible active session onto this release or bypass
`source_mismatch`; use a fresh compatible parent/child/session. Existing archives remain readable.
Synthetic report-capacity checks do not replace a persistent-storage restore drill or VM/provider
load acceptance before enabling collection.

Approved destination only: `alfa2-512717` / `europe-west10-c` /
`instance-20261025-174348`. This is a research-only VM, not a replacement API server.
Use OS Login through IAP; no worker ports need public or internal ingress. Do not
change the existing API VM, its project IAM, GitHub SSH secrets or Vercel routing.
Do not run a bare `docker compose up` on the research VM: it defaults to the API.

The optional Compose profile provides `research-ranks` and `research-outcomes`,
each UID/GID10001, read-only root, dropped capabilities, no-new-privileges,
256 MiB/0.5 CPU, bounded tmpfs and an archive-only mount. These are resource
safeguards, not evidence that the largest cohort fits or meets its deadlines.
No runtime dependency or exchange credential is required by the runner.

Deploy only an independently reviewed, immutable full-SHA release after all
verification gates. The host wrapper `scripts/research_compose_control.py` targets
only the `atlas-research` Compose project and the two named worker services.
It uses argv, a fixed local Docker context, and strictly parsed operator data;
it never sources that data as shell code. Verify the local default Docker context
points to this VM, and verify the release checkout SHA/cleanliness before use.

Persistent control location (outside SHA releases):
`/home/deploy/alfa-terminal/research-control.env`. Missing means disabled. An
existing malformed, symlinked, oversized or group/world-writable file blocks
preflight. Create/edit it only as the trusted operator with restrictive permissions.
Do not commit it, mount it into containers or place credentials in it. Allowed
keys are exactly `ATLAS_RESEARCH_ENABLED=true|false` and an optional canonical
`ATLAS_RESEARCH_SESSION_ID` (required when true).

The wrapper accepts `--release /home/deploy/atlas-releases/FULL_SHA` and
`--action build|up|health|stop`. Use the same control file for new releases and
rollback. With missing/false control, `up` stops only the named research workers;
it does not start research or touch an API service or volume. With explicit true
control and a valid enrolled session, `up` waits for both worker health checks.
Source incompatibility is a blocked session, not permission to reinterpret it.

The sole archive bind is `/home/deploy/alfa-terminal/data/research` to
`/app/data/research`. Before collection, verify its persistent storage/UID10001
ownership, >=2 GiB free, archive size <5 GiB, backup/restore expectations and boot
disk deletion policy. Do not recursively change ownership of unrelated paths.
Secure Boot, workload service-account permissions and measured resource usage
remain explicit infrastructure review items, not claims satisfied by unit tests.

Shipping code is not activation. After separate activation approval, choose one
complete archived child and its exact parent, then use `atlas research-start
--shadow-id UUID --archive-dir /app/data/research` in the compatible image.
Enrollment/status/stop are offline; no recurring enrollment or candidate rescans
are introduced. Record its session UUID in the operator control before starting
the two roles. Observe `research-status` plus per-role `research-health` and actual
archived snapshot/outcome/report IDs. A healthy terminal-idle process is not
collecting; a heartbeat is not a successful market observation.

Ranks run anchored five-minute slots without backfilling missed live observations.
Outcome passes are due at the original 24h/48h endpoints plus60s, limited to30
actual HTTP attempts/90s including backoff and cleanup, with fixed12h retry expiry.
Both roles honor durable cooperative cooldowns; this is not a global IP quota
guarantee. At expiry, reports retain unknown paths and the whole original cohort.
Reserved reports freeze exact IDs/checksums/as-of so late evidence cannot change
a restarted finalization. Durable filesystem publication is not a hard network
deadline. SIGTERM waits for committed evidence where possible; OS kill/OOM can
still leave explicitly interrupted or missing observations.

Local packaging check: `uv run python -m tests.research_container_smoke --image IMAGE`
uses synthetic archives, no network, constrained API/idle/stopped-worker containers
and a stopped-worker restart. Test-created containers/volume are removed afterward.
This is packaging/lifecycle verification, not target-VM capacity or24h/48h evidence.

The existing workflow still deploys the API on main. A feature-branch CI pass or
image build does not authorize merging main under this VM-only scope; publishing
must resolve that trigger boundary before any out-of-scope external side effect.
