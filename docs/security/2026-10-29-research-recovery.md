# Research collection recovery — 2026-10-29

## Current checkpoint — October30

V3 release `3f9b36f5e5f34c892e886b6aca86bfb8fc860d57` is installed and running
only on the approved research VM.702 tests/static/image/capacity gates passed.
Active session `ec5b83a8-b892-473e-a0e6-69425fb35c5e`, parent
`79c0a603-20ca-4099-820d-132c8905ec14`, child
`bfac3bda-dc08-4832-8e7c-b741d90fae46`, frozen512-member cohort.
Anchor2026-10-30 04:30UTC; endpointsOctober31/November1 at04:30UTC.
At09:41:37UTC, all62 real5min snapshots through09:40 were independently
reconstructed/accepted:26complete,36bounded,noneexcluded,maxobservedgap303.909302s.
One coreSOONUSDT candidate and zero qualified shadow candidates; not strategy
validation. Post-activation VM-disk snapshotREADY. Core selection is unchanged.
Deployment closeout is complete; temporary task SSH keys revoked and files
removed, original access preserved. The next required work is the future24h/48h
outcome/coverage review, not another capture or deployment.
See the chronological entries below for exact checksums and retained artifacts.
Do not restart capture, reset this anchor, re-enable the old study, or add scans.

## Scope and historical October29 checkpoint

Only project `alfa2-512717`, zone `europe-west10-c`, VM
`instance-20261025-174348`, instance ID `6237690318220919058` is in scope.
The machine is currently e2-small. GitHub, the production API VM and Vercel have
not been changed. Preserve all existing cohort evidence and original anchors.

Old session (finalized; workers stopped): `766a87d6-8a08-4d02-8139-a50e2b5e284c`.
Parent: `9d2627cc-25be-4d2d-afc6-9a5e87e40f05`.
Child: `30a2e6e5-309f-4c16-b18b-62e97ba152ee`.
Cohort: 513. Anchor: 2026-10-27 15:49 UTC. Endpoints: October 28/29
15:49 UTC. The finalized 24h report contains only 16 complete forward outcomes;
the rest remain unavailable. Historical prospective gaps cannot be backfilled.

## Applied operational recovery

Isolated worktree: `/Users/jithinjacob/.codex/worktrees/dual-track-shadow/trdindicator`.
Branch `codex/research-vm-direct-release`, clean commit
`1e829884cf9c455012f3e9ce4105c134a717e32f`.

- Process-local journal cache checks the full path set and payload/manifest
  inode, size, modification time, change time and mode on every read. New or
  changed records are checksum validated; journal sequences are still reduced
  and validated. This is an optimization for trusted host administration, not
  protection against a root attacker able to manipulate filesystem metadata.
- Checksummed immutable heartbeat pointers support a separate Docker liveness
  probe. Rich research health/status remain separate quality checks.
- Supervisor reuses the status returned by each worker tick, avoiding two
  redundant status computations per loop.
- Restart emits a new heartbeat even in a previously visited sampling slot.

Full test suite, source lint, formatting, mypy across 86 files, and the exact
SHA image's network-disabled container smoke passed. A preceding candidate
passed the 1000-symbol/120-snapshot 256 MiB/0.5 CPU report smoke with peak RSS
66,732 KiB. This does not establish real-archive sustained sampling.

Local image config ID: `sha256:19383dcb375dece9d29a159dbe551c6623f3c842f9b5e9b026428df6a6f2ce05`.
VM-loaded image reports config ID
`sha256:87b7f374d9720b5639a13f6a90a950923df780f0f0e041007deb12881ce09176`.
Do not claim these IDs match. Transfer SHA256 and the two changed module bytes
were independently verified, and a read-only network-disabled source identity
check against the frozen session passed on the VM at the normal worker limits.

Source tar SHA256: `37a287b39854a6bf6775f518e99ecddb9f2b1be05b369151d39efec5566b2c79`.
Image tar SHA256: `308f3b413a9ddfd9d98f86687976f8ca4c9a10565e28ecf334b16c5a731d4d9e`.
Installed immutable release:
`/home/deploy/atlas-releases/1e829884cf9c455012f3e9ce4105c134a717e32f`.
Both workers were recreated using the research-only wrapper, retaining the
same control/session/archive. The wrapper returned success and both healthy.

