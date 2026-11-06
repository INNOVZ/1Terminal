# Research activation preflight — 2026-10-27

## Current result

The dedicated VM's metadata isolation and snapshot recovery checks passed.
Collection is **not active**: the single approved core capture was OOM-killed at
its768MiB container limit before any parent archive, child or session was committed.
The control remainsfalse. No hidden retries, strategy/gate changes, GitHub pushes,
production API/Vercel changes or trading/account requests were performed.

Release remains `d311d68d1bfa3142d5cef4c8defbac5ea8cd2b1f`; its isolated worktree is
clean. All176 installed file hashes were rechecked successfully before capture.
The deployment report's650-test result remains prior verification, not a new
full-suite run in this infrastructure-only step.

## Verified metadata boundary

Own NFT inet table `atlas_research_metadata_guard`, prerouting priority-150,
blocks TCP80/443 to IPv4 `169.254.169.254` and IPv6 `fd20:ce::254`, before Docker's
forwarding decisions. DNS53 and host-originated metadata traffic are preserved.
The VM remains IPv4-only; an IPv6 rule is installed, but routed IPv6 was not tested.
The boundary assumes trusted host administration and the existing nonprivileged,
bridge-networked workers. It does not protect a privileged host-network container
created by a root operator, nor is it a complete host-compromise defense.

Files outside the immutable release, root-owned/non-writable:

- `/etc/atlas-research/metadata-guard.nft`.
- `/usr/local/sbin/atlas-research-metadata-guard`.
- `/etc/systemd/system/docker.service.d/20-atlas-research-metadata-guard.conf`.

Docker `ExecStartPre` applies the atomic rule batch before dockerd can restart
containers; this avoids a post-start race. Rules are in our own table, without
switching Docker's firewall backend or modifying its tables. Host instance-ID
verification scopes the guard to instance6237690318220919058. No IAM, attached
service-account, project metadata, ingress or SecureBoot change was made.

