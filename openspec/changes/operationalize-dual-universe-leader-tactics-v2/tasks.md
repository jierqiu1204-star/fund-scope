## 1. Freeze the V1 baseline and implementation boundary

- [x] 1.1 Verify the archived V1 artifacts and strict-validation record, preserve its five unresolved real-environment acceptance tasks as carryover, and do not reopen or claim those tasks complete.
- [x] 1.2 Record the archived V1 source registry, candidate registry, manifest, holdout, and evidence hashes as immutable V2 baselines.
- [x] 1.3 Map the existing PIT replay, forward-outcome, cost, factor-diagnostic, evidence, and bounded-continuation components that V2 will reuse, and document the production write boundaries.
- [x] 1.4 Add a V2 feature flag and separate registry/storage namespace that default to disabled without changing existing ranking or alert behavior.

## 2. Register source evidence and frozen V2 contracts

- [x] 2.1 Create the compact source registry with article identity, publication time, capture hash, concise disclosed-rule assertions, unavailable proprietary elements, and non-equivalence wording; do not copy the full local article corpus into the repository.
- [x] 2.2 Freeze the chronological source split through 2026-07-15, the 2026-07-16 through 2026-07-29 validation interval, and the one-time 2026-08-03 locked case.
- [x] 2.3 Register exactly `leader_breakout_proxy_v2`, `base_launch_proxy_v2`, and `former_leader_repair_proxy_v2` with canonical formulas, thresholds, windows, score weights, missing-value rules, and immutable hashes.
- [x] 2.4 Register the `preparing`, `confirmed`, and `invalidated` transition contract, simulated execution timing, costs, ETF clone policy, and universe-specific peer semantics.
- [x] 2.5 Add registry validation that rejects runtime threshold, weight, window, candidate, holdout, or proprietary-signal substitutions under the V2 identity.

## 3. Add A-share point-in-time research data

- [x] 3.1 Add additive persistence for A-share authoritative-universe snapshots with effective time, receipt time, source cutoff, provider identity, listing state, board, and exclusion reasons.
- [x] 3.2 Add additive persistence for A-share theme membership with effective interval, first receipt time, taxonomy version, source, confidence, and supersession linkage.
- [x] 3.3 Extend the approved adjusted-history store or adapter to support A-share total-return-adjusted OHLCV, adjustment identity, receipt time, revision identity, finite checks, and continuity checks.
- [x] 3.4 Enforce source governance so Sina/efinance raw prices and Tencent audit-only data cannot increase decision coverage or populate formula inputs and outcomes.
- [x] 3.5 Implement PIT reads that require both effective time and factual receipt time at or before the cutoff and preserve later revisions as separate audit facts.
- [x] 3.6 Mark imported history without factual receipt time as `historical_research_only` so it cannot count toward factual forward-session promotion gates.
- [x] 3.7 Implement a deterministic single-worker collector with adaptive batches of 5 to 20 securities, durable idempotent checkpoints, bounded memory, provider cooldown, and a hard continuation budget of at most 55 seconds.
- [x] 3.8 Expose A-share universe, adjusted-daily, history-tier, PIT-theme, provider-health, raw-violation, non-finite, and exclusion readiness metrics without fallback inflation.

## 4. Implement the dual-universe formula engine

- [x] 4.1 Define a normalized, finite, cutoff-bound session-frame contract consumed by a pure formula engine with no database or network calls.
- [x] 4.2 Implement the common hot-theme score, core-leader score, adjusted MA5/10/20 alignment, 120-session peak-volume gate, minimum-five-peer gate, and three-and-20-percent batch-breadth gate.
- [x] 4.3 Implement `leader_breakout_proxy_v2` with the prior-20-session adjusted-high breakout and frozen finite score components.
- [x] 4.4 Implement `base_launch_proxy_v2` with the three-session MA5/MA10 cross, MA20 position and slope, ATR compression, and symmetric `abs(close - MA20) / ATR20` overextension gate.
- [x] 4.5 Implement `former_leader_repair_proxy_v2` by reusing the frozen V1 prior-leader, drawdown, positive-stabilization, compression, and symmetric-overextension semantics plus the V2 hot-theme gate.
- [x] 4.6 Implement the A-share adapter using factual PIT theme peers and the ETF adapter using PIT theme/tracked-index peers after clone deduplication.
- [x] 4.7 Persist every passed and failed gate, peer count, breadth denominator, component score, source cutoff, feature hash, and stable exclusion reason.
- [x] 4.8 Add boundary, missing-value, non-finite, history-tier, peer-count, clone, and cross-adapter parity tests for all frozen formulas.

