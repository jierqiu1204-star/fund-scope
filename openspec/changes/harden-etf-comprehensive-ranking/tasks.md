## 1. Reconcile Contracts And Lock Regression Baselines

- [x] 1.1 Compare this change with `make-opportunity-final-decision-ranking`, `improve-etf-cross-sectional-ranking`, `add-etf-sector-trend-scoring`, `expand-etf-factor-library`, and `add-etf-theme-catalyst-scoring`, and record each overlapping task as retained, superseded, or blocked.
- [x] 1.2 Remove or supersede every active-change requirement that lets label or validation evidence contribute to a current score, label, rank, allocation, tracked position, alert, or notification.
- [x] 1.3 Pause pending deployment, production recompute, Top-N validation, allocation recompute, and evidence-promotion tasks from overlapping active changes until the v3 publication contract is ready.
- [x] 1.4 Freeze the `final_score_v3` component DAG/manifest, compatibility aliases, stale/unavailable caps, homogeneous asset buckets, anti-double-count lineage, and current score-version selector in one reviewed configuration contract.
- [x] 1.5 Convert the failing opportunity-order test into `test_comprehensive_sort_uses_final_decision_score_not_theme_heat`, preserving theme/catalyst evidence assertions.
- [x] 1.6 Convert the failing score-bucket test into `test_score_bucket_validation_requires_current_full_ranking_contract`, covering legacy score, later partial run, contract mismatch, missing score, and non-finite score.
- [x] 1.7 Add failing regression tests for partial-run canonical pollution, stale canonical cache, one-code detail rank, filtered live rank change, missing-hash classification, post-enrichment caps, negative evidence, and validation side effects before changing production behavior.

## 2. Add Snapshot, Price-Basis, And Universe Schema

- [x] 2.1 Add nullable typed and indexed snapshot identity fields to `ShortResearchSignalRun` for scope kind/hash, universe and input-snapshot hashes, score/rule versions, ranking contract hash, score field, cutoff, trade date, price basis, coverage counts, publication state/time, and a unique idempotency key.
- [x] 2.2 Add nullable item-level `ranking_score`, `score_eligible`, global rank, and source snapshot identity without removing current compatibility fields or guessing values for legacy items.
- [x] 2.3 Add raw-versus-research price-basis, adjusted value, provider version, source timestamp, and decision-eligibility fields needed by ETF daily history while preserving raw OHLC data.
- [x] 2.4 Add effective-dated ETF universe membership storage with activation/deactivation dates, source, tracked-underlying identity, and exclusion reason.
- [x] 2.5 Add typed source ranking/scope/universe/input hashes, price basis, execution model, and cutoff fields to validation runs so current evidence selection does not rely on JSON inspection.
- [x] 2.6 Update ORM, Pydantic, API, and TypeScript contracts for the new nullable fields without making legacy rows appear current.
- [x] 2.7 Create migration tests covering upgrade, downgrade, a single Alembic head, indexes, idempotency uniqueness, nullable legacy rows, and duplicate membership interval prevention.
- [x] 2.8 Backfill only factual values that can be proven from stored records; leave score versions, contract hashes, price basis, and point-in-time identity null when they cannot be reconstructed.

## 3. Implement Immutable Canonical Ranking Snapshots

- [x] 3.1 Implement canonical serialization and hashing for score/rule versions, component DAG/manifest, scope, universe and input snapshots, price basis, cutoff semantics, and reliability policy with order-independent hash tests.
- [x] 3.2 Persist explicit `full`, `theme`, and `codes` scope kinds and ensure every new run records exact scope rather than relying on omitted query fields.
- [x] 3.3 Build the expected point-in-time universe snapshot and hash before scoring, including inactive-later ETFs for historical replay and tracked-underlying metadata for clone handling.
- [x] 3.4 Implement idempotent publication state transitions so a full snapshot becomes published only after item count, rank continuity, hashes, coverage, and summary metadata pass and commit atomically.
- [x] 3.5 Replace permissive latest-run lookup with an indexed canonical selector requiring ETF, published, full scope, current score/contract, compatible basis, target trade date, and exchange-calendar freshness.
- [x] 3.6 Remove the fixed recent-50 scan limit and return explicit waiting, stale, legacy, or version-mismatch when no canonical snapshot qualifies.
- [x] 3.7 Resolve and pin the source snapshot once per list, detail, live, portfolio, and validation request so concurrent publication cannot mix runs inside one response.
- [x] 3.8 Enforce post-publication immutability for snapshot identity and item rank/score fields and add mutation-rejection tests.
- [ ] 3.9 Propagate snapshot id, score version, contract hash, scope, trade date, generated time, coverage, freshness, and limitations to every downstream response.
- [ ] 3.10 Add selector tests proving later partial/theme/code, fund, mixed-asset, failed, stale, and legacy runs never replace a compatible full ETF snapshot.

