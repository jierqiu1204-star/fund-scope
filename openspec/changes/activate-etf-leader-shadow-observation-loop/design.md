## Context

The production-safe leader modules already freeze the hypothesis registry, proxy formulas, PIT adapter, evaluation contract, evidence projection, and a bounded multi-phase continuation. The production PIT workflow already captures complete dual-ranking sources and materializes Stage A/B artifacts, but the deployed leader workflow refuses to start before 252 complete sources and has no production handler composition. The evidence endpoint therefore returns registry metadata with no accumulated observations.

The implementation must run on 2 cores and 4 GB, keep every command or continuation below 60 seconds, use no live provider in research continuation, and preserve the modular-monolith dependency direction.

## Goals / Non-Goals

**Goals:**

- Start immutable research observation accumulation with the first complete PIT source.
- Reuse existing PIT capture, adjusted-history loader, leader formulas, checkpoint lease, evidence table, and artifact store.
- Expose useful current-session research observations and exact progress before statistical validation is possible.
- Preserve cross-sectional peer correctness while processing no more than 20 ETFs per page.
- Mature forward outcomes idempotently without delaying later observations.

**Non-Goals:**

- Lowering daily 95-percent readiness, policy-v2 61-session 90-percent readiness, 252-session, 40-independent-date, fold, uncertainty, holdout, or promotion gates.
- Reconstructing historical taxonomy, membership, receipt timestamps, or provider facts from current metadata.
- Changing `final_score_v3`, production ranking, allocation, tracking, alerts, SMTP, or real execution.
- Adding parameter search, machine learning, proprietary-signal claims, or intraday fill simulation.

## Decisions

### 1. Separate collection eligibility from promotion eligibility

The workflow may collect a source when the source itself is complete and compatible. Promotion evaluation continues to require the existing statistical gates. The current preflight check that requires 252 sources before any handler runs moves to evidence/promotion evaluation.

This is preferred to weakening the sample threshold because collecting observations is not a claim of alpha. Waiting for 252 sources before collecting outcomes was rejected because it delays evidence by another forward window and leaves no operational feedback.

### 2. Use one immutable manifest per PIT source and code version

Each source produces a deterministic leader continuation manifest bound to source, universe, input, hypothesis, candidate, factor, cutoff, and code identities. Existing `EtfFactorExperimentCheckpoint` rows and the per-manifest replay artifact store remain the durable cursor and intermediate storage.

Completed observation and later matured-outcome reports are appended through the existing `EtfFactorExperimentEvidence` table under a separate `leader_tactics_observation_v1` experiment family. The final statistical report keeps `leader_tactics_shadow_v1`; the evidence projection reads both families so a newer daily observation cannot hide a completed final report. It deduplicates observations by signal date and observation identity. A new mutable observation table is avoided because the existing evidence model already supplies unique manifests, hashes, provenance, and research-only payloads.

### 3. Materialize compact primitives in two bounded passes

The FEATURES continuation reads no more than 20 ETFs and their required adjusted history, validates source-bound membership, taxonomy, sector, baseline, and regime facts, and persists compact primitives rather than full bar histories. Only after all pages for the signal date are sealed does finalization compute peer percentiles, clone policy, frozen candidate scores, and the complete current-session observation.

This preserves cross-sectional correctness and memory bounds. Scoring each page independently was rejected because peer percentiles and Top N cohorts would depend on batch boundaries.

### 4. Bind metadata to the published source snapshot

Baseline score and current-session sector, taxonomy, tracked-index, issuer, theme, and regime facts come only from the complete source signal run and its items when their observed timestamps and contract identities satisfy the replay cutoff. Missing fields remain explicit exclusions. The workflow does not consult mutable current asset metadata to repair an older source.

Future factual sessions may accumulate from the moment this capture contract is deployed. Historical sessions are admitted only if equivalent immutable facts and receipt times already exist.

### 5. Keep outcome maturation as a separate due unit

New observation sources take precedence over already-complete units, while the oldest pending five-session or ten-session outcome is matured when its adjusted facts become eligible. The outcome identity includes the original observation hash, outcome cutoff, execution/cost contract, and code version. It is append-only and cannot consume the locked holdout.

### 6. Compose the lane in workflows and schedule it independently

Core feature and evaluation logic remains in `strategy_lab`; database loading and cross-domain composition live in `app.services.workflows`; scheduler and admin routes only invoke one bounded continuation. Separate evidence-API and continuation flags remain fail-closed. Read-only evidence may be enabled before continuation, but observation work starts only after complete PIT sources exist.

## Risks / Trade-offs

- [Current source items may lack factual taxonomy or regime fields] → Persist exact per-field exclusions and add no fallback; future complete source capture can include compatible facts.
- [A 1,490-ETF source needs many pages] → Keep 20-ETF pages, compact primitives, monotonic cursors, and due-aware oldest-source scheduling.
- [Outcome append rows could double-count a signal date] → Bind every row to the original observation hash and select the latest compatible maturity version per signal date.
- [Existing evidence rows use the final-report schema] → Add an explicitly versioned observation report kind and retain backward-compatible final-report projection.
- [Leader work could contend with PIT capture] → Separate leases and cadence, one page per trigger, no provider calls, and skip when server resource admission fails.

## Migration Plan

1. Add observation report schemas, checkpoint handler composition, and tests with all production flags disabled.
2. Deploy the read-only evidence projection and verify existing disabled or not-materialized states remain compatible.
3. Finish the shared readiness rollout and obtain one complete policy-v2 PIT source at daily 95-percent and 61-session 90-percent coverage.
4. Enable the evidence API, then enable one bounded leader continuation with a frozen code version.
5. Verify one source progresses monotonically, produces either a complete zero-match observation or a complete research match list, and leaves production tables unchanged.
6. Enable due-aware scheduling and record at least three different factual sessions before closing rollout tasks.

Rollback disables leader continuation and scheduling while retaining immutable artifacts and evidence. It does not delete observations, rewrite PIT sources, or change production ranking state.