## 5. Materialize causal lifecycle and evidence

- [x] 5.1 Add append-only V2 run-manifest, candidate-observation, state-transition, holdout-use, and revision-audit persistence with idempotent uniqueness rules.
- [x] 5.2 Implement the lifecycle reducer that captures the immutable signal-day high, confirms only on a later eligible close above that high and MA5, and invalidates on an eligible close below MA5.
- [x] 5.3 Implement next-eligible-close simulated entry and exit with declared non-zero costs, and reject attempts to infer intraday fills from daily OHLCV.
- [x] 5.4 Make compatible checkpoint resumes retain the manifest identity and reject incompatible registry, taxonomy, adjustment, cost, or state-policy checkpoints.
- [x] 5.5 Return stable unavailable reasons for all data, formula, transition, outcome, manifest, and sample gates with exact numerators and denominators.
- [x] 5.6 Add production-boundary guards and tests proving V2 runs cannot mutate rankings, score weights, allocations, positions, alerts, notification/SMTP state, or execution state.

## 6. Connect source-label and economic validation

- [x] 6.1 Build the source-label set only from explicitly supported asset/date/theme facts, preserve positive/core/unresolved distinctions, and leave unmentioned assets unlabeled.
- [x] 6.2 Connect the frozen V2 candidates to the existing PIT replay, forward-outcome, cost, factor residual, concentration, and block-bootstrap components instead of creating another backtest engine.
- [x] 6.3 Implement the A-share five-session cost-adjusted paired net-excess endpoint against decision-eligible same-theme equal-weight peers on common support.
- [x] 6.4 Preserve the ETF Top10 five-session paired net-excess endpoint against the frozen comprehensive-ranking Top10 baseline and label other horizons exploratory.
- [x] 6.5 Report source-label precision/recall separately from frequency, turnover, cost drag, drawdown, concentration, regime dependence, coverage, residual factor overlap, and primary economic outcomes.
- [x] 6.6 Enforce 252 factual PIT sessions, 40 non-overlapping primary dates, three chronological folds, purge/embargo, Holm-adjusted 95-percent block-bootstrap lower bound above zero, one-time holdout, and all provenance/stability gates before promotion eligibility.
- [x] 6.7 Evaluate the 2026-08-03 case once under the frozen formulas and factual cutoff, checking whether `603039` and `002131` surface naturally without passing their identifiers into screening logic.
- [x] 6.8 Persist locked-case match, mismatch, or unavailable evidence and reject any V2 tuning or repeated holdout access after the case is opened.
- [x] 6.9 Add no-lookahead, partial-label, common-support, transaction-cost, non-overlap, purge/embargo, multiple-testing, holdout-once, and insufficient-data tests.

## 7. Add the read-only candidate and evidence API

- [x] 7.1 Add a bounded read endpoint for `universe=etf|ashare`, `formula=all|breakout|base_launch|former_leader_repair`, `state=preparing|confirmed|invalidated`, `as_of`, and stable cursor pagination.
- [x] 7.2 Return deterministic candidate rows with code, name, theme, formula, state, score, gate facts, signal/transition dates, cutoff, source provenance, exclusions, and manifest hash.
- [x] 7.3 Return a summary envelope with per-layer coverage, provider health, candidate/exclusion counts, registry identity, availability state, economic evidence, notification provenance, and execution provenance.
- [x] 7.4 Ensure API reads use only persisted materializations, never trigger provider work, never fall back between universes, and distinguish empty results from unavailable evidence.
- [x] 7.5 Add authorization, filter validation, page-size cap, deterministic pagination, stale/cancelled request, serialization, and unavailable-reason API tests.

## 8. Extend the research evidence workbench

- [x] 8.1 Add an `ETF / 个股` switch to the leader-tactics evidence panel, default it to ETF, preserve `as_of`, and keep each universe's counts and availability separate.
- [x] 8.2 Add all/breakout/base-launch/former-leader-repair formula filters and preparing/confirmed/invalidated lifecycle filters with deterministic pagination.
- [x] 8.3 Render candidate rows and expandable gate details including source assertion, proxy identity, formula version, cutoff, provider, exclusion, manifest, and transition evidence.
- [x] 8.4 Display source-disclosed rules beside the transparent proxy and prominently state that the proprietary takeoff signal was not reproduced and the output is research-only, not investment advice.
- [x] 8.5 Keep candidate lifecycle, historical replay, validation, notifications, provider delivery, and user-confirmed execution visually and textually separate.
- [x] 8.6 Add loading, cancellation, empty, partial-coverage, incompatible, stale, insufficient-data, and locked-case-mismatch states in beginner-readable Chinese.
- [x] 8.7 Add frontend contract and interaction tests for universe isolation, filters, pagination, provenance, research-only wording, and no fallback behavior.