## 4. Restore One-Way Evidence And Risk Boundaries

- [ ] 4.1 Remove validation/label-evidence reads from signal score generation and delete the post-generation evidence-driven score and conclusion rewrite.
- [ ] 4.2 Remove validation-evidence score reapplication from observation portfolio loading while preserving evidence as explanatory output.
- [ ] 4.3 Move stale, unavailable, insufficient-history, and other hard score/conclusion limits to one final post-enrichment step shared by signal generation and replay.
- [ ] 4.4 Add property tests proving stale scores never exceed 55, unavailable scores never exceed 45, adding a risk never raises score, and auxiliary/evidence changes cannot bypass a cap.
- [ ] 4.5 Change evidence summary construction so missing hashes remain missing and classify as `旧口径结果`; add mismatch tests for score, rule, universe, price basis, and allocation identities.
- [ ] 4.6 Remove `total_score` and other legacy fallback from current Top-N/score-bucket validation and report stable exclusion keys and code/date reasons.
- [ ] 4.7 Ensure negative, inconclusive, stale, or high-sample label evidence is display-only and can never receive a positive runtime score contribution.
- [ ] 4.8 Add an integration test that snapshots signal, allocation, position, alert, and notification tables before every validation type and proves only evidence tables change.

## 5. Make Daily Data Point-In-Time And Publication-Safe

- [ ] 5.1 Ingest and persist traceable total-return-aware ETF research values alongside raw daily values, with fixtures for distributions, splits, merges, and unit adjustments.
- [ ] 5.2 Use accumulated NAV for fund return/drawdown research while retaining unit NAV for the existing display contexts that need it.
- [ ] 5.3 Route ranking, volatility, drawdown, percentile, and forward-return calculations through the declared adjusted research series and fail closed when its provenance is unavailable.
- [ ] 5.4 Stop promoting 14:55/pre-close, server-time fallback, diverged, stale, or display-only intraday quotes into verified decision daily history.
- [ ] 5.5 Implement the one-trade-date data barrier and default 95 percent expected-universe publication threshold, with exact included/excluded coverage reporting.
- [ ] 5.6 Close point-in-time universe membership when ETFs disappear or become ineligible and preserve delisted/liquidated members in historical coverage.
- [ ] 5.7 Change bounded daily sync ordering to missing/stale first plus persisted rotation cursor while retaining tracked/default-display priority and proving lower-priority ETFs cannot starve.
- [ ] 5.8 Map skipped, deferred, partial-provider, and coverage-failed business results to `skipped`, `partial`, or `failed` JobRun states instead of success.
- [ ] 5.9 Add a per-trade-date workflow lock and sequence universe refresh, daily sync, coverage check, full scoring, atomic publication, and optional downstream generation.
- [ ] 5.10 Register and expose freshness for ETF universe, theme/catalyst, daily repair, and intraday cleanup jobs.
- [ ] 5.11 Expand data-health output to include raw/research dates, basis, provider, decision eligibility, skipped/deferred state, and issue details requested by the workbench.
- [ ] 5.12 Add regression tests for mixed trade dates, insufficient coverage, partial batch, provider-incompatible basis, sync cursor rotation, deactivation history, and JobRun state mapping.

## 6. Build The Single-Pass `final_score_v3` Pipeline

