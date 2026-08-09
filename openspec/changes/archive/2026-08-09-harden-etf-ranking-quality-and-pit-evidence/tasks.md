## 1. Freeze Contracts And Production-Shaped Regressions

- [x] 1.1 Add fixtures reproducing low-turnover ETFs in canonical Top N, unresolved taxonomy, cross-border misclassification, zero tracked-underlying coverage, unsafe percentile interpolation, mutable receipt time, duplicate same-date publication, and duplicate frontend score-sort semantics.
- [x] 1.2 Define versioned contracts for canonical eligibility, observation-only state, taxonomy precedence, tracked-underlying evidence, clone coverage, peer-count minimum, percentile method, component bounds, and stable exclusion reasons.
- [x] 1.3 Add invariants proving raw/display-only Sina or efinance prices, stale quotes, estimates, current-vintage membership, inferred receipt times, and unknown taxonomy cannot increase canonical or PIT coverage.
- [x] 1.4 Preserve the frozen `daily_reconstructable_v1` 55/30/15 formula, `final_score_v3` weights, actionable evidence gates, portfolio rules, alert policy, and notification behavior in regression tests.

## 2. Persist Auditable Taxonomy And Underlying Identity

- [x] 2.1 Add additive models and migration for append-only taxonomy and tracked-underlying facts with source, provider version, normalized identifier, observed-at cutoff, confidence, rule version, evidence hash, and supersession lineage.
- [x] 2.2 Implement precedence-aware classification so authoritative product/index facts and cross-border markers outrank domestic sector/theme keywords, with deterministic fixtures for Hong Kong, broad-market, bond, commodity, money-market, sector, and unknown ETFs.
- [x] 2.3 Ingest only authoritative tracked-index identities in serial 5–20 ETF pages with durable cursors, a maximum 55-second slice, and no name-only formal clone inference.
- [x] 2.4 Expose taxonomy, unknown-bucket, tracked-underlying, unresolved-identity, source, cutoff, and rule-version coverage through compact read-only projections.
- [x] 2.5 Prove later metadata revisions append new facts while historical PIT replay continues to select the fact visible at its cutoff.

## 3. Make Percentiles, Components, And Clone Semantics Safe

- [x] 3.1 Add unit and property tests for empirical ranks below, between, equal to, and above peer observations; ties; direction reversal; minimum peer count; and empty or non-finite inputs.
- [x] 3.2 Replace equality-dependent percentile logic with a bounded deterministic midrank and fail closed on insufficient peers, non-finite values, or primitive/component/aggregate bound violations.
- [x] 3.3 Validate score contracts and factor DAGs for missing weights, invalid ranges, cycles, duplicate identities, and undeclared fallbacks before materialization.
- [x] 3.4 Implement clone-group shared price primitives while retaining ETF-specific turnover, spread, premium, fees, provider consensus, and execution quality.
- [x] 3.5 Keep clone-aware ordering in shadow until authoritative underlying coverage reaches its predeclared activation gate, then select one optional diversified representative by a frozen tradability order without replacing canonical rank identity.
- [x] 3.6 Prove identical inputs produce identical scores, exclusions, ranks, representatives, and hashes across input order and safe batch sizes 5, 10, and 20.

## 4. Enforce Canonical Research Eligibility

- [x] 4.1 Apply the versioned 61-session, finite adjusted-data, CNY 50 million average-turnover, and known-compatible-taxonomy gates when materializing canonical daily research ranks.
- [x] 4.2 Persist excluded but stored ETFs as searchable `observation_only` rows with exact history, liquidity, taxonomy, freshness, and provenance reasons and no canonical or actionable rank.
- [x] 4.3 Mark 61–119-session canonical rows as `provisional_short_history`; require at least 120 sessions plus every existing intraday execution input before actionable eligibility.
- [x] 4.4 Return canonical, observation-only, peer-diagnostic, diversified-presentation, and actionable positions as distinct non-aliasing fields.
- [x] 4.5 Add production-shaped comparison evidence for eligible counts, Top-N membership, low-liquidity exclusions, unknown taxonomy, clone/theme concentration, and deterministic rank changes without attributing structural changes to alpha.

## 5. Preserve Immutable Adjusted-Price Availability

- [x] 5.1 Add append-only adjusted revision storage with immutable `first_seen_at`, `observed_at`, provider and adjustment versions, payload hash, decision eligibility, and supersession link while retaining the current projection for fast reads.
- [x] 5.2 Change adjusted-history upsert so a refresh may update the current projection but cannot overwrite accepted first-seen or prior revision evidence.
- [x] 5.3 Implement cutoff-aware PIT selection of the latest compatible revision visible by the signal cutoff and bind the selected revision hash to replay inputs.
- [x] 5.4 Give legacy rows without factual receipt evidence zero historical PIT credit while preserving their valid current-production use.
- [x] 5.5 Add idempotency, revision-order, late-backfill, provider-change, visibility-cutoff, index-use, and bounded-memory tests.

## 6. Version Readiness And Canonical Publication

- [x] 6.1 Introduce a new immutable readiness-policy version that preserves 95-percent target-date adjusted coverage and 90-percent canonical 61-session coverage without reinterpreting v1/v2 evidence.
- [x] 6.2 Include research and actionable contract hashes, policy version, universe/input/cutoff hashes, provider-health seal, and surface-group hash in canonical publication identity.
- [x] 6.3 Add a database uniqueness guard or locked canonical registry so concurrent exact identities yield one published winner and idempotent retries return it.
- [x] 6.4 Preserve immutable prior publications and record explicit supersession when a new compatible contract publishes for the same trade date; selectors must return exactly one current canonical row.
- [x] 6.5 Seal provider-health hash, check time, policy, covered fields, and source range with the draft and reject missing, stale, or incompatible actionable evidence.
- [x] 6.6 Distinguish `research_complete_actionable_unavailable` from `research_complete_actionable_available` and retain exact actionable blocker counts.

