## 1. Freeze Ranking Contracts

- [x] 1.1 Add tests for distinct `daily_reconstructable_v1` and `actionable_rank_v1` identities, score fields, versions, hashes, and forbidden inputs.
- [x] 1.2 Implement the actionable eligibility policy as a wrapper around `final_score_v3` without changing the existing score contract.
- [x] 1.3 Add tests and implementation for `provisional_short_history`, `standard_history`, and `full_history_context` based on point-in-time eligible session counts.
- [x] 1.4 Add product-field policy tests that distinguish mandatory, missing, stale, and explicitly `not_applicable` actionable inputs.

## 2. Generate Separate Ranking Surfaces

- [x] 2.1 Write failing service tests showing that 61 eligible adjusted sessions produce a research row while fewer sessions fail closed.
- [x] 2.2 Write failing service tests showing that missing intraday fields preserve the research row but exclude the actionable row with field-level reasons.
- [x] 2.3 Implement one auditable signal run that persists independently ordered research and actionable rows with coverage and rejection summaries.
- [x] 2.4 Add deterministic tie-breaking, non-finite rejection, cap-violation, as-of alignment, and contract-hash tests for both surfaces.

## 3. Bound Full-Universe Processing

- [x] 3.1 Add a performance test for batched price-history loading that prevents per-ETF history queries during a full-universe run.
- [x] 3.2 Implement deterministic batches of at most 20 ETFs, one-worker locking, resumable cursor state, and idempotent retry.
- [x] 3.3 Record per-batch duration, processed, eligible, excluded, failed, and remaining counts without starting unbounded history synchronization.
- [x] 3.4 Verify every provider and command boundary used by the ranking workflow has a hard timeout of at most 55 seconds.

## 4. Migrate Downstream Consumers

- [x] 4.1 Add API compatibility tests and additive surface metadata to cached `/api/short-research` ranking responses.
- [x] 4.2 Change ETF observation allocation candidate selection to require a matching eligible `actionable_rank_v1` row and test fail-closed exclusions.
- [x] 4.3 Change any rank-derived email candidate path to require matching actionable context and persist suppression reasons.
- [x] 4.4 Add regression tests proving tracked-position risk alerts remain independent of research-rank membership and still require their own decision-eligible data.
- [x] 4.5 Add evidence-contract tests that reject research/actionable contract substitution and distinguish warm-up range from replay range.

## 5. Update The Workbench

- [x] 5.1 Add frontend contract tests for research rank, actionable rank, history tier, eligibility reason, coverage, and timestamps.
- [x] 5.2 Make the broad research surface the default cached ETF list and add a separate actionable filter.
- [x] 5.3 Show provisional-history, missing-action-evidence, provider-health, and fail-closed empty states without presenting fallback candidates.
- [x] 5.4 Verify desktop and mobile views keep observation language and do not merge the two scores or ranks.

## 6. Shadow Migration And Verification

- [x] 6.1 Run bounded shadow generation on production-shaped data and record research/actionable counts, exclusions, deterministic hashes, latency, and peak batch size.
- [x] 6.2 Verify that a provider outage leaves the research surface usable and prevents actionable publication, allocation, and rank-derived email selection.
- [x] 6.3 Run relevant backend, frontend, domain-boundary, and strict OpenSpec validation commands with individual timeouts of at most 55 seconds.
- [x] 6.4 Document the consumer switch and rollback result before making `actionable_rank_v1` the required downstream surface.

## 7. Repair Acceptance Regressions

- [x] 7.1 Add regression tests and switch remaining ETF latest-signal, advisor, and live-watch consumers from the legacy `final_score_v3` selector to the dual-surface selector, keeping live Top 20 membership actionable-only.
- [x] 7.2 Reconcile the production-shaped deterministic hash with the current frozen component contracts and update the recorded evidence only after deterministic regeneration passes.
- [x] 7.3 Re-run bounded backend, frontend, domain-boundary, Ruff, and strict OpenSpec validation.