- [ ] 6.1 Introduce a typed `RankingInput` and versioned component DAG/manifest distinguishing score-bearing from explanatory components, primitive-factor lineage, required fields, units, asset buckets, weights, and missing-data behavior.
- [ ] 6.2 Reject component manifests that double-count one primitive factor through both a composite component and a separately weighted child, with DAG validation tests.
- [ ] 6.3 Compute all declared score-bearing base, cross-sectional, sector, liquidity/structure, premium, theme, and factor inputs before one final-score calculation.
- [ ] 6.4 Produce and persist `distance_to_ma20_pct`, required 20/60-day turnover references, eligible premium/discount, component source dates, reliability, and per-metric peer counts from the production metric path.
- [ ] 6.5 Make missing or non-finite required inputs unavailable; remove `or 0`, neutral-50, silent weight-renormalization, and fixture-only producer behavior from score-bearing paths.
- [ ] 6.6 Require 21 eligible closes for a twenty-return volatility metric and expose every effective window/sample count.
- [ ] 6.7 Deduplicate or cluster-weight ETFs sharing an underlying index before cross-sectional percentiles and sector breadth while preserving individual displayed items.
- [ ] 6.8 Calculate percentiles inside declared homogeneous ETF asset buckets, persist bucket identity, and prevent incompatible profile versions from direct comparison.
- [ ] 6.9 Keep unimplemented flow, fundamental, valuation, macro, or other factor groups explanatory/unavailable until a future manifest activates a point-in-time producer.
- [ ] 6.10 Add theme/catalyst maximum age and prevent a factor/profile pass from restoring a component rejected by data or quality gates.
- [ ] 6.11 Either incorporate `RankingRecord.base_score` explicitly in the v3 manifest or remove it and its now-dead callers; add a test documenting the chosen behavior.
- [ ] 6.12 Derive observation labels and explanations from v3, apply final hard limits once, persist score breakdown and limitations, and reject NaN/Infinity at the boundary.
- [ ] 6.13 Add production-path integration tests from computed market metrics through sector/factor/theme enrichment to final score, including all-missing MA20 and turnover cases.
- [ ] 6.14 Add a shadow v2-versus-v3 comparison report for coverage, component availability, caps, rank correlation, top-N changes, and exclusion reasons without changing the canonical reader.

## 7. Correct Static, Detail, Live, And User-Scoped Ranking Semantics

- [ ] 7.1 Sort and assign persisted daily global rank across the full snapshot before search, theme, labels, tracking, pagination, or page-size filters.
- [ ] 7.2 Return `global_rank` and `filtered_position`, keep legacy `rank` as a documented global-rank alias, and keep `total` as filtered pre-pagination count.
- [ ] 7.3 Make asset detail read the stored item/global rank directly so a one-code lookup cannot return synthetic rank one.
- [ ] 7.4 Freeze the live watch scope and hash, calculate base/live scope ranks before user filters, and return `base_global_rank`, `live_scope_rank`, `filtered_position`, `rank_scope`, and nullable `rank_change`.
- [ ] 7.5 Calculate rank change only between the same live-scope hash and score version; use a shared stable tie-break and return null for incomparable scopes.
- [ ] 7.6 Replace truthiness sentinels in return, drawdown, risk, liquidity, and other sort modes with explicit finite/missing sort tuples that keep real zero and put missing values last.
- [ ] 7.7 Implement `tracking_states` consistently for static and live endpoints through API/workflow orchestration without making research services import tracking services.
- [ ] 7.8 Separate scheduler-wide quote collection from authenticated presentation sources so another user's holding never appears as `tracked_position` or changes user-scoped counts.
- [ ] 7.9 Define current alert relevance/expiry for `触发提醒` and `仅网页提示` filters so historical alerts do not remain active indefinitely.
- [ ] 7.10 Reject unsupported tracking/filter combinations explicitly instead of silently ignoring query parameters.
- [ ] 7.11 Add multi-user, filter, pagination, equal-score, zero/missing-value, one-code detail, and static/live compatibility tests, including domain-boundary assertions.

## 8. Normalize Intraday Accuracy And Session Handling