## 9. Operational acceptance and rollout

- [x] 9.1 Run migration and rollback checks on an isolated test database and verify additive storage does not alter V1 or production ranking state.
- [x] 9.2 Run bounded interruption/resume tests with batch sizes 5, 10, and 20 and verify identical hashes, no duplicates, maximum memory, single-worker leasing, and at-most-55-second continuations.
- [x] 9.3 Run focused backend domain-boundary, PIT, formula, lifecycle, evidence, validation, and API test groups, with every command protected by a hard timeout no greater than 60 seconds.
- [x] 9.4 Run focused frontend typecheck and leader-evidence tests, with every command protected by a hard timeout no greater than 60 seconds.
- [x] 9.5 Run Ruff only on changed Python files and run strict OpenSpec validation for this change, each under a hard timeout no greater than 60 seconds.
- [x] 9.6 Deploy with collection, screening API, and UI feature flags disabled; verify migrations, health, scheduler non-overlap, resource headroom, and production-state hashes.
- [x] 9.7 Enable bounded factual capture first, then research materialization, then the read-only API/UI after readiness checks; record provider health, coverage, candidate counts, unavailable reasons, and rollback commands.
  - Production acceptance, 2026-08-08 Asia/Shanghai: deployed commit
    `6786d5ba996b46fb586cecf1ece7034a8f0401e9`; `/api/health` reported
    `app=ok` and `db=ok`. Capture, A-share materialization, API, and UI were
    enabled in that order; ETF V2 materialization remained disabled.
  - Friday feature session `2026-08-07` was materialized on decision date
    `2026-08-08` as `post_close_watchlist`, with next eligible session
    `2026-08-10`. Manifest
    `4dd60f0e5b82426025c47f20d3e59af14f8b46fa95ec7b7af0e5e91e3f59c10d`
    contains 5,540 assets and 16,620 observations: 15,700 available and 126
    qualifying, all under `former_leader_repair_proxy_v2`; breakout and base
    launch each had zero qualifying observations. The prior incompatible
    membership-hash manifest remains append-only audit evidence and is not the
    newest API-selected manifest.
  - Readiness at the accepted cutoff: authoritative universe 5,540/5,540
    (100%); adjusted daily 5,534/5,540 (99.89%); 61 sessions 5,497/5,540
    (99.22%); 120 sessions 5,455/5,540 (98.47%); 180 sessions 5,420/5,540
    (97.83%); factual PIT theme 5,273/5,540 (95.18%). TickFlow supplied
    986,341 qualified adjusted facts and was healthy; raw decision violations
    and non-finite violations were both zero. The 300-session layer remains
    explicitly `insufficient_history` and economic validation remains
    `economic_validation_not_materialized`; neither is substituted by fallback
    data or represented as promotion evidence.
  - Production-code read projection returned `materialized_only`, stable
    pagination, explicit feature/membership/next-eligible dates,
    `research_only=true`, `production_mutation_allowed=false`, and
    notification/execution provenance `none`. Independent backend API/storage
    tests, frontend interaction tests, and domain-boundary tests passed. Host
    headroom was approximately 2.3 GiB available RAM; no provider call or ETF
    ranking, allocation, position, alert, SMTP, or execution mutation occurred.
  - Rollback retains append-only evidence and disables only V2 paths. Set
    `ETF_LEADER_TACTICS_V2_CAPTURE_ENABLED=false`,
    `ETF_LEADER_TACTICS_V2_MATERIALIZE_ENABLED=false`,
    `ETF_LEADER_TACTICS_V2_API_ENABLED=false`, and
    `NEXT_PUBLIC_ETF_LEADER_TACTICS_V2_ENABLED=false`; keep
    `ETF_LEADER_TACTICS_V2_ETF_MATERIALIZE_ENABLED=false`, then rebuild/restart
    the backend and frontend with the deployed compose file. For code/schema
    rollback, use the matching `/var/backups/fundscope/rollback-metadata-*.txt`
    and its verified database dump rather than deleting research facts.
- [x] 9.8 Confirm that insufficient data remains explicitly research-only and that no candidate can affect rankings, positions, email, or execution before a separate manually approved promotion change.