## 7. Complete The Bounded Production PIT Loop

- [x] 7.1 Replace permanent unavailable handlers with durable adapters for frozen candidates, forward outcomes, ranking validation, factor evidence, policy shadow, and final evidence using the existing workflow and artifact store.
- [x] 7.2 Make each continuation single-worker, one-page, idempotent, resumable, and below 55 seconds, committing artifact and cursor atomically and never starting a second page in the same trigger.
- [x] 7.3 Keep future entry/exit windows pending until their exact exchange sessions complete; never shift dates or reconstruct missing historical intraday execution evidence.
- [x] 7.4 Bind every artifact to manifest, code, input, cutoff, predecessor, candidate, outcome, and cost hashes outside the mutable checkpoint.
- [x] 7.5 Prove all research phases leave production ranking, allocation, positions, alerts, notifications, SMTP, score weights, execution receipts, and holdout state unchanged.
- [x] 7.6 Expose `ranking_ready` separately from `research_ready`, including factual PIT sessions, non-overlapping primary dates, folds, pending windows, exclusions, and all promotion blockers.

## 8. Produce Honest Structural And Factor Evidence

- [x] 8.1 Measure membership turnover on every eligible daily transition separately from normalized rank churn among common members.
- [x] 8.2 Apply cutoff-valid factual spread and liquidity costs when available and the frozen conservative cost model otherwise, recording every cost input and unavailable reason.
- [x] 8.3 Add residual IC and common-support marginal contribution diagnostics for overlapping momentum, sector, risk, and liquidity signals.
- [x] 8.4 Run only the three frozen main candidates: corrected canonical Top10 baseline, Top15-buffer/one-replacement/three-session-hold hysteresis, and hysteresis plus existing regime/liquidity gate.
- [x] 8.5 Keep leader, catalyst, theme, sentiment, valuation, alternate volume transforms, and machine-learning ideas in separately pre-registered zero-weight shadow experiments.
- [x] 8.6 Report paired five-session Top10 net excess as the sole ranking primary metric, with auxiliary horizons, hit rate, drawdown, turnover, cost drag, concentration, clone coverage, exclusions, adjusted confidence intervals, folds, and locked-holdout status.
- [x] 8.7 Keep every candidate `insufficient_data` and production weights frozen until 252 factual PIT sessions, 40 independent primary dates, three chronological folds, adjusted uncertainty, and all existing quality gates pass.

## 9. Align API And Workbench Semantics

- [x] 9.1 Extend API schemas and TypeScript contracts with separate surface identities, canonical and observation coverage, history tier, tradability, taxonomy, underlying, clone, peer, cutoff, provider-health, publication, PIT, cost, and concentration evidence.
- [x] 9.2 Make `日线研究榜` the default surface and `盘中可行动榜` the separate execution-qualified surface.
- [x] 9.3 Remove duplicate sort options that map to the same score/order and ensure every remaining sort key changes ordering under its declared surface contract.
- [x] 9.4 Render observation-only, provisional, low-liquidity, unknown-taxonomy, unresolved-clone, stale, coverage-blocked, provider-unhealthy, and no-actionable states distinctly with stable Chinese explanations.
- [x] 9.5 Prove research replay, structural shadow, leader/catalyst shadow, policy shadow, and real delivery/execution evidence cannot be visually or semantically presented as formal strategy performance.

## 10. Run Bounded Verification

- [x] 10.1 Run focused taxonomy, underlying, percentile, bounds, clone, eligibility, revision, readiness, publication, selector, PIT phase, diagnostics, API, and production-isolation tests in separate commands with hard timeouts below 60 seconds.
- [x] 10.2 Run frontend TypeScript and focused static-contract checks with hard timeouts below 60 seconds.
- [x] 10.3 Run `uv run pytest tests/test_backend_domain_boundaries.py` with a hard timeout below 60 seconds and fix any dependency-direction violation.
- [x] 10.4 Run Ruff on changed backend and test files in bounded groups, then run any wider Ruff check only with a hard timeout and process-status handling.
- [x] 10.5 Run strict OpenSpec validation when the project OpenSpec CLI is available; until then, validate artifact presence, requirement/scenario structure, task numbering, and whitespace locally.

## 11. Roll Out And Collect Real Evidence

- [ ] 11.1 Deploy schema and code with the new publication policy, clone activation, and later PIT phases disabled; record artifact version, migration head, feature flags, and rollback selectors.
- [ ] 11.2 Run bounded serial taxonomy, tracked-underlying, and adjusted-revision backfills with durable checkpoints, 5–20 ETF adaptive batches, a maximum 55-second slice, single concurrency, and the existing memory limit.
- [ ] 11.3 On one real target date, compare old and corrected shadow rankings for authoritative universe, adjusted and canonical coverage, liquidity/taxonomy/clone exclusions, Top-N changes, concentration, caps, non-finite rejects, provider health, and server resources.
- [ ] 11.4 Enable the new readiness policy only after target-date coverage is at least 95 percent and canonical coverage is at least 90 percent; verify exactly one canonical research publication and an honest actionable state.
- [ ] 11.5 Verify the production research API and workbench select the exact canonical identity, contain no low-liquidity or unknown-taxonomy canonical rows, expose unresolved clone coverage honestly, and never substitute raw or stale data.
- [ ] 11.6 Enable one-page PIT continuation and record monotonic immutable artifacts across at least three different real trading dates without counting same-date retries as additional sessions.
- [ ] 11.7 Keep formal promotion closed until all 252-session statistical gates pass; record rollback evidence that selects the prior compatible snapshot and disables new adapters without deleting immutable facts.