- [ ] 8.1 Introduce an Asia/Shanghai exchange calendar for holidays, open, lunch, reopen, close, freshness, and next-poll decisions.
- [ ] 8.2 Require a compatible fresh canonical daily snapshot and eligible quote before producing an intraday comprehensive score; expose fresh quote separately when the base is stale.
- [ ] 8.3 Replace universal price-change thresholds with versioned ATR/volatility and homogeneous asset-bucket thresholds.
- [ ] 8.4 Build historical same-exchange-minute turnover references and compare current cumulative turnover with same-time distributions instead of full-day averages.
- [ ] 8.5 Return activity adjustment unavailable when same-time history is insufficient and do not apply an automatic morning penalty or neutral fallback.
- [ ] 8.6 Use only eligible finite premium/discount, spread, consensus, price, and timestamp inputs and expose unavailable component reasons without weight transfer.
- [ ] 8.7 Return one effective server market status and next-poll interval and trigger refresh at open, lunch, afternoon reopen, close, and next-trading-day boundaries.
- [ ] 8.8 Add tests for weekday holidays, boundary transitions, stale base plus fresh quote, provider divergence, morning/afternoon equivalent activity, volatility buckets, and insufficient same-time history.

## 9. Replace Validation With Contract-Exact Date-Level Evidence

- [ ] 9.1 Select only successful compatible full snapshots and the finite score field declared by each snapshot; record every source snapshot id and every exclusion reason.
- [ ] 9.2 Build same-date Top 5/10/20/50 and `all_scored` portfolios from the identical point-in-time universe, ranking contract, price basis, and reliability policy.
- [ ] 9.3 Aggregate each Top-N or label bucket to one equal-weight observation per signal date before time-series statistics.
- [ ] 9.4 Use non-overlapping signal dates for each validation horizon and report overlapping/pending windows separately.
- [ ] 9.5 Implement the versioned T+1 adjusted-close entry, horizon exit, and fixed two-sided fee/slippage model with missing-entry exclusion.
- [ ] 9.6 Declare Top 10 five-trading-day paired net excess return versus `all_scored` as the primary endpoint and label all other Top-N/horizon combinations exploratory.
- [ ] 9.7 Report unique/effective signal dates, asset count, price/universe coverage, mean/median net and paired excess returns, win rate, drawdown, turnover, costs, and exclusions.
- [ ] 9.8 Implement a deterministic date-block bootstrap 95 percent interval and separate sample sufficiency from `supportive`, `inconclusive`, or `negative` effect direction.
- [ ] 9.9 Enforce `insufficient` below 20 independent dates or 95 percent required coverage and prove duplicated same-date ETF rows cannot increase confidence.
- [ ] 9.10 Group and store evidence by exact ranking hash, scope, universe, score field, score/rule version, basis, reliability, label, timing, horizon, asset bucket, and source date.
- [ ] 9.11 Keep old survivor-biased, unadjusted, hashless, partial, or legacy-score validation visibly old and exclude it from current-contract totals.
- [ ] 9.12 Update evidence APIs and workbench data so users can see primary versus exploratory endpoint, confidence interval, effective dates, coverage, costs, and limitations.
- [ ] 9.13 Add tests for T+1 execution, distributions, delisted ETF membership, duplicate rows, non-overlap, paired baseline, interval state boundaries, contract grouping, and no decision-domain writes.

## 10. Fix Workbench State, Privacy, And Operational Behavior

