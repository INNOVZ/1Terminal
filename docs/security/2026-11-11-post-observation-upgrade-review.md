# Post-observation screener upgrade review

Follow-up implementation and replay are recorded in `docs/security/2026-11-11-discovery-corrections.md`. The findings below describe the reviewed pre-correction revision.

Reviewed local HEAD `09ca616` on November 11, 2026 against the supplied post-observation blueprint and the preserved Nov 8–10 study analysis. This review preserves the existing implementation. Deployment state and current market performance were outside this verification.

## Assessment

The production portal explicitly requests `feature_contract="discovery_v2"` and defaults to a 320-symbol scan. The upgrade therefore changes the live candidate-generation path. Complete 336-hour contraction, closed-four-hour RVOL, broader selection, the lower discovery turnover floor, upper-range anomaly ranking and the additional structural archetype paths are present.

These changes address identifiable discovery bottlenecks. Their effect on future top-gainer precision, early-detection recall and false positives remains unmeasured. The supplied report overstates both implementation completeness and historical predictive evidence.

## Findings

### P1 — Stage-2 admission still excludes part of the intended discovery track

`src/algotrader/application/crypto_screener.py`, lines 468–511, admits 45 score-ranked survivors and at most 15 quote-anomaly/readiness probes. The report specifies 45 plus 30 and a score threshold of 38. The actual outer preliminary threshold is `min(55, min_score)`, with an additional viability threshold inside `_evaluate_single`.

The probe readiness condition uses `_ready_waiting_symbols`. That set is populated only when L1–L5 pass and L6 fails. A candidate whose gates already pass is removed from this set. VVI/emerging status only boosts a symbol already in the readiness set. Consequently, a VVI setup or structurally ready setup that misses the score shortlist can still miss enrichment. More than 15 eligible readiness candidates also exceed the probe capacity.

Define admission semantics explicitly and exercise them through `screen()` with competing cohorts. Preserve bounded capacity and record which eligible candidates lose their slots. The report's guaranteed promotion claim currently has no implementation basis.

### P1 — Production VVI uses weaker strength conditions than specified

`crypto_screener.py`, lines 1227–1237, requires membership in `_emerging_symbols`, volume acceleration at least 1.35, price above EMA20 and four-hour taker ratio at least 1.00. The emerging helper in `domain/discovery_features.py` requires relative strength above zero and 24-hour return at most 9%.

The documented minimum relative strength of +1.5 percentage points and minimum 24-hour return of zero are absent. A local constructed closed-bar example returned `emerging=True` with BTC-relative strength +0.105 percentage points, 24-hour return −5% and volume acceleration 1.5. Those values satisfy the actual emerging dependency despite failing the documented strength conditions. Final publication still has additional gates.

Use one explicit ignition predicate shared by the journal, shortlist and scoring path. Keep exploratory emerging observations separately defined so an observational flag cannot silently substitute for the stronger production trigger.

### P1 — Deployment unlinks SQLite shared memory while the previous service can be running

`.github/workflows/deploy.yml`, line 127, unconditionally removes `/state/*.sqlite3-shm` before the subsequent build and Compose replacement. The existing container can still hold database connections at that point. `WatchlistService._ensure_table`, lines 80–86, also removes every existing SHM file after any SQLite operational error, with only an instance-local Python lock.

This does not establish exclusive ownership of the database. Concurrent connections can retain an old shared-memory mapping while new connections create a different mapping, undermining WAL coordination. The recovery branch also catches unrelated operational failures, including lock and permission problems. Existing code deletes only zero-length WAL files, which differs from the document's claim about all short sidecars.

