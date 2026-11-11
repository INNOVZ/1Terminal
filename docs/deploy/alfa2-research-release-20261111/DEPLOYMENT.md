# Discovery comparison release

Target scope is `alfa2-512717/europe-west10-c/instance-20261025-174348`.
The user performs deployment. Each study uses a new directory. Preserve completed studies
and their original images for strict replay.

## Release identity

Use the immutable linux/amd64 image ID and tar checksum in `manifest.json`. The local
export is `tmp/alfa2-research-release-20261111/image.tar`. Load and verify the same image
on each machine that captures, prepares or collects. Source/runtime guards require the
same runtime throughout a new study.

Collection defaults to false. The composition defines independent observation and
offline reporting services. It publishes no ports and runs with a read-only root,
unprivileged UID, dropped capabilities and bounded resources.

## Capture and prepare

Run the one-off capture on a machine with verified capture capacity. The GCP observation
VM requires a separate capacity check before attempting this operation there. Example
commands assume the verified image is available locally and `/absolute/new-evidence`
is an empty writable directory owned appropriately for UID 10001.

```sh
docker run --rm --platform linux/amd64 --read-only --cap-drop ALL \
  --security-opt no-new-privileges --memory 1536m --memory-swap 1536m \
  --cpus 1 --pids-limit 64 \
  --mount type=bind,src=/absolute/new-evidence,dst=/evidence \
  "$ATLAS_PAIRED_IMAGE" python -m scripts.research_capture_evidence \
  --execute-public-capture --discovery-v2 --max-symbols 320 \
  --max-supplemental 120 --max-attempts 3000 --deadline-seconds 600 \
  --output /evidence/capture.jsonl

docker run --rm --platform linux/amd64 --network none --read-only \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 1536m --memory-swap 1536m --cpus 1 --pids-limit 64 \
  --mount type=bind,src=/absolute/new-evidence,dst=/evidence \
  "$ATLAS_PAIRED_IMAGE" python -m scripts.research_prospective prepare \
  --bundle /evidence/capture.jsonl --study /evidence/study
```

Preparation must finish within 600 seconds after evidence completion. Capture-to-issuance
latency and transfer time therefore matter. Synthetic capacity measurements establish
local test behavior. A fresh real capture still needs verification before enrollment.

For a subsequent capture at the planned four-hour cadence, optionally mount the earlier
study read-only and add `--previous-study /previous-study` to `prepare`. Both studies
must use this release. Context older than eight hours or issued after the new capture
is rejected. Each cohort has its own issue time, enrollment and outcome anchor.

## Enroll on the observation VM

Transfer the prepared study, original capture and verified image inside the approved
VM scope. Verify checksums and available VM resources. Sampling reserves 512 MiB and
0.5 CPU, while reporting reserves 256 MiB and 0.25 CPU. Account for the OS and existing
processes when checking the host's available capacity.

Enrollment must occur within 600 seconds after issuance. The following command reserves
the next five-minute UTC anchor. Prepare a new decision if its enrollment window expires.

```sh
docker run --rm --platform linux/amd64 --network none --read-only \
  --cap-drop ALL --security-opt no-new-privileges \
  --mount type=bind,src=/absolute/new-study,dst=/study \
  "$ATLAS_PAIRED_IMAGE" python -m scripts.research_prospective enroll \
  --study /study --confirm-enrollment
```

## Start the two workers

Set the following variables to the verified image and newly enrolled study directory.
`ATLAS_PAIRED_COLLECTION_ENABLED=false` keeps the composition disabled.

```sh
export ATLAS_PAIRED_IMAGE=sha256:REPLACE_WITH_MANIFEST_IMAGE_ID
export ATLAS_PAIRED_STUDY_DIR=/absolute/new-study
export ATLAS_PAIRED_COLLECTION_ENABLED=true
export ATLAS_PAIRED_PROJECT=atlas-discovery-cohort-20261111
docker compose -p "$ATLAS_PAIRED_PROJECT" -f compose.research-study.yaml --profile observation up -d
docker compose -p "$ATLAS_PAIRED_PROJECT" -f compose.research-study.yaml --profile observation ps
```

Use a distinct Compose project name for each study and check resources before overlapping
cohorts. This keeps a later deployment from replacing an earlier cohort's containers.

Confirm slot zero is observed and health reports `collecting`. Only a valid slot zero
starts the observation. Failed or missed anchors invalidate the study and never slide.
Use `status` for an audited state read and `health` for lightweight operational health.

The observation worker ends after the fixed 48h window. The report worker finalizes each
horizon once using its independent lock. Restarting reporting reuses valid existing
reports. It can be rerun with the offline `report --horizon 24` or `--horizon 48` CLI.

## Interpret the comparison

Inspect control, funding-carry and discovery engine/radar results separately from watch
selection, readiness and emerging views. Watch and readiness observations are explicitly
non-actionable. A watch selection is an evaluation-coverage reference.

Use `observed_slots`, `missed_slots`, `failed_slots`, `absent_slots` and
`unobserved_slots` to assess collection. `recorded_slots` counts files, including failure
records. Feature-unavailable symbols are listed separately. Promotion requires comparative
evidence across additional periods, including adverse moves and alert load.

This handoff prepares one cohort at a time. Recurring candidate capture is a separate
deployment workflow. Production promotion and real VM acceptance remain pending.