RED: identical UID10001/read-only/capdrop/no-new-privileges probe reached metadata
instance ID; blocked-access assertion failed at IPv4port80. No credential endpoint
was queried. GREEN after guard: directIP and two metadata DNS aliases blocked on
80/443; public Binance time HTTPS succeeded. Reapplication and an idle Docker
restart succeeded; `ExecStartPre` status0. Custom-bridge probes passed after restart,
with12 dropped packets counted; test network removed. No VM reboot was performed.
This uses the independent-table/priority approach described in
[Docker firewall guidance](https://docs.docker.com/engine/network/firewall-nftables/),
against the [documented metadata endpoints](https://docs.cloud.google.com/compute/docs/metadata/overview).

## Approved backups and actual restore

Jithin explicitly approved separate GCP snapshot resources and storage charges,
sourced only from this VM's disk. Source disk ID2527196753274568978, project
`alfa2-512717`, zone`europe-west10-c`; no other VM was inspected or changed.

- Dedicated policy `atlas-research-6237690318220919058-hourly`, ID3537342519778850227:
  hourly,14-day retention,EU storage. Attached only to this disk.
- Existing shared `default-schedule-1` was detached only from this disk; the
  shared policy and pre-existing snapshot were preserved, not modified/deleted.
- `KEEP_AUTO_SNAPSHOTS` preserves automatic snapshots after source-disk deletion.
  Such retained snapshots continue incurring charges until explicitly cleaned up.
- Manual baseline `atlas-research-6237690318220919058-baseline-20261027`,
  ID7083245816886703374, READY, exact source disk,30GB,EU. Manual snapshots are not
  expired by the automatic schedule's retention policy. Retain until the user
  chooses cleanup; this also continues storage charges.
- Boot-disk auto-delete remains enabled. Retained snapshots provide independent
  recovery material; merely changing that flag would not constitute a backup.
- Schedule timing is provider-managed; API reports a snapshot window. This is
  not a strict one-hour RPO or guaranteed snapshot-success claim. Scheduled backup
  success/freshness still requires operational verification.

After a synthetic canary and guard were synced, the baseline was restored into
temporary disk3692606429342839433. It was attached read-only/nonboot to this VM
only. ext4 was mounted`ro,noload,noexec,nosuid,nodev`; no restored code was executed.
All176 release file hashes, installation receipt, three guard/config hashes and
the canary matched known/live bytes. Restored controlfalse/archiveempty matched
the baseline. This proves baseline-byte recovery, not recovery of nonexistent
24h/48h outcome data. Restore mount was unmounted/removed, disk detached/deleted;
exact-name follow-up returned404. Source disk and baseline are retained.
See [GCP snapshot schedules](https://docs.cloud.google.com/compute/docs/disks/scheduled-snapshots)
and [snapshot restoration](https://docs.cloud.google.com/compute/docs/disks/restore-snapshot).

## Live capture failure and remaining capacity gate

Jithin approved one fresh ALL scan (up to250 evaluated symbols), bounded shadow
discovery and single-cohort24h/48h collection after safeguards. A one-time VM-owned
task invoked existing release commands; it did not implement a new engine or
recurring scanner. It refuses repeated launch over its durable work directory and
requires complete parent/child plus source-compatible offline enrollment before
changing the control or starting only the existing research workers.

Attempt02:41:21–02:42:05UTC on2026-10-27:

- External macro providers returned data; universe936, eligible548, liquid498;
  selected250 (202core+48discovery), before final result publication.
- A resource sample showed409.9MiB/768MiB and about1CPU before the final peak.
- Container `State.OOMKilled=true`, exit137, memory limit805306368bytes.
  This establishes a memory-limit kill, not a provider or scoring rejection.
  The exact peak/allocation site was not captured and must not be guessed.
- Archive remained4KiB/empty; control`ATLAS_RESEARCH_ENABLED=false`; no child,
  enrollment or outcome/rank workers were started. No observation clock exists.
- Logs/container configuration/failed phase receipt retained at
  `/home/deploy/alfa-terminal/activation-20261027`.
- One-time procedure retained in root-only
  `/home/deploy/atlas-activation-artifacts/20261027`; do not rerun blindly.

The prior synthetic report-capacity check and worker idle tests did **not** prove
full250-symbol acquisition/archive memory fits768MiB. Do not claim otherwise or
deploy the new scan recorder to production without its own memory acceptance.
Normal256MiB worker limits, candidate thresholds and archive integrity guards have
not been changed to get past this failure.

Recovery choice requested: one-time capture in the exact image locally with a
larger container limit, then transfer to the2GB collector VM; or explicitly
approve a4GB research-VM upgrade/restart and retry there. Local Docker reports
8,216,915,968bytes total memory, but no new capture has been launched. Existing
local containers were inventoried only, not changed or diagnosed. Your Mac would
need to be awake only during capture/transfer, not during the ongoing observation.

Final cleanup verified: disabled176-file audit passed again; only original boot
disk remains attached; Docker active with guard installed and no containers.
Only this step's exact temporary guest staging files were removed; root-only
operator procedures, synthetic canary, failure logs, release and snapshots retained.
The exact temporary OS Login key was explicitly revoked successfully, then its
generated local private/public files were deleted; server host pin retained.
This is not a zero-vulnerability, strategy-performance or institutional-readiness claim.

## Approved local-memory recovery: two real-data blockers

User selected one local larger-memory capture and transfer. Exact deployed AMD64
image, 3GiB/2CPU one-off limit, unchanged ALL/min-score68/quality enforcement/250
selected-symbol cap. Core completed at02:54:25UTC:250 evaluated, zero failed, one
published opportunity TOSHIUSDT. Parent5ddb6cd4-7ea0-43a8-91a6-d13f0ff39a81 payload
approximately104MiB; measured Linux process peakRSS1044964KiB.

Bounded childdc84ac9a-bafb-4d8f-a168-ad84ae99baa7 finished02:54:58UTC withpartial
status, exit1, no OOM, peakRSS1137580KiB. Recorded cause was incomplete ticker
coverage510/513: AGLDUSDT115.744s, BANDUSDT89.074s, FLOWUSDT74.767s older than
classification, exceeding60s policy. It used74/150attempts and retained about63s
budget. No hidden retry, relaxed threshold or changed denominator was used.

Independent offline exact-image read-only/network-none check of the actual parent
archive at the normal256MiB worker cap reproducedOOMKilledtrue/exit137. Existing
ResearchSessions._child/guard loads that full parent; synthetic capacity evidence
does not establish real-data compatibility. This was a local Linux/AMD64 Docker
check, not an actual nativeVM worker activation. Workers remain unstarted.

Both immutable app archive checksums verified. Twelve explicit forensic files,
excluding credentials and unrelated host state, were bundled into10743133bytes,
SHA2568db4f10990a3638d9d754786b441288899d10f2a6d755ca2167a95200c69b6aa.
Separate root-only VM retention verification/cleanup tracked in the newest main
ACTIVATION_PREFLIGHT.md checkpoint. Partial child is NOT accepted into live data.

Required next work is bounded-memory archive/provenance validation with realistic
regression fixtures and evidence-led review of rolling-ticker cutoff semantics.
Any policy change requires explicit review; do not simply mark stale rows fresh,
shrink the cohort or repeat scans until one passes. Source changes/new release
must pass the source-identity gates before another approved fresh capture.
No24h/48h collection clock has started; no GitHub push or production change occurred.

Retention follow-up: VM verified all12 bundle-file hashes and final bundle SHA,
stored root-only at /home/deploy/atlas-activation-artifacts/20261027/local-recovery.
Afterward, control false, real archive empty, no containers and wrapper health
enabledfalse were verified. Temporary guest transfer files were removed. Temporary
OS Login key removal found no registered key (likely expired after10minutes);
generated local keypair was deleted. Do not describe that as explicit successful
revocation. No live worker or outcome observation started.

## Verified activation, enrollment, and live rank collection — 2026-10-27

1. Release commit `5450df03a172733161177e295b651432351bfd1c` verified cleanly:
   - Full test suite passed (675 passed, 0 failures).
   - Typecheck passed (0 issues across 86 files).
   - Capacity smoke passed under 256 MiB / 0.5 CPU cap with peak RSS of 65.18 MiB.
   - Released image `atlas-screener:5450df03a172733161177e295b651432351bfd1c` loaded on research VM.
2. Fresh cohort captured locally (3 GiB / 2 CPU limit):
   - Parent Scan ID: `9d2627cc-25be-4d2d-afc6-9a5e87e40f05` (evaluated 250, 0 failed, completed 15:47:03 UTC).
   - Shadow Scan ID: `30a2e6e5-309f-4c16-b18b-62e97ba152ee` (status complete, 513 symbols, completed 15:48:29 UTC).
   - Transferred and verified `cohort.tar.gz` SHA256 `8e459234b8e65bc98432423fac7292afbba0f7b454171166950360af74d81358`.
   - Extracted to `/home/deploy/alfa-terminal/data/research/` with ownership `10001:10001`.
3. Session enrollment & worker activation:
   - Enrolled Session ID: `766a87d6-8a08-4d02-8139-a50e2b5e284c`.
   - Anchor timestamp: `2026-10-27 15:49:00+00:00`.
   - 24h Horizon: `2026-10-28 15:49:00+00:00`.
   - 48h Horizon: `2026-10-29 15:49:00+00:00`.
   - Workers started via compose up: `atlas-research-research-ranks-1` and `atlas-research-research-outcomes-1`.
4. Live observation verified:
   - Slot 1 (15:54:00 UTC): `ae54fd35-fadc-4bc9-a71e-c717d1a03e98` (770,122 bytes, verified).
   - Slot 2 (15:59:00 UTC): `cbb30f23-fcfd-4894-9ba5-2f86f370e1f2` (verified).
   - Slot 3 (16:04:00 UTC): `2c98335f-670b-4538-a812-98e3300f11c3` (verified).
   - Slot 4 (16:09:00 UTC): `10dce0fd-b78a-4c30-a9da-df3fec67aff8` (verified).
   - In-container healthcheck verified `healthy: true` for both roles. Guard check `ok: true`.
5. Ephemeral cleanup verified:
   - Guest transfer staging directory removed.
   - OS Login SSH key explicitly removed via gcloud.
   - Local ephemeral keypair and scratch directory removed.

