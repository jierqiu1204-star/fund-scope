## Context

The ETF comprehensive ranking is served through two related paths. `/api/short-research` reads a cached daily signal run, while `/api/etf-quotes/live-rankings` overlays eligible intraday evidence on that daily base. Validation, evidence summaries, observation portfolios, selected-asset detail, and the `/short-term` workbench all consume the same persisted signal items.

The intended modular-monolith direction is sound, but the current trust boundary is not. A later partial theme/code run can be selected as the latest global run; filtered queries are re-ranked as if they were global; successful runs have no canonical freshness or compatibility selector; validation accepts legacy `total_score`; missing evidence hashes can be replaced with the current hash; validation evidence is fed back into live scoring; and post-score evidence adjustment can bypass stale/unavailable caps. Daily data may also mix trade dates, raw unadjusted prices, and intraday-derived display quotes, while sector, premium, turnover, and factor consumers do not always receive the production fields they expect.

This change is cross-cutting but remains inside the existing modular monolith. It must preserve the documented dependency direction, prefer unavailable/waiting states over misleading financial fallbacks, retain the current public endpoint paths, and reconcile active changes for final-decision ranking, cross-sectional ranking, sector scoring, factor expansion, and theme scoring. In particular, this design supersedes any active-change statement that allows validation evidence to modify the current ranking: validation is a research-only consumer.

## Goals / Non-Goals

**Goals:**

- Give every canonical ETF ranking one immutable, queryable identity and one point-in-time universe/data contract.
- Ensure all score-bearing inputs are decision-eligible, finite, complete for their declared contract, and computed before the final score.
- Apply data and risk hard limits once, at the end of scoring, so later enrichment cannot weaken them.
- Prevent partial, stale, legacy, mismatched, or half-synchronized runs from becoming the default ranking or current validation evidence.
- Make daily rank, live-scope rank, filtered position, and rank change unambiguous and stable.
- Make validation statistically honest enough for research decisions without introducing a complex optimization platform.
- Preserve current-user isolation in tracking filters, watchlist explanations, and frontend caches.
- Provide staged migration, shadow comparison, rollback, and explicit evidence recomputation gates.

**Non-Goals:**

- Splitting the backend into microservices or changing the established domain dependency direction.
- Connecting to a broker, executing orders, or turning observation ranks into trading instructions.
- Adding a machine-learning optimizer, online-learning weights, or automatic validation-to-ranking feedback.
- Treating every planned theme, macro, valuation, flow, or fundamental factor as score-bearing before a real producer and point-in-time tests exist.
- Redesigning the entire `/short-term` page; only behavior needed for truthful ranking, status, filtering, and user isolation is in scope.
- Replacing tracked-position risk rules, portfolio allocation rules, or notification thresholds except where they must consume a compatible ranking snapshot.

## Decisions

### 1. Extend the existing signal run into the immutable ranking snapshot

`ShortResearchSignalRun` remains the persisted batch boundary; a second parallel ranking table would duplicate ownership and complicate migration. Add indexed, typed snapshot fields rather than relying on ad-hoc JSON inspection:

- `scope_kind`: `full`, `theme`, or `codes`
- `scope_hash`
- `universe_snapshot_hash`
- `input_snapshot_hash`
- `score_version` and `rule_version`
- `ranking_contract_hash`
- `score_field`
- `data_cutoff` and `as_of_trade_date`
- `price_basis`
- `expected_item_count`, `eligible_item_count`, and `coverage_ratio`
- `published_at` and a publication state
- a unique `idempotency_key`

Diagnostic details, the included/excluded universe manifest, and exclusion counts may remain in JSON, but selectors never infer mandatory identity fields from JSON defaults. New signal items persist a nullable typed `ranking_score` and `score_eligible` state so current validation has one unambiguous score source. Validation runs persist the source ranking, scope, universe and input hashes, price basis, execution model, and cutoff as typed query fields. A successful snapshot becomes immutable. Existing rows receive nullable columns and remain legacy; migrations must not fabricate hashes or current versions.

Alternative considered: introduce a new `EtfRankingSnapshot` table. This gives a clean model but duplicates the current run/item relationship and requires broader API migration. Extending the run is the smaller change.

### 2. Canonical reads are exact and fail closed

The default ETF workbench, asset detail, observation portfolio, live-ranking base, and current-version validation select only a published `scope_kind=full` ETF snapshot whose score version, contract hash, trade date, price basis, and freshness satisfy the consumer contract. Theme/code runs remain available for research inspection but cannot replace the canonical pointer.

No compatible snapshot means `waiting`, `stale`, or `unavailable`; the service does not fall back to an arbitrary earlier success. Selection uses indexed fields and is not limited to scanning the last 50 runs. Canonical freshness uses the Chinese exchange calendar, not elapsed weekday assumptions.

