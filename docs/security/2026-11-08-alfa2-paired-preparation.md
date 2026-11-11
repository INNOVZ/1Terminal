# Alfa2 prospective comparison — prepared November 8, 2026

Scope: prepare locally; the user deploys. No VM, production API, Vercel, Git push,
old research controls/workers, old cohort or anchor was changed.

## Release and frozen experiment

Image: `atlas-research-paired:2026-11-08` (Linux amd64).
Immutable ID: `sha256:435e5fe024b198ed23ca5990bf45d7be3cff662e835b4b8d531d7bb7aab2cfb5`.
Default: capture help only, `ATLAS_RESEARCH_ENABLED=false`, uid10001, no ports.
Local export/manifest paths are recorded in the final verification section below.

This integrates the current checkout's core and lifecycle fixes with the existing
research capture tooling; it does not replace the core with the older worktree's
strategy. Only a protected funding-source seam is added to the engine; default funding
selection is identical. The older isolated worktree and its uncommitted work remain intact.
The previously tested exact-primitive encoding fast path is included without changing
serialized representations. Production does not import these research scripts.

The preregistered policy is `paired-prospective-v1` in `scripts/research_prospective.py`:

- One original eligible canonical USD-M cohort, at most1000 members. Deep capture evaluates
  up to320 selected symbols; rank collection retains the whole original cohort, including
  symbols excluded by selection limits. This is not every Binance spot/futures listing;
  listings introduced after the frozen cohort are outside the denominator.
- Unchanged engine control versus quote-funding-carry research subclass. Raw funding rates
  and per-arm selection/enrichment budgets remain unchanged. No new thresholds, named-token
  exceptions, pending-enrichment arm or archetype-arbitration arm is introduced.
- Both engine publications and fresh radar views are frozen separately. The radar view
  applies the same 5/5 admission and16-card limit to each arm. Retained sidebar history is
  not silently treated as a fresh prediction. Shorts are retained in provenance but the
  top-gainer study's candidate metrics concern long predictions.
- Fixed momentum reference: ten largest observed rolling24h returns among original eligible
  quotes at capture. Already-gainers at the observation anchor are separated from later
  observed gainers. Arm sizes/unknown denominators are reported, not assumed equal.
- All inputs precede common issuance, after verified comparison. Enrollment must follow
  issuance within600s; stale captures cannot become prospective predictions. A fixed future
  five-minute boundary is reserved. A failed/missed first snapshot invalidates the study;
  it is never moved to a later start. Actual first observation confirms clock start.
- One public bulk ticker request per five-minute slot,577 slots including the anchor over48h;
  24h/48h sampled outcomes use the same anchor for every arm. No recurring candidate scans,
  account calls, API keys or order execution. The runner exits after the bounded window.
- Missing/stale/duplicate values remain unknown. Rank bounds certify top-ten membership only
  when omitted competitors cannot change it. Competition ranks preserve ties. Legacy
  `RankPolicy`/`ShadowPolicy` defaults are untouched; paired rank semantics are isolated.
- Reports separate rolling24h ranks from matched-anchor sampled price returns. They include
  first observed gainer time, sampled lead time, missed gainers, admission/rejection journals,
  endpoint coverage/returns and sampled maximum/minimum returns. These are not true intrabar
  highs/lows, executed fills, stop/target sequencing or continuous absence certification.
  The preregistered price false-positive measure is nonpositive sampled endpoint return;
  missing endpoints are unknown. Initial already-gainers are not early detections.
- Full original evidence, discarded/failed slots and cohort members remain on disk. Reports
  finalize once per horizon, after the endpoint sample deadline, rather than rebuilding on
  every tick. They do not declare predictive advantage or auto-promote a strategy.

## Capture timing repair

New captures use `atlas-public-evidence-v3`. V2/strict legacy archives keep their prior rules.
V3 requires source/HTTP hashes, exact adapter reconstruction and exact control replay as
before. Source price/funding observations must still be fresh at acquisition (60s), every
receipt must be available before the common cutoff, and the entire capture is bounded to
600s. Capture latency and issuance timestamps remain explicit. This fixes conflation of
acquisition freshness with a multi-minute scan's completion time; it does not pretend all
inputs arrived simultaneously or make stale source quotes acceptable.

Macro telemetry retains the earlier typed/time checks; underlying macro HTTP bodies are
not recorded. Delayed issuance and staggered supplemental derivatives acquisition remain
limitations. Fresh live provider acceptance may expose other data errors; do not waive them.

## User deployment: install disabled first

Approved destination only:
`alfa2-512717 / europe-west10-c / instance-20261025-174348`
(numeric instance ID6237690318220919058). Do not install this on the production API VM.

1. Transfer the provided image tar plus manifest and Compose file to a new release directory
   on this VM using your established secure transfer method. Check the tar SHA256 against
   the release manifest before loading it.
2. Load the image with `docker load --input /absolute/path/to/image.tar` and inspect its ID.
   It must equal the immutable ID above. Loading an image does not start collection.
3. Help-only verification, with no network or study mounted:

```sh
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges \
  sha256:435e5fe024b198ed23ca5990bf45d7be3cff662e835b4b8d531d7bb7aab2cfb5 \
  python -m scripts.research_prospective --help
```