## Live verification at 14:04 UTC — recovery incomplete

Both roles run the new SHA image and report healthy liveness. Rank restart count
0; outcome restart count 1, neither currently marked OOM-killed. There are 58
rank archives versus the previous 39. Journal status totals: 19 bounded,
35 partial, 3 complete, 1 failed. Last accepted rank arrival is still October 27
22:54:30 UTC. Thus green liveness has not established recovered collection.

Recent partial snapshots have 6–11 unknown rows, including omitted
`VELODROMEUSDT` and various stale rows. The frozen v2 policy accepts only stale
rows up to floor(1% of cohort), and rejects omitted rows. No policy, freshness,
denominator or existing record was changed to obtain a passing result.

## Real-archive timing result and next repair

Named diagnostic container `atlas-research-probe-20261029-1408` ran one
network-disabled read-only timing probe with UID10001, 256 MiB/0.5 CPU and a
300-second alarm. It completed with exit0 and OOMfalse and was removed explicitly.
Measured seconds at the real worker limits: parent cold79.750, warm0.997;
child cold3.838, warm1.020; guard cold31.423, warm14.252; status cold18.631,
warm12.499. Parent payload108,979,146 bytes; child4,207,915 bytes.
Thus repeated warm guards/status and full-history stop polling can consume the
entire30-second capture allowance and starve outcome passes.

Further local operational repair is committed in the isolated worktree as
`fb92272461f2578c6e491b7938700b37aa596320`:
anchored O_NOFOLLOW directory handles for cached journal metadata checks;
defensive copies of cached mutable event details; control-journal-only stop
polling; reuse verified session/events within status guard; single-stat storage
walks that tolerate atomic staging renames but still reject missing committed
evidence. All685 tests, source lint, formatting, typing across86 files and the
exact SHA image's network-disabled packaging smoke pass. Worktree is clean.
Local image config `sha256:f334183b347f35c6940cb1b09472f0f95e2c0d0504f7990a48e5354e4a9523ba`.
Source tar SHA256 `b42d7db6181e131a7f9ca9a0622712024c20548fcf27bb026f951624f647c355`;
image tar SHA256 `2b94e9e65bc419ee8065adafaf40c8a56fc3b1cc324bb1ac6cfd0670a85571fc`.
Both verified on the VM. Read-only root-owned release installed at
`/home/deploy/atlas-releases/fb92272461f2578c6e491b7938700b37aa596320` and image loaded.
Not yet applied to workers at this checkpoint. The second named probe
`atlas-research-probe-fb922724-20261029` is measuring the real archive using the
same bounded settings, read-only mount and300-second alarm. Imported hashes of
all four changed source modules pass. Source-identity and warm timing results
are pending; inspect logs/state and remove the exact probe when complete.
Second remote staging: `/tmp/atlas-release-fb922724.qZkIRg`.
Second local packaging: `/private/tmp/atlas-research-fb922724.B0XHv0`.
An earlier unnamed timing SSH connection was interrupted without usable output;
it is not timing evidence.

Remote transfer staging `/tmp/atlas-release-1e829884.5J8LiR` and local packaging
directory `/private/tmp/atlas-research-release.pZubcu` remain. Temporary OS Login
key `key2` in that directory was re-registered with TTL30m at about14:01 UTC.
Revoke the exact expiring keys using their OS Login fingerprints and delete the
generated private/public files when finished; preserve the original key
`1f336a04b970df5ff568c59cc37745fc104ca9204ed53b6fde2ca144326a822e`.
The earlier inspection key `325e1f8daa316c1e15c4b50b5e3b02530050f273410b5460cff3acd17a2d4b01`
was explicitly revoked, and its local keypair and scratch directory deleted.

Next: identify and repair remaining timing costs with real-archive regression
coverage; verify actual accepted five-minute samples and whole-cohort outcome
throughput before enrolling a fresh cohort. Preserve this study as incomplete.
Do not silently expand the frozen missingness policy or reset its clock.

## October30 resumption — supersedes pending deployment notes above

Jithin explicitly approved the versioned v3 missing-data rank policy. Work resumed
in the same isolated worktree at fb922724; main checkout prerequisite changes are
untouched. V3 implementation is currently uncommitted and not deployed. See
`docs/superpowers/specs/2026-10-29-research-missing-data-v3-proposal.md` for approved
scope and worktree `docs/research-missing-data-v3.md` for semantics and gates.