- [ ] 10.1 Extend frontend types and query parsing for snapshot identity, freshness, limitations, explicit rank fields, rank scope, decision eligibility, market status, polling interval, and filtered totals while preserving legacy fields.
- [ ] 10.2 Extract small pure helpers for ranking parameters, effective live status, rank presentation, view state, pagination clamp, count scope, and Asia/Shanghai calendar values without restructuring unrelated page layout.
- [ ] 10.3 Display global and filtered rank separately and consume only comparable server rank-change fields in both desktop and mobile views.
- [ ] 10.4 Derive quote/session status from the applicable live-list or selected-ETF response so the default comprehensive page cannot show open data as closed or zero watched items.
- [ ] 10.5 Refetch all ETF quote queries at open, lunch, afternoon reopen, close, and next-session boundaries regardless of the previous response and use server polling intervals while open.
- [ ] 10.6 Distinguish loading, ready, filtered-empty, no-snapshot, stale, unavailable, and request-error states and ensure an error never renders as ordinary no-results.
- [ ] 10.7 Clamp invalid offsets when totals shrink, retain previous-page navigation whenever offset is positive, and label current-page versus full/filtered summary counts.
- [ ] 10.8 Request or lazily fetch data-health details whenever the issue panel depends on them and keep fund and ETF issue-count scopes separate.
- [ ] 10.9 Add stable user identity to private query keys, cancel/remove private queries on logout/account change, and pass React Query AbortSignal through Axios query functions.
- [ ] 10.10 Disable overlapping sync, signal, and advisor actions for the same scope and bind advisor requests to the completed source snapshot id.
- [ ] 10.11 Replace UTC date slicing and browser-dependent `YYYY-MM-DD` parsing with Asia/Shanghai calendar helpers and test 00:00-08:00 and negative-browser-offset cases.
- [ ] 10.12 Add frontend unit/component tests for filters, ranks, market boundaries, stale/error/empty states, pagination shrink, user A to B cache isolation, in-flight cancellation, task mutual exclusion, count scope, and Shanghai dates.

## 11. Migrate, Shadow, Publish, And Recompute

- [ ] 11.1 Deploy additive nullable schema before new writers and confirm old readers continue working against the migrated database.
- [ ] 11.2 Run full universe and adjusted-price synchronization until the publication coverage gate passes, recording unresolved source and basis exclusions.
- [ ] 11.3 Generate v3 snapshots in shadow and require non-null identity, no non-finite score inputs, hard-cap invariants, expected coverage, and reviewed top-rank differences before promotion.
- [ ] 11.4 Switch canonical readers with one configured score-version selector while retaining a rollback path that never labels v2/legacy evidence as current.
- [ ] 11.5 Migrate the workbench, detail, live ranking, portfolio, and evidence consumers to explicit snapshot/rank fields before deprecating legacy aliases.
- [ ] 11.6 Recompute historical rankings only from reconstructable point-in-time adjusted data and leave unreconstructable dates explicitly legacy rather than filling them.
- [ ] 11.7 Re-run Top-N and label validation under the exact v3 contract; keep evidence `等待验证` or `样本不足` until independent-date gates pass.
- [ ] 11.8 Verify rollback by selecting the prior reader, preserving v3 rows, showing old/degraded status, and switching back without data rewrite.
- [ ] 11.9 Monitor publication coverage, snapshot age, provider health, component availability, cap violations, non-finite rejects, rank churn, and validation exclusions across at least three exchange sessions before closing rollout.

## 12. Verification And Risk Closure

- [ ] 12.1 Run ranking, evidence, API, sector/factor/theme, intraday, data-sync, scheduler, migration, and validation tests with explicit timeouts; keep the two former failures as passing regression tests.
- [ ] 12.2 Run `uv run pytest tests/test_backend_domain_boundaries.py` and confirm the new tracking filters remain in API/workflow orchestration.
- [ ] 12.3 Run `uv run ruff check .`, backend type checks used by the repository, frontend typecheck, frontend lint, and frontend unit/component tests.
- [ ] 12.4 Run `openspec validate harden-etf-comprehensive-ranking --strict` and fix every schema, requirement, and scenario error.
- [ ] 12.5 Compare unfiltered and filtered static/live responses from one snapshot and automatically prove stable global rank, explicit filtered position, compatible rank change, user isolation, and pagination invariants.
- [ ] 12.6 Verify every audited P0/P1 path: partial-run pollution, stale cache, mixed dates, legacy score fallback, fabricated hash, validation feedback, cap bypass, raw-price distortion, sector input gaps, filtered-rank fabrication, and cross-user leakage.
- [ ] 12.7 Verify every audited accuracy path: clone handling, asset buckets, premium and factor availability, point-in-time universe, non-overlapping date-level validation, costs, uncertainty, same-time activity, and exchange calendar.
- [ ] 12.8 Verify every audited workbench path: tracking filters, live status source, boundary refresh, stale/error/empty states, pagination recovery, health detail, count scope, auth cache, task concurrency, and Shanghai dates.
- [ ] 12.9 Run any verification expected to exceed three minutes with a bounded timeout and process monitoring, using the project server Docker environment when local static builds are known to hang.
