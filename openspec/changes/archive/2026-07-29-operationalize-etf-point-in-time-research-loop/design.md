## Context

FundScope has separate research and actionable ranking surfaces, immutable factor experiment contracts, point-in-time panel builders, Stage A and Stage B ranking replay primitives, three frozen ranking candidates, forward-outcome and ranking-validation functions, and a pure ETF action replay lifecycle. The missing piece is an operational coordinator that runs these components in order, persists one evidence identity, resumes safely on the 2-core/4-GB production host, and exposes results without merging research simulations with live decisions.

The current production readiness change remains responsible for authoritative-universe and adjusted-history synchronization. This change consumes only data that already satisfies its provenance rules and does not lower either 95 percent publication gate.

## Goals / Non-Goals

**Goals:**

- Materialize reproducible point-in-time ranking and policy-shadow evidence through one bounded, resumable, single-worker workflow.
- Make Top10 five-session paired net excess the only ranking promotion endpoint and Top20 ten-session action-cycle benefit the policy endpoint.
- Operationalize existing factor diagnostics, walk-forward validation, uncertainty, multiplicity, holdout, and promotion gates.
- Keep production ranking, research replay, policy shadow, live notification, provider delivery, and confirmed execution independently identifiable.
- Fail closed with stable reasons when historical visibility, adjusted data, coverage, future windows, or sample counts are insufficient.

**Non-Goals:**

- Changing `final_score_v3`, current factor weights, allocation rules, alert thresholds, or notifier behavior.
- Inferring historical membership, publication timestamps, quotes, deliveries, or executions.
- Using raw Sina/efinance prices, AI-generated values, current-vintage survivors, or current rankings as historical decision inputs.
- Running grid search, training machine-learning models, or creating a second backtest engine.
- Treating completion of software tasks as evidence that real-data promotion gates passed.

## Decisions

### 1. Reuse one Strategy Lab coordinator

Add one coordinator above the existing replay input, Stage A, Stage B, frozen candidate, forward outcome, factor evidence, ranking validation, and action replay modules. It owns orchestration and persistence only; scoring, allocation, risk, and notification rules remain in their domain modules.

Alternative considered: add another portfolio backtest service. Rejected because it would duplicate execution and action lifecycle rules and weaken evidence identity.

### 2. Separate immutable run identity from resumable execution state

The immutable manifest includes ranking contract, candidate registry, cutoff, universe and input hashes, adjusted price basis, feature schema, execution and cost policies, chronological split, purge/embargo, bootstrap seed, promotion thresholds, code version, and holdout identity. Checkpoints contain only mutable cursor, phase, batch-size profile, counts, timings, and failure summaries.

Resuming with a material manifest mismatch requires a new run identity. Completed artifacts are append-only; retrying a page is idempotent by run, phase, signal date, candidate, and page identity.

### 3. Enforce factual point-in-time visibility

Historical cohorts use authoritative membership facts and adjusted rows whose recorded availability is no later than the declared signal cutoff. Missing or later-backfilled evidence produces an exclusion; the coordinator never substitutes current membership or rewrites availability time.

The 61-session count is a feature warm-up only. Formal promotion additionally requires at least 252 eligible point-in-time sessions and 40 non-overlapping primary samples.

### 4. Freeze candidate and endpoint policy

The only ranking candidates are the existing baseline Top10, Top10 hysteresis with rank-15 buffer/max-one-replacement/minimum-three-session hold, and hysteresis plus the frozen regime/liquidity gate. No runtime parameter grid is accepted.

Ranking selection uses paired Top10 five-session net excess against the frozen baseline on common support, T+1 eligible adjusted-close entry, five-session adjusted-close exit, and 5 bps fee plus 5 bps slippage per side. Top5/20 and 1/3/10-session cells are exploratory.

Policy evaluation separately uses the existing Top20 ten-session action-cycle contract and its declared execution model. Directional stop/profit accuracy is secondary to cost-adjusted action-cycle benefit.

### 5. Apply promotion gates without automatic promotion

Evidence is `insufficient_data` until dual production coverage is at least 95 percent, 252 eligible sessions and 40 independent primary dates exist, at least three chronological folds complete, and all provenance and quality checks pass. A candidate is promotion-eligible only when the Holm-adjusted 95 percent primary interval is above zero, required fold/regime signs are stable, maximum drawdown is no more than two percentage points worse than baseline, and coverage, non-finite, concentration, clone-policy, exclusion, and raw-price gates pass.

The holdout is consumed once per immutable experiment. Passing gates only permits a separately reviewed score-version proposal; this workflow never changes production.

### 6. Keep execution bounded and adaptive

One database lease protects each run. Every provider/data operation and command has a 55-second hard limit. Batch size starts at 10, remains between 5 and 20, halves after timeout or memory pressure, and increases by at most 5 after consecutive healthy pages. Checkpoints are committed after every completed page so the scheduler can continue later.

No workflow invocation loops until completion. A scheduler trigger performs at most one bounded continuation and reports remaining work.

### 7. Persist evidence through existing evidence tables

Reuse `etf_factor_experiment_evidence` and `etf_factor_experiment_checkpoints` for immutable manifests, phase artifacts, aggregates, exclusions, and continuation state. Policy-shadow artifacts reuse the action replay artifact contract and are linked by experiment/run hashes. Additive fields or tables are introduced only where the current JSON evidence envelope cannot preserve provenance or query status safely.

Alternative considered: write replay results into production signal, position, alert, or notification tables. Rejected because simulated evidence must not become a live business fact.

### 8. Extend the existing evidence API additively

Evidence responses expose ranking source kind, policy mode, cutoff, manifest hash, coverage dimensions, exclusions, primary/exploratory labels, samples, costs, intervals, holdout state, notification provenance, execution provenance, and a stable unavailable reason. Existing clients remain compatible because all additions are optional and old evidence is explicitly marked incompatible or legacy.

The workbench displays production ranking, research replay, policy shadow, live notification, and confirmed execution in separate sections. It never infers stronger provenance from a weaker state.

## Risks / Trade-offs

- [Real PIT history is too short] → Persist `insufficient_data`, continue prospective collection, and do not tune or promote.
- [Backfilled adjusted rows look historically usable] → Compare recorded availability with the signal cutoff and exclude later-visible rows.
- [A 55-second slice ends mid-phase] → Commit only complete idempotent pages and resume from the last durable cursor.
- [Adaptive batching changes output] → Keep batch size outside the immutable research inputs and test identical hashes/results across page sizes and interruption points.
- [Multiple testing creates a false winner] → Permit at most three candidates, pre-register the primary endpoint, apply Holm correction, and lock the holdout.
- [Research actions leak into production] → Use pure lifecycle adapters plus table-snapshot and dependency-boundary tests.
- [UI overstates evidence] → Require explicit source/provenance fields and stable unavailable states in API and frontend tests.

## Migration Plan

1. Complete and verify the active production readiness coordinator without lowering its dual 95 percent gates.
2. Deploy additive evidence persistence and the research-loop coordinator disabled for automatic scheduling.
3. Run deterministic fixtures, interruption/resume tests, and production-shaped read-only cohorts.
4. Enable one bounded continuation per scheduler trigger; allow `insufficient_data` evidence to accumulate prospectively.
5. Enable policy-shadow materialization only after complete ranking cohorts exist.
6. Expose the additive evidence API and separated frontend sections.
7. Keep `final_score_v3` and all production consumers unchanged until a separately approved promotion proposal passes every gate.

Rollback disables the research-loop trigger and API feature flag, retains append-only evidence for audit, and leaves production ranking, positions, alerts, and notifications unchanged.