Recovery needs an exclusive maintenance boundary, preservation of database/WAL evidence and checks scoped to the diagnosed failure. Normal deployment should let SQLite close and recover its own files. SQLite documents the shared-memory coordination requirement and corruption risks from divergent shared-memory areas. [SQLite WAL documentation](https://www.sqlite.org/wal.html)

### P1 — The research challenger does not select the production discovery contract

`scripts/research_discovery.py`, lines 148–152, constructs `DiscoveryScreener` with the inherited default `feature_contract="legacy"`. Its overrides correct volume history and watch selection, but discovery-gated lower turnover selection, watch reservation, VVI and readiness promotion remain disabled.

A direct local instantiation confirmed `_feature_contract == "legacy"`. For a newly recorded production request containing `feature_contract`, `run_discovery` also forwards that field to `screen()` without removing it. A direct signature check reproduced `TypeError` for that keyword. The regular replay and comparison paths already pop the field and pass it to the constructor.

Any forthcoming paired measurement needs an explicitly identified challenger matching the intended production strategy. Shared changes in archetypes and gate code also affect the current legacy mode, so that mode cannot independently represent the original historical strategy merely through its feature-contract label.

### P2 — Unknown gates bypass strict five-pass retention

`application/radar_service.py`, lines 31–51, deliberately retains unavailable candidates with `gates_passed=0`, unknown checklist items and `failed_gates=[]`. `domain/radar_lifecycle.py` also excludes unknown items from its failed-title list. The new portal filter, lines 295–332, removes explicit failures or age above 96 hours, allowing unknown candidates to remain visible.

This conflicts with the new report and AGENTS requirement that every visible retained candidate has five verified passes. The existing provider-failure test explicitly expects unknown retention. Enforce the chosen strict publication rule using positive verification of all five items while preserving unavailable episodes in audit state. Lifecycle checks also remain a distinct, more tolerant predicate than fresh six-layer admission, including a −8% seven-day OI floor in retention.

### P2 — Several claimed gate changes are absent or differ from the implementation

The active domain `screening_gates.py` L5 checks squeeze/bandwidth by archetype. It has no newly added `bandwidth <= .14 and VCR <= .95` branch or VVI input as described in the report. Its L6 universal seven-day OI threshold is −2.5%, with alternative four-hour and confirmed 24-hour inflection paths. The stated −3% threshold paired with nonnegative four-hour OI or 1.01 taker flow does not describe that implementation.

The checklist separately allows seven-day OI down to −3%, or a discovery 24-hour OI alternative, while still requiring L6 to pass. Archetype changes for tight midrange VCP and buyer-absorption fallback are present. Align the specification with the intended predicates before changing thresholds further.

### P2 — The reported recall and early-warning conclusions lack matching reproducible evidence

The preserved `2026-11-11-pre-gainer-deep-research.md` reports T−1h BTC-relative strength medians of +2.49 and +0.43 percentage points, and T−6h values of +1.11 and −0.26. The supplied report states +4.04/−0.27 and +1.58/−0.22. A reproducible alternate sample/time alignment supporting those replacements was not located in the inspected analysis code or evidence documents.

The original analysis found 36/55 later entrants below reconstructed four-hour EMA50 at the initial scan. It does not establish that all eventual gainers began as qualifying quiet coils. Its illustrative 16/55 number describes changes in membership of a contraction interval. It does not establish 16 complete L1–L5 passes or production publications. The report's 37/55 new L5 pass count also needs a saved, timestamped full-pipeline reproduction.

The older 38/55 figure measured selection coverage under a particular counterfactual allocation. At a budget of 320, current production computes 243 core plus 57 discovery plus up to 20 watch symbols. It does not implement the report's 256 plus 64 plus 20 allocation. Changed quotas and anomaly ranking require their own coverage calculation. A rotating hash watch pool gives bounded coverage and does not guarantee inclusion of TST or any named low-turnover token.

The observed −2.47% momentum-reference endpoint average is supported by the earlier study. Attribution of all avoided losses to anti-chasing requires individual rejection traces. Reference endpoint returns also do not measure a strategy with fills, exits and fees. Named winners' present-day scores cannot establish their historical publication before their first top-ten appearance.

## Verification

- Ruff passed for `src`.
- Mypy passed for 91 source files.
- The prescribed lifecycle, discovery and live-cache test groups passed, 49 tests total.
- Direct executable checks confirmed the 243/57/20 allocation, weaker emerging predicate and research feature-contract mismatch.
- Full backend suite passed, 803 tests. It emitted one existing Starlette/httpx deprecation warning.
- Frontend TypeScript checking passed with `tsc --noEmit`.

The document also references several nonexistent current paths, including `application/research_runner.py`, `application/research_discovery.py`, `application/radar_lifecycle.py` and `config/settings.py`. Relevant implementations currently live under `scripts`, `domain/radar_lifecycle.py`, `application/radar_service.py` and `config.py`. The study's 48-hour endpoint was November 10 at 13:50 UTC. The later 14:10 timestamp describes report publication. The archived slot accounting distinguishes 576 observed snapshots and one missed slot.

No production code, strategy threshold, deployment or study enrollment was changed during this review.

## Engineering order

1. Remove unsafe database sidecar handling from the active-service deployment path.
2. Make production admission and ignition match an explicit specification, including overflow behavior and unknown-gate publication.
3. Replay the upgraded production entry point against available point-in-time inputs. Report selected, enriched, published and early-detected coverage separately, alongside control-symbol admissions and false positives. Mark missing historical inputs unavailable.
4. Align the research challenger with that exact implementation before using it to measure the upgrade prospectively. Preserve the completed study and its original interpretation.

The immediate work is to finish and verify the candidate-generation changes using the existing evidence. A further unchanged observation run would leave these demonstrated gaps unresolved.