Alternative considered: keep permissive latest-run selection and add UI warnings. That still allows portfolio and validation consumers to use the wrong cohort, so warning-only behavior is insufficient.

### 3. Full-universe scoring precedes every presentation filter

Signal generation builds the eligible full universe, deduplicates or clusters ETF clones by tracked underlying index for cross-sectional distribution calculations, computes homogeneous asset-bucket percentiles, and persists the complete ordered result. Theme, code, search, label, tracking, and pagination filters run only against persisted results.

The daily API exposes `global_rank` from the immutable snapshot and `filtered_position` for the current result. Asset detail returns the stored global rank and never re-enumerates a one-code result. Null sort values always appear last; exact zero remains a real value; all sort modes share one deterministic code tie-break.

For live ranking, the backend freezes a `live_scope_hash` before user filters. It returns `base_global_rank`, `live_scope_rank`, `filtered_position`, and `rank_scope`. `rank_change` is produced only between comparable ranks with the same score version and scope hash; it is null otherwise.

Alternative considered: call the filtered position `rank` for compatibility. The endpoint may retain a deprecated alias temporarily, but the UI must consume the explicit fields so it cannot imply a global move.

### 4. `final_score_v3` is a declared, single-pass component manifest

The current order is replaced by one typed `RankingInput` flow:

1. validate point-in-time market data and universe eligibility;
2. compute base metrics and per-metric peer coverage;
3. compute declared score-bearing sector, structure/liquidity, premium, theme, and factor inputs;
4. calculate `final_score_v3` once from finite eligible components;
5. derive observation labels and explanations;
6. apply stale/unavailable and other hard limits last;
7. persist the immutable item and score breakdown.

The score version contains a component DAG and manifest identifying score-bearing versus explanatory components, fixed weights, required input fields, eligible asset buckets, missing-data behavior, and primitive-factor lineage. A primitive factor may contribute through exactly one declared path; a composite score and its children cannot both be weighted again. A declared score-bearing input is mandatory: if it is missing or display-only, the final score is unavailable. Missing components are not replaced with `0` or `50`, and available weights are not silently renormalized. Planned factors without production data remain explicitly explanatory/unavailable until a future score version activates them.

Label validation and historical performance are never score-bearing. The initial v3 weights are frozen, versioned parameters derived from the non-evidence score components and reviewed with shadow walk-forward results; runtime validation never updates them. `RankingRecord.base_score` is either deliberately incorporated and documented in the manifest or removed as dead input.

Alternative considered: continue v2 and patch individual calculations. A new version is required because removing label evidence, changing component eligibility, and correcting data basis materially changes score meaning.

### 5. Producer-consumer contracts are typed and integration-tested

Base metric production must provide the exact finite fields consumed by sector and score code, including MA20 distance, 20/60-day turnover references where applicable, premium/discount source and timestamp, valid peer counts, and reliability. Missing MA20 does not mean “above MA20”; missing 60-day turnover does not receive a neutral sector contribution.

Factor profiles record profile version, component coverage, source dates, and degradation reasons. Profiles with different manifests are not mixed in one cross-sectional comparison. Theme/catalyst snapshots have an explicit maximum age; a quality gate cannot be undone by a later factor assignment.

The factor library remains capable of displaying unavailable groups, but fund flow, fundamentals, valuation, and macro factors cannot be advertised as score-bearing until a real point-in-time producer exists.

Alternative considered: keep flexible dictionaries and add more unit fixtures. The existing problem is precisely that fixtures can provide fields production never emits, so an end-to-end typed contract test is required.

### 6. Daily research uses point-in-time, total-return-aware data

Raw exchange/provider OHLC and NAV remain available for display and execution context. Research return, volatility, drawdown, percentile, and forward-outcome calculations use a separately stored total-return-aware series with `price_basis`, provider, source timestamp, adjustment version, and decision eligibility. Funds use accumulated NAV for cross-distribution return calculations. If a traceable adjusted series is unavailable, the affected score/outcome is unavailable rather than calculated from an incompatible raw series.

The universe gains point-in-time membership history with effective dates and exclusion reasons, so delisted, liquidated, or later-ineligible ETFs remain in historical coverage denominators. Refresh closes missing memberships instead of leaving vanished products active.

Intraday snapshots are not promoted into official decision daily history merely because the time is after 14:55. A daily ranking uses verified daily data for one exchange trade date. The publication barrier defaults to at least 95% coverage of the expected default-display universe and requires all included decision assets to share that date. Lower coverage publishes no canonical snapshot and reports exclusions.

Alternative considered: accept mixed dates with reliability penalties. Cross-sectional percentiles are cohort-relative, so a penalty cannot repair comparison against different market days.

### 7. Synchronization and publication form one auditable workflow

The scheduled/admin workflow is:

`refresh universe -> synchronize required daily data -> validate same-date coverage -> generate full snapshot -> publish canonical snapshot -> optionally generate allocation -> run research-only validation later`.

A lock prevents overlapping sync and generation for the same trade date. Batch sync uses priority plus stale-first ordering and a persisted cursor so lower-priority ETFs cannot starve. `skipped`, `deferred`, provider-incomplete, and coverage-below-threshold results are first-class partial states. `JobRun` maps business result state to success/partial/failed rather than treating every normal function return as success.

Universe refresh, theme/catalyst refresh, and intraday cleanup receive explicit scheduler registrations and freshness reporting. Manual UI controls call the workflow or respect its dependency state; they do not launch sync, ranking, and advisor generation concurrently.

Alternative considered: retain time-separated independent jobs. Provider latency makes fixed five- or fifteen-minute gaps an unreliable dependency mechanism.

### 8. Evidence hashes are exact and validation is a read-only domain consumer

The canonical ranking contract hash is computed from a canonical serialization of score/rule versions, component manifest, full-universe snapshot hash, price basis, data cutoff semantics, and relevant reliability policy. Signal items and validation runs persist the exact source hash.

Evidence classification follows strict states:

- missing source hash/version: `旧口径结果`;
- present but mismatched hash/version: `版本不一致`;
- exact contract but incomplete windows or samples: `等待验证` or `样本不足`;
- exact contract with completed sufficient evidence: `同源已验证`.

The evidence builder never substitutes the current hash for a missing validation hash. Score-bucket validation reads only the score field declared by the source snapshot; it never falls back to `total_score`, opportunity score, or another version. Validation tasks can write validation/evidence records only and must not mutate signal scores/ranks, allocation, tracked positions, alerts, or notifications.

Alternative considered: keep evidence as a small score component after shrinkage. This violates the project’s one-way evidence boundary and creates a self-referential strategy, so evidence remains explanatory.

### 9. Validation uses date-level, point-in-time observations

The minimum correct validation design avoids a large modeling framework:

- reconstruct or read only exact-contract point-in-time full snapshots;
- calculate one equal-weight observation per signal date and Top-N bucket instead of treating N ETFs as N independent time samples;
- use non-overlapping signal dates for each 1/3/5/10-day horizon;
- define the primary endpoint as Top 10, five-trading-day net excess return versus the same-date `all_scored` baseline;
- use T+1 adjusted close as the versioned conservative entry model, an adjusted close after the horizon as exit, and fixed versioned two-sided cost/slippage;
- report unique/effective sample dates, asset and price coverage, mean/median net return, paired excess return, win rate, drawdown, turnover, exclusions, and a date-block bootstrap 95% confidence interval;
- keep Top 5/20/50 and other horizons exploratory.

Evidence state and direction are separate. Fewer than 20 independent dates or less than 95% required coverage is `insufficient`; a confidence interval crossing zero is `inconclusive`; an interval below zero is `negative`; only a lower bound above zero is `supportive`. Negative evidence is displayed and can never become a positive live-score component.

Alternative considered: immediately add factor regression, HAC estimators, and automated hyperparameter selection. Date-level aggregation, non-overlapping windows, paired baselines, and block bootstrap address the largest current biases with much less machinery.

### 10. Intraday adjustment requires a compatible fresh base and normalized evidence

An intraday total is available only when the daily base snapshot is compatible and fresh and the selected quote is cross-provider decision-eligible. A fresh quote with a stale daily base may be displayed, but it cannot produce a “fresh intraday comprehensive score”.

Price movement is standardized by the ETF’s volatility/ATR and homogeneous asset bucket rather than one fixed percentage threshold. Activity compares current cumulative turnover with the historical distribution at the same exchange minute; if the same-time reference is unavailable, the activity adjustment is unavailable rather than automatically penalized. Premium/discount, spread, quote consensus, and freshness can contribute only with traceable eligible values. Exchange holidays and lunch/close transitions use an exchange calendar.

Scheduled all-user monitoring may still evaluate each owner’s positions, but API watchlist source labels and tracking filters are computed for the authenticated user only. Current alert-state filters ignore expired historical alerts.

Alternative considered: retain fixed thresholds for simplicity. The same move and cumulative turnover have materially different meaning across ETF types and times of day, so fixed thresholds systematically bias ranks.

### 11. API compatibility is additive and the UI consumes explicit state

Existing endpoint paths remain. Responses add a snapshot block and explicit rank/state fields, including:

- `snapshot_id`, `score_version`, `ranking_contract_hash`, `scope_kind`, `as_of_trade_date`, `generated_at`, `coverage_ratio`, and `freshness_status`;
- `global_rank`, `live_scope_rank`, `filtered_position`, `rank_scope`, and nullable `rank_change`;
- `score_source`, `market_status`, `next_poll_seconds`, `decision_eligible`, and limitations.