Live inspection confirms fb922724 was deployed October29 at14:30 UTC. Successive
real snapshots arrived14:34:07,14:39:06,14:44:05 UTC, then every5min through15:44.
These repaired timing, but v2 still correctly rejected the omitted original
VELODROMEUSDT row (and occasionally a second stale row). Final rank count74.

Both original reports are now finalized and payload checksums verified October30:

- 24h report4962d065-4fdc-43f2-ace5-d725416a4d61:16/513 complete,497 unknown;
  sha256 de3c6d0451c659f2935c0f9769d524b94b3cca8b1dac0e0753c7af2dbe65b558.
- 48h reportf0be9a86-9516-43e3-a585-e54faf69721d:289/513 complete,224 unknown;
  sha256 c9ea849f46b91625ba42264d9a648ffbe8288213d68c913b178e8f2c8ad1377e.
- Both have22 accepted ranks and insufficient sampling. Do not call the study
  successful or infer strategy edge from these incomplete outcomes.

The old workers' heartbeat slots clamp at720 (60h); after the window, cheap
liveness becomes stale while the expired workers remain alive. Both reports had
finished. At approximately04:22 UTC October30, strict control was setfalse and
only the two atlas-research services were gracefully stopped via the fb922724
wrapper. Both exited0; disabled-state health passed. Archives/reports remain.
No core production deployment, GitHub push, new capture or session occurred.

The isolated synthetic outcome fixture initially failed the2GiB free-space guard
because VM /tmp is a small tmpfs. It was moved to persistent disk at
`/home/deploy/atlas-diagnostics/outcome-capacity-20261029`; cross-filesystem copy
ownership was restored to UID10001. Neither failure changed production limits.
Current offline probe `atlas-outcome-capacity-probe-20261030b` uses fb922724,
networknone,256MiB/.5CPU,420-second alarm,513-member original archive copy and
mocked public klines. Guard passed; result pending. Remove exact probe/copy after
preserving logs; never merge synthetic evidence into the live archive.

Temporary key2 was renewed for30min early in this resumption (about04:12 UTC).
Revoke exact generated fingerprints and delete their keypairs before completion.
Prior packaging/staging cleanup remains outstanding as listed above.

Next gates: full v3 suite/static checks, exact-image smoke and bounded capacity;
preserve legacy policies and reject cross-version mixtures/forged raw evidence.
Only then package/install with collectionfalse, capture one fresh local parent
and child, transfer/enroll, enable its two research workers and verify real slots.

### October30 verification updates

The offline fb922724 throughput probe finished exit0/OOMfalse:9 complete original
symbol paths,27 simulated HTTP attempts,91.516 seconds including durable cleanup.
The90-second acquisition bound and30-request cap remain unchanged. This is
synthetic capacity evidence only, not a guarantee of real exchange availability.
Latest approved-disk snapshot `instance-20261025-1-europe-west10-c-20261030033725-j6513czm`
is READY, created03:37:26 UTC October30, source disk ID2527196753274568978.

V3 candidate image `atlas-screener:v3-verification-20261030-0424` built successfully:
configsha256 a819c9b8a36eade84fc572bde90baface1aa396fb9b09560201d9e4ee0c66319.
Network-disabled constrained packaging smoke passed: API ready, executionfalse,
stopped role remains idle after restart, pending role idle. Full final suite and
1000-symbol/120-snapshot constrained report capacity are running. Source lint,
typing86files, changed-file formatting and diffcheck pass. No candidate image
has been installed on the VM and no fresh live cohort has been captured yet.

### V3 release gate passed —04:25 UTC October30

Final clean-source run:702 passed,1 inherited Starlette/httpx deprecation warning,
115.23seconds. Static checks pass across86files. Exact immutable image packaging
smoke passes;1000-member/120-snapshot constrained report peakRSS66,556KiB,
networknone,256MiB/.5CPU. Local release commit
`3f9b36f5e5f34c892e886b6aca86bfb8fc860d57` is clean (not pushed).

