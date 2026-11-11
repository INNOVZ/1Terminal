# Discovery corrections and historical replay

## Implemented behavior

- Standard API scans and explicit rescans default to a 320-symbol ceiling. The allocation remains 243 core, up to 57 anomaly discovery and up to 20 rotating watch symbols. Unfilled anomaly slots can leave the total below 320.
- Discovery Stage 2 has 45 score-ranked slots and 30 additional discovery/anomaly slots. L1–L5 readiness is recorded independently of L6 and final preliminary scoring. Strict VVI also has an independent promotion path. Overflow is counted and journaled. Final publication continues through the full engine gates and quality filters.
- The production ignition feature requires six-hour BTC-relative strength of at least 1.5 percentage points, turnover acceleration of at least 1.35 and a closed-bar 24-hour return between zero and 9%. Price must also hold EMA20 and four-hour taker flow must be at least 1.00 for its kinetic trigger. The exploratory emerging flag retains its separate, weaker research definition. The documented thresholds are implemented hypotheses with unestablished out-of-sample benefit.
- A VVI setup without another archetype can take the emerging-momentum path while holding EMA50 and POC within the specified range-position ceiling. The remaining domain gates still apply.
- Discovery L5 explicitly recognizes the documented quiet-coil or VVI paths. Discovery L6 requires the documented confirmation alongside its seven-day OI tolerance, or the existing confirmed fresh OI-inflection path. Resistance-ceiling, ignition and acute-liquidation checks remain active. Enriched L6 and the discovery derivatives verification flag agree.
- Retained radar publication requires five positively verified current gates. Unknown telemetry is preserved in audit events and excluded from the active candidate response. The publication version is `radar-v3`, which forces refresh of older caches before exposure. The 96-hour limit remains.
- SQLite recovery and deployment leave WAL/SHM lifecycle management to SQLite. Operational database failures propagate with their original evidence preserved. Tests cover lock and I/O errors.
- The research challenger explicitly selects the production discovery contract and consumes recorded feature-contract metadata correctly. Its policy is versioned as `discovery-v2-full336-rvol4h-watch20-admission-v2`. Supplemental acquisition remains opt-in and bounded, allowing up to 90 derivative calls and 450 total calls for the union of comparison-arm requirements. Existing 3,000-attempt, 600-second and 128 MiB public-wire limits remain.

The 30-slot discovery capacity can overflow. The implementation records exclusions and does not promise promotion of every eligible setup.

## Historical replay and provenance

The original public-market capture and checksummed study definition were retrieved read-only from the approved alfa2 VM. The capture semantic digest matches the definition's `bundle_sha256`. The definition digest matches the preserved cohort-summary evidence. Scan `04cf1e1d-19ee-4cd7-9520-fdf0875e86c2` therefore binds to study `71346822-f686-4bb1-99ec-0f56ef8f0ea1`.

Both counterfactual arms run `CryptoOpportunityScreener.screen()` with final quality filtering, frozen macro/quotes and an offline network guard. The baseline uses an isolated export of committed revision `09ca616`. The corrected arm uses the current source. Each output retains its engine identity and full decision journal.

The original capture acquired deep histories for 48 symbols. Complete 336-hour inputs for the wider 320 ceiling were never captured. Retrospectively retrieved hourly history is admitted only where its checksum matches the existing research manifest and its entire original closed-bar overlap equals the archive exactly. This extends 20 symbols. Missing four-hour, weekly, hourly and derivative requests stay unavailable. Subsequent four-hour forecasts are outside this one-frame replay.

|Measurement|Before corrections|After corrections|
|---|---:|---:|
|Selected at the original anchor|301|301|
|Later top-ten entrants selected for evaluation|39 of 55|39 of 55|
|Selected symbols with required replay inputs available|20|20|
|Selected symbols with unavailable required inputs|281|281|
|Stage-2 probe admissions|15|30|
|Verified-input engine publications|1000PEPE, INJ|1000PEPE|
|Verified publications among the 55 later entrants|0|0|

The engine's generic `evaluated` counter includes empty results after failed provider reads. The replay's explicit missing-input accounting takes precedence over interpreting that counter as complete evaluation.

NIGHT reaches derivatives enrichment in both arms and continues to fail enriched L6 activation. Its structurally ready status is preserved. INJ has seven-day OI +28.10%, four-hour OI −0.489%, 24-hour OI −0.0359% and four-hour taker ratio 0.9518. The corrected confirmation rule rejects that combination. PEPE retains five displayed passes. These are specific decision changes at the initial anchor.

This replay establishes implementation behavior and selection coverage. It supplies no measured predictive uplift for the 281 unavailable cases or later scan times. The original report's claims that all gainers began as quiet coils, that 16/55 passed the complete structural pipeline, or that named tokens would certainly publish remain unsupported by this replay.

## Reproduction

Run from the repository root with the original capture and definition files available locally.

```bash
.venv/bin/python -m scripts.research_post_observation_replay \
  --capture /tmp/atlas-post-observation-capture.jsonl \
  --definition /tmp/atlas-post-observation-definition.json \
  --output /tmp/atlas-corrected-new-replay.json
```

For the baseline, export `src` from `09ca616` into an isolated temporary directory and put that directory's `src` first in `PYTHONPATH`. Run the same replay script against the same evidence. Strict archive replay retains its source-identity guards. This separate counterfactual command labels the changed-source diagnostic explicitly.

Full outputs and hashes are retained in `docs/security/evidence/2026-11-11-discovery-corrections-summary.json` and its two companion replay files.

## Verification and delivery boundary

Focused regressions cover bounded 75-candidate enrichment with overflow, independent VVI admission, VVI threshold boundaries, discovery/legacy OI behavior, research feature-contract replay, strict unknown-gate publication, cache-version refresh and preservation of SQLite sidecars on failure. API lifespan tests isolate temporary state from the user's local cache.

The final full backend suite passed all 819 tests with RuntimeWarnings treated as errors. Ruff passed on the changed Python files, mypy passed for 91 source files and `git diff --check` passed. One existing Starlette/httpx deprecation warning remains. Replay engine identities, original study/bundle binding and companion output hashes were verified. Deployment-resource capacity and predictive uplift remain unverified. Changes remain local and uncommitted. The completed VM study is preserved. No deployment, enrollment or new collection run was performed.