Legacy `rank`/`total_score` response fields can remain during migration but map to the documented daily global rank/current final score only; validation never consumes those aliases. Static tracking filters are implemented for the authenticated user instead of being silently ignored. Unknown or unsupported filter combinations return a clear validation error.

The frontend uses response `as_of_trade_date`, not a global maximum from another asset class. It distinguishes loading, empty, error, stale, waiting, and unavailable states; clamps pagination when totals shrink; preserves a previous-page action; and uses Asia/Shanghai calendar dates. Market status comes from one effective source, with boundary refetches at open, lunch, reopen, and close and server-provided polling intervals.

Private React Query keys include user identity. Logout or identity change cancels in-flight private requests and clears private caches; request cancellation is passed through the API client. This change may extract small query/status/rank helpers but does not require a broad page redesign.

Alternative considered: patch only the displayed labels. Explicit API semantics and cache isolation are needed because downstream logic, not just text, currently uses ambiguous state.

### 12. Active OpenSpec changes are reconciled before promotion

This change does not duplicate already-correct cross-sectional, sector, theme, or factor implementations. During implementation, each pending task in `make-opportunity-final-decision-ranking`, `improve-etf-cross-sectional-ranking`, `add-etf-sector-trend-scoring`, `expand-etf-factor-library`, and `add-etf-theme-catalyst-scoring` is classified as retained, superseded, or blocked by this contract.

The conflicting “label evidence contributes to final score” behavior is superseded explicitly. Existing tests that still expect opportunity/theme heat to determine the comprehensive ordering are migrated to assert final-score semantics while preserving theme evidence as explanation.

## Risks / Trade-offs

- [Strict selection initially leaves the workbench in `等待数据`] → Deploy schema and producer changes first, generate a shadow v3 full snapshot, and promote only after coverage and comparison gates pass.
- [Adjusted-price history changes historical metrics and ranks] → Preserve raw series, version the price basis, compare v2/v3 in shadow, and never relabel old evidence as current.
- [A 95% coverage barrier can delay daily publication during provider incidents] → Prioritize stale/missing ETFs, expose exact exclusions, and prefer an honest delayed snapshot to mixed-date ranks.
- [Point-in-time universe history increases storage and migration work] → Store membership intervals rather than a full copy per day; snapshot hashes and item membership preserve exact run cohorts.
- [Full-universe-first computation costs more than code/theme runs] → Reuse one published full snapshot for filters and partial views; do not recompute percentiles for presentation filters.
- [New rank fields can confuse existing consumers] → Keep temporary aliases, add contract tests, migrate the workbench first, and publish deprecation notes before removing aliases.
- [Date-level validation accumulates sufficient samples more slowly] → Report `insufficient` honestly and keep exploratory metrics separate from the primary endpoint.
- [Current active changes can reintroduce superseded behavior] → Add an explicit overlap checklist and run architecture/contract regression tests before completing this change.
- [Auth cache clearing may cause additional refetches] → Clear only private query namespaces and retain public immutable data where safe.

## Migration Plan

1. Reconcile active OpenSpec requirements, pause their pending deployment/recompute tasks, and mark conflicting label-evidence scoring work as superseded.
2. Add nullable snapshot, adjusted-data provenance, and point-in-time universe schema; deploy without changing readers.
3. Apply immediate safety fixes: stop validation feedback, enforce hard caps last, classify missing hashes as legacy, remove validation score fallback, fix user scoping, and stop filtered re-ranking claims.
4. Implement same-date sync/coverage workflow and typed producer-consumer contracts; generate `final_score_v3` snapshots in shadow.
5. Backfill only factual provenance and point-in-time membership that can be reconstructed. Do not synthesize contract hashes; all pre-v3 runs and evidence remain legacy.
6. Compare v2 and v3 coverage, finite outputs, rank stability, risk invariants, and known fixtures. Publish v3 only after all contract gates pass.
7. Switch daily, detail, live, portfolio, and frontend consumers to the current v3 snapshot selector; retain explicit degraded status if rollback selects legacy data.
8. Recompute ranking history and research-only validation under the v3 contract. Evidence remains `等待验证` or `样本不足` until independent-date thresholds are met.
9. Remove deprecated read aliases only in a later change after all consumers have migrated.

Rollback selects the previous reader version through one configured score-version selector and disables v3 publication; it never rewrites or deletes v3 snapshots. Any v2 results shown after rollback remain explicitly old/degraded evidence and cannot regain current-contract status.

## Open Questions

No user decision blocks the proposal. Implementation must confirm provider-specific adjusted-price semantics and available same-time intraday history during the first data-contract task; if a source cannot meet traceability requirements, that component remains unavailable rather than receiving a fallback.