Then report deployment for verification. Do not enable collection, enroll old evidence or
reuse the October research control file. Deployment is not observation activation.

## After deployment verification: acceptance and enrollment sequence

These are new-study operations, not installation steps. Use a newly created, empty
VM directory owned by10001:10001, separate from `/home/deploy/alfa-terminal/data/research`.
Verify available RAM/disk and backup coverage before acceptance. Keep the two terminal
legacy workers and all historical data unchanged during this preparation.

Use the exact immutable image throughout. With the new directory bound at `/evidence`,
read-only root, dropped capabilities, no-new-privileges,768MiB/no swap,1CPU,pids64:

1. Capture once:
   `python -m scripts.research_capture_evidence --execute-public-capture --max-symbols 320
   --max-supplemental 60 --max-attempts 3000 --deadline-seconds 600
   --output /evidence/capture.jsonl`
2. Offline preparation (`--network none`), immediately after capture:
   `python -m scripts.research_prospective prepare --bundle /evidence/capture.jsonl
   --study /evidence/study`
   This verifies/replays both arms and freezes the comparison; it does not enroll or fetch.
   Failed comparisons remain failures. Do not relax source, funding or freshness guards.
3. Once actual VM acceptance passes and activation is authorized, enroll within the frozen
   issuance window:
   `python -m scripts.research_prospective enroll --study /evidence/study --confirm-enrollment`
   This reserves a future anchor without HTTP. Start the worker before that boundary.
4. Configure the supplied isolated Compose profile with:
   `ATLAS_PAIRED_IMAGE` set to the exact immutable image ID,
   `ATLAS_PAIRED_STUDY_DIR` set to the new host study directory, and
   `ATLAS_PAIRED_COLLECTION_ENABLED=true` only at the authorized activation step.
   Start just its `observation` profile/service. The paired flag is distinct from the old
   `ATLAS_RESEARCH_ENABLED` host controls to prevent accidental inherited activation.
5. Verify `status` shows observation_started=true and the slot0 artifact has status observed.
   Record its fixed anchor and both UTC deadlines. If slot0 is missed/failed, stop and retain
   that failed trial; a genuinely fresh capture/decision is required for another study.

The sampler runs non-root/read-only,512MiB/no swap,0.5CPU,pids64,no ports. Its study mount
is the only persistent write mount. Stored snapshots survive restarts; locks refuse duplicate
workers, committed slots are idempotent, missed slots are not backfilled, and exchange
cooldown is persisted. Reports require no market HTTP and finalize once per horizon.
The profile has restart=no: it will not keep relaunching an invalid or completed study.
VM/daemon reboot or a failed worker therefore requires operator inspection and explicit
restart of this same enrolled study; anchors are preserved and gaps remain unknown.

## What is still unverified

Actual VM installation, real full-size provider acceptance, backup/restore of the new study
directory, first valid anchor snapshot and sustained48h acquisition remain deployment/runtime
gates. Local synthetic fixtures are not real enrollment, market performance or proof that
future top gainers will be found. Repeat preregistered periods before strategy promotion.

## Final local verification and export

- Final complete backend suite: **784 passed**, one existing Starlette warning,126.82s.
- Protocol tests: **20 passed**; includes once-per-horizon reporting and storage-before-HTTP.
- Touched research lint/format and Git whitespace checks passed.
- Capture/replay: **320 evaluated**,320 Stage1 survivors,45 shortlist+15 probes,60 supplemental
  snapshots,0 failures; complete in183.35s under768MiB/1CPU, no OOM.155,753,844-byte evidence;
  process peak RSS595,264KiB. This is synthetic, not market opportunity performance.
-48h archive/report: **1000 symbols x577 snapshots**, including stored raw receipts;
  806,892,176 bytes on disk. Write128.10s, report234.07s, peak RSS74,120KiB under512MiB/0.5CPU;
  complete, no OOM. The24h report separately passed289 snapshots in116.23s, leaving138.77s
  after its45s endpoint allowance before the next five-minute boundary. This is measured
  local capacity, not guaranteed real VM latency under concurrent production load.
- Final packaged source/tool/runner/rank hashes match the current checkout. Runtime Python
  3.13.18,pydantic2.13.4,httpx0.28.1; source hash
  `b182ba78f210d38b04f4206c7e18243513db8e4e07482e2c96b7d5dc707aac4a`.
- Network-disabled default-run smoke: disabled,0 HTTP attempts; no credentials present.
  Resolved Compose profile is non-root/read-only, has no ports and defaults disabled.
- The two completed local capacity containers were removed; evidence/output files retained.

Release folder:
`/Users/jithinjacob/Desktop/trdindicator/tmp/alfa2-research-release-20261108/`

Image tar: `image.tar`,170,956,288 bytes.
SHA256: `0ff68b2c7ae8cc90a85c4bf78fb5833dad395cd1a619421a946520e9951f09e8`.
The folder also contains `manifest.json`, `DEPLOYMENT.md`, the Compose profile and frozen
policy JSON. It is Git-ignored; large synthetic datasets are not included. Small test
logs, source hashes and reproduction drivers are retained under
`docs/security/evidence/2026-11-08-paired-*`.

The former worktree remains intact. Changes are uncommitted; no cloud deployment,
collection activation, new live cohort or anchor occurred during this preparation.