Image configsha256 a819c9b8a36eade84fc572bde90baface1aa396fb9b09560201d9e4ee0c66319.
Imported86-file source fingerprint
e2b27acef5476e254e7b4dde41a9ebff2b04d6445e3b7d3176ccb94c61905ffb.
Local package `/private/tmp/atlas-v3-release.EgIUgL`:
source.tar.gz SHA256 a71481335a6b07546a6accb06c79038fd232dd7370fd35ee5b266df805bfd1c6;
image.tar.gz SHA256 0580d43c2e89eb8bef6832763d4e28058369c643ab7e39d5f65f5fdca47546e3.
Transferred to `/tmp/atlas-release-3f9b36f5.ZJJTjo`, both VM hashes verified.
First restrictive extraction safely refused the repository's four internal
symlinks; no release was installed by that attempt. Retry allows only those four
exact paths/targets, each verified to point at a regular archived internal file.
Installation pending at this checkpoint; collection remainsfalse, workers stopped.

Successful offline throughput logs retained root-only at
`/home/deploy/atlas-activation-artifacts/20261030/outcome-capacity-fb922724.txt`.
Probe container and isolated diagnostic/archive copy were explicitly removed;
original live data remains. Earlier failed extraction staging cleanup is pending.

### V3 installed and fresh capture started —04:29 UTC October30

Immutable3f9b36f release installed and image loaded on the approved VM; strict
control stillfalse and only old stopped containers exist. Network-disabled VM
probe verified the complete86-file imported fingerprint equals the local tested
image, UID10001, v3 policy defaults, and both v2 reports remain readable. PeakRSS
51,244KiB under256MiB/.5CPU. IPv4/IPv6 metadata nft guards verified unchanged.

One approved fresh capture started locally in named container
`atlas-v3-capture-20261030-0429`, exact3f9b36f image,3GiB/2CPU,UID10001,
read-only root,cap-dropALL,no-new-privileges,pids128,archive-only mount.
Archive directory `/private/tmp/atlas-v3-cohort.bdP3Vh/archive`, private enclosing
directorymode700. ALL mode,minscore68,max250evaluated,qualitytrue, no account
credentials or trading. Script immediately runs bounded shadow discovery after
parent completion, validates raw child evidence and parent/child checksums,
prints IDs/coverage.1200-second safety alarm. No recurring scan or hidden retry.
Inspect its logs/state; do not launch another capture if this one fails/partial.
Only after verified complete/bounded output transfer/enroll and enable workers.

### Fresh v3 cohort verified and transferred —04:30 UTC

Single capture completed exit0/OOMfalse, peakRSS1,156,956KiB (one-off3GiB limit).
Parent `79c0a603-20ca-4099-820d-132c8905ec14`, completed04:29:09.015966 UTC:
eligible547,liquid512,evaluated250,failed0,returned1.
Child `bfac3bda-dc08-4832-8e7c-b741d90fae46`, completed04:29:24.886444 UTC:
complete,original cohort512,fresh valid512,unknown0,v3 policy,zero qualified
shadow candidates. Current universe differs from the previous513-member study;
do not force a denominator or repeat capture until candidates appear. This study
can measure operations/core/control comparisons, not positive shadow edge with
an empty shadow candidate arm. The source anchor is04:30 UTC October30.

Local bundle `/private/tmp/atlas-v3-cohort.bdP3Vh/cohort.tar.gz` (10MiB), SHA256
f294a4c3309aa647be638df30e46ef83c1024812dbea9080bbc72c989aa31b60.
Transferred to `/tmp/atlas-release-3f9b36f5.ZJJTjo/cohort.tar.gz`.
VM import verifies bundle plus each manifest/payload checksum, allows only the
four canonical record files, refuses overwrite and retains the old study.
Root-only bundle retention intended at
`/home/deploy/atlas-activation-artifacts/20261030/cohort-79c0a603.tar.gz`.
One-off offline enrollment runs256MiB/1CPU to reduce preparation latency; the two
normal workers remain256MiB/.5CPU each. Enrollment/guard result pending.

### Fresh session enabled —04:31 UTC

Imported parent payloadsha2560dab5d8c115f97bf94d209c2aae2b7efe2cd13428ac2c59976e64ed4ef557dbe,
child payloadsha2566a97c947552bcc201d4448da945fb30b35ded63b134f94c4341131b6a7687004.
VM-side v3 enrollment and current source/storage guard passed. New session
`ec5b83a8-b892-473e-a0e6-69425fb35c5e`, enrolled04:30:39.912804 UTC,
anchor2026-10-30 04:30UTC,24h2026-10-31 04:30UTC,48h2026-11-01 04:30UTC.
Strict root-only control nowtrue for this new ID only.3f9b36f wrapper recreated
only the two research services and both reachedhealthy. First scheduled5-minute
rank at04:35UTC remains pending; do not claim accepted collection yet.
Temporary key2 renewed for30min about04:31UTC; revoke it explicitly at completion.

Runtime verification at04:32UTC passed: both3f9b36f workers correct new session,
healthy,0restarts,OOMfalse,UID10001:10001,256MiB/.5CPU,read-onlyroot,
cap-dropALL,no-new-privileges,non-hostnetwork,archive-onlybind,nohostports.
The opt-in flag lives in the host's strict deployment control; the actual worker
session ID is passed via command arguments, not container environment. A first
diagnostic assertion mistakenly expected that flag in the container Env; the
correct command/control boundary was then explicitly verified. No runtime fix
or configuration change was needed for that diagnostic expectation.

Task-generated OS Login fingerprints identified by matching local public keys:
`7d21c24e6e87a23fbe942d0ba1f7dc2bf138fd18a6dbdfbfe10462eb7ceffff8`
(older key without expiry) was explicitly revoked successfully at04:32UTC.
Current key2 fingerprint
`e1ea6bbc041d13541ff3600e6f19f60fb165671f812e75d69b9879c51a76e2a1`
remains short-lived until final verification/explicit revocation. Preserve original
`1f336a04b970df5ff568c59cc37745fc104ca9204ed53b6fde2ca144326a822e`.
Known unused failed-install staging is
`/home/deploy/atlas-releases/.v3-install-dd20mv0s`; remove only this exact directory.

### First real v3 slot verified —04:35 UTC

Snapshot `46da890a-d20e-4598-8138-99903d40b133`, scheduledslot1:
requested04:35:03.232060,arrived04:35:03.728430,completed04:35:03.734823 UTC,
complete,512valid,0unknown,errornull. Host verified payload checksum and journal
link. Both workers healthy,0restarts,OOMfalse. Slot0 was honestly marked missed
because deployment began after its04:30:30 request deadline; no backfill/reset.
The initial decision and source anchor remain04:29:24.886444/04:30UTC.

Offline frozen cohort inspection: coreSOONUSDT,long,score91.5,conviction80.0,
VCP_COMPRESSION_LONG; initial membership partitions10inside/502outside/0unknown.
No qualified shadow results. These are archived facts, not performance forecasts.
Original capture container was removed; local archived records remain. Exact old
transfer directories and `.v3-install-dd20mv0s` were removed after installed
releases and retained data were verified; no live evidence was deleted.

Final new-release offline throughput/report check started while waiting for
consecutive slots. Container `atlas-outcome-v3-probe-20261030`, exact3f9b36f,
networknone,256MiB/.5CPU,420-secondalarm. Only copied/checksum-verified fresh
parent,child,sessiondefinition,event prefixes and current rank snapshots live at
`/home/deploy/atlas-diagnostics/outcome-v3-capacity-20261030`. Mocked future
minute data and its report remain in this isolated copy, never live archives.
Result pending; retain logs and remove exact probe/copy afterwards.

Offline actual-data omission check passed without archive writes: removing initial
leaderPARTIUSDT yields v3bounded with all512retained,9certifiedinside,
501certifiedoutside,2unknown memberships (missing leader plus crossing boundary).
Explicitv2 remainspartial with all membershipsunknown, preserving its semantics.

First final-v3 throughput diagnostic failed its fixture, not a production rule:
it simulated48h data datedNovember1 while the kline parser used the realOctober30
clock. All30mockHTTP requests correctly produced no closed minute data and thus
0complete paths in18.652seconds. No real archive was modified. Correct only the
mock provider clock inside the isolated process, as in the acceptance tests;
never relax closed-candle validation. Failure log retained root-only at
`/home/deploy/atlas-activation-artifacts/20261030/outcome-v3-fixture-clock-failure.txt`.
Rerun container `atlas-outcome-v3-probe-20261030b` pending, same image/limits/copy.

Post-activation scheduled snapshot nowREADY:
`instance-20261025-1-europe-west10-c-20261030043725-y4b1sxq4`,
created2026-10-30 04:37:26.573UTC, exact source diskID2527196753274568978.
It is later than fresh cohort import/activation, unlike the preceding03:37backup.

### Second real slot and corrected v3 throughput passed —04:40 UTC

Second snapshot `5dae6878-ca53-4f6f-8ede-17933dbcad75`, slot2:
requested04:40:02.837176,arrived04:40:03.201123,completed04:40:03.209652 UTC.
Bounded,511valid/1unknown(ZRXUSDT),all512retained,errornull. Both workers healthy,
0restarts,OOMfalse. This is intentionally bounded evidence, not full completeness.

Corrected offline v3 throughput/report probe passed exit0/OOMfalse:
10complete synthetic48h symbol paths,30simulatedHTTPrequests,20.325seconds,
same256MiB/.5CPU cap. The v3 report retained512outcomes,10complete and sampling
insufficient as expected for a copied first rank sample. Prior invalid synthetic
attempts remain in the isolated copy, testing fair traversal after a failed prefix.
No simulated prices/outcomes/reports entered the live research archive.
Keep this evidence distinct from live Binance coverage, which is not due yet.
Retain corrected logs and remove probe/copy. Third scheduled slot04:45UTC pending.

At04:42UTC, the deployed image independently read/checksummed/reconstructed both
live rank archives under v3. Current source/storage guardoktrue. Complete sample:
10inside/502outside/0uncertain. Bounded sample:validfraction0.998046875,
9inside/501outside/2uncertain (one absent/stale value can also make a boundary
member uncertain). All512 retained; no invented exact ranks.

Corrected probe log retainedroot-only at
`/home/deploy/atlas-activation-artifacts/20261030/outcome-v3-capacity-passed.txt`.
Corrected probe container, isolated synthetic/archive copy and latest duplicate
transfer staging were removed explicitly. Live archives, immutable installed
releases, cohortbundle, failed/successful probe logs and approved disk snapshots
remain. No synthetic artifacts were merged into live measurement namespaces.

## Final October30 closeout — sustained collection verified

After interruption, the same session continued without restart/reset/rescan.
At09:41:37.359980UTC, an exact-image, network-disabled, read-only256MiB/.5CPU
audit independently reconstructed all62 live snapshots from raw checksummed
evidence:26complete,36bounded,62accepted,0excluded. Latestaccepted09:40:03.597421;
maximum observed gap303.909302seconds (cap600), maximum unknown values5/512.
PeakRSS163,676KiB. Source/storage guardoktrue;24h outcome stillnotdue.

Final scoped runtime/control/artifact check passed: only the two expected
atlas-research services, correct3f9b36f image/newsession,healthy,0restarts,OOMfalse,
unchanged limits/isolation and strict root-only control. Cohort bundle checksum
and retained probe log verified. Latest approved VM-disk snapshotREADY,
`instance-20261025-1-europe-west10-c-20261030093725-r0xnpcu5`,
created09:37:26.577UTC, source diskID2527196753274568978.

The existing temporarykey2 was briefly renewed for10min only to complete this
audit, then explicitly revoked successfully. OS Login fingerprint inventory
verified that only originalkey
`1f336a04b970df5ff568c59cc37745fc104ca9204ed53b6fde2ca144326a822e`
remains. Both task-generated fingerprints7d21.../e1ea... are absent. The four
generated local key/key.pub/key2/key2.pub files were deleted from
`/private/tmp/atlas-research-release.pZubcu`; source/image packages were preserved.
No future command should assume those ephemeral keys still exist.

No GitHub push, production API VM update, Vercel deployment or main-checkout
prerequisite overwrite occurred. Verified code remains in clean isolated branch
`codex/research-vm-direct-release`, commit3f9b36f. Main checkout recovery/spec docs
are uncommitted. Before any future production release, integrate/review that
verified branch with the preserved main-checkout prerequisite work explicitly.

Keep the VM running through original endpoints2026-10-31/2026-11-01 04:30UTC;
bounded outcome collection/retries may finish later (fixed12h retry windows).
Review coverage and matched-time benchmarks before any strategy-edge claim.
One core candidate/zero qualified shadow candidates cannot validate positive
hidden-discovery edge. This is not a security certification or zero-risk claim.
