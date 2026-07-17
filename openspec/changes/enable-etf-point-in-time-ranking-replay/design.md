## Context

FundScope currently has two different evidence shortages that both surface as `N/A`. The production ranking validator has no compatible historical published `final_score_v3` snapshots, while the notification/action database has no completed live action cycles or notification attempts. The six stored signal runs are one-date legacy/v2 rows with v3 shadow diagnostics, not immutable v3 ranking snapshots. Treating them as published or rebuilding v3 with current quote/theme data would create look-ahead evidence.

The repository already contains useful bounded replay primitives under `app.services.strategy_lab.etf_action_replay`: point-in-time input cutoffs, complete cross-section manifests, sequential feature/replay batches, atomic checkpoints, immutable artifacts, T+1 simulated execution, action-cycle benefit, frozen candidate registries, walk-forward purging, clustered bootstrap, and one-time holdout guards. This change extends those primitives for ranking research instead of adding a second backtest engine.

The deployment target is 2 CPU cores and 4 GB RAM. Every continuation must therefore use one worker, explicit row/date/event bounds, persisted progress, and a hard runtime limit of 55 seconds. Financial outputs prefer unavailable or insufficient states over raw-price, current-universe, estimated, or post-hoc fallback results.

## Goals / Non-Goals

**Goals:**

- Produce auditable research-only ETF ranking cohorts from data provably reconstructable at each decision cutoff.
- Give Top5/10/20 and 1/3/5/10-day research results without relabeling them as historical production v3 evidence.
- Supply provenance-safe ranking cohorts to the existing action-policy replay so policy-shadow accuracy and return can be measured separately from live notification and execution facts.
- Evaluate only a small frozen candidate set with chronological validation, explicit costs, uncertainty, turnover and drawdown controls.
- Complete large replays through deterministic, idempotent, bounded batches on the production hardware profile.

**Non-Goals:**

- Backfilling old signal runs with fabricated v3 identities, premium, IOPV, spread, provider consensus, catalyst data, or current contract hashes.
- Replacing the production `final_score_v3` writer/publication fix in `harden-etf-comprehensive-ranking`.
- Calling a policy-shadow notification an email send, SMTP acceptance, provider-confirmed delivery, or user execution.
- Searching ranking weights, running threshold grids, selecting the best Top-N/horizon after seeing outcomes, or automatically changing live ranking/action policies.
- Using Sina/efinance raw prices, stale quotes, estimated prices, or current-universe membership as decision evidence.

## Decisions

### 1. Evidence dimensions are orthogonal

Every result records independent dimensions instead of one ambiguous `same_contract` flag:

- `ranking_source_kind`: `production_published` or `research_replay`;
- `signal_contract_compatibility`: `same_production_contract`, `same_replay_contract`, `mismatch`, or `legacy`;
- `action_policy_contract_compatibility`: `same_contract`, `mismatch`, or `legacy`;
- `policy_mode`: `production_live` or `policy_shadow`;
- `notification_provenance`: `not_attempted`, `shadow_eligible`, `smtp_failed_live`, `smtp_unknown_live`, `smtp_accepted_live`, or `provider_delivered_live`;
- `execution_provenance`: `none`, `simulated_execution`, or `user_confirmed`.

`provider_delivered_live` requires a provider receipt id and timestamp. SMTP acceptance is not delivery, and neither delivery state is execution. A replay may match the current action policy while still using a different research ranking contract; those facts remain separately visible.

Alternative considered: add `research_replay` as another `ShortResearchSignalRun.publication_state`. Rejected because it would make canonical-selector mistakes likely and blur production publication with research artifacts.

### 2. Historical ranking uses a distinct daily-reconstructable contract

The first replay score contract is `daily_reconstructable_v1`, with `score_field=research_score` and `price_basis=total_return_adjusted`. It reuses the existing deterministic daily score semantics: trend 55%, risk 30%, and daily liquidity 15%, including the exact symmetric overextension definition `abs(adjusted_close - adjusted_MA20) / adjusted_ATR20`. It requires finite adjusted OHLCV warm-up and never consumes intraday spread/IOPV/premium/provider-consensus, theme/catalyst, current validation evidence, positions, alerts, or notifications.

The weights are inherited and frozen before outcome calculation; this change does not optimize them. `daily_reconstructable_v1` has its own manifest and hash and can never compare as the same signal contract as `final_score_v3`. Missing required daily inputs make the row unavailable; no zero, neutral 50, stale value, or weight renormalization is allowed.

The score implementation is extracted into a small pure daily-replay scorer used by both historical feature materialization and existing daily replay callers. Strategy Lab consumes the scorer but cannot write production ranking state.

Alternative considered: reconstruct exact v3. Rejected because historical quote structure, premium, provider consensus, catalyst evidence, and pre-2026-07-13 universe membership do not currently exist.

### 3. Point-in-time eligibility is proven, not inferred

For date T, replay accepts only membership intervals and metadata whose effective/availability facts cover T, and input rows whose source cutoff is compatible with the declared decision cutoff. A current ETF list, later theme classification, future delisting status, or today's ranking cannot fill historical membership.

Historical membership authority is an additive, Strategy-Lab-only, immutable fact table; the operational `EtfUniverseMembership` table remains production-current state and is never copied, inferred, or migrated into replay history. Every accepted fact carries `external_source_id`, `provider`, `provider_version`, `observed_at`, its effective interval, `evidence_hash`, and `raw_payload_hash`. The replay loader reads only these receipt-backed facts, excludes receipts observed after its cutoff, and treats a real date with no qualifying receipt as unreconstructable. Distinct overlapping facts, hash/payload mismatches, updates, and deletes are rejected. The bounded audit/backfill path accepts externally supplied receipts only, supports dry-run, paginates by stable cursors under row/date/time limits, and reports gaps without deriving membership from current survivors, prices, or metadata.

Adjusted data must preserve provider, adjustment kind/version, source timestamp, and decision eligibility. A provider history synchronized later may be used for scale-invariant return, MA-distance, volatility, drawdown and ATR-ratio features only when an immutable before/after provider receipt proves that the actual revision was one constant multiplicative transform and invariance fixtures prove that unit-factor scaling cannot change those features. A provider/version name alone is not proof of the actual revision; until revision receipts exist, any row timestamped after the cutoff is rejected. Otherwise the date is excluded as `unproven_adjustment_point_in_time` or `future_known_input`. Absolute price-level features are not part of this replay contract.

Forward outcomes use the existing conservative T+1 adjusted-close model and versioned two-sided costs. Missing entry/exit values remain exclusions; the signal-date close is never substituted.

Alternative considered: infer membership from the first stored price or use all current ETFs. Rejected because listing history alone does not prove historical decision eligibility and omits delisted products.

Alternative considered: enrich or reinterpret `EtfUniverseMembership` as historical evidence. Rejected because its rows do not carry the external receipt identity, provider version, observation timestamp, or payload/evidence hashes needed to prove what was knowable at a historical cutoff.

### 4. Materialization is two-stage, bounded and chunk-invariant

Stage A processes code/date feature units in canonical order and writes immutable feature artifacts. Each invocation uses one worker, caps source rows/output items, stops by 55 seconds, and atomically records the last completed unit plus all contract/schema/input hashes.

Stage B processes complete dates only. It verifies the authoritative point-in-time membership and feature manifest before computing one full cross-section, global ranks, Top-N cohorts and `all_scored`. It never ranks individual code chunks. A date is committed atomically; interruption resumes after the last complete date. Changing candidate definitions, cutoff, universe/input hash, feature schema or score manifest rejects the checkpoint and requires a new replay identity.

The same replay with different safe chunk sizes, interruption points, or one-shot execution must produce identical feature hashes, ranks, portfolios, events and summaries.

Alternative considered: load every ETF/date into memory and rank once. Rejected because it can exhaust the 2-core/4-GB server and cannot safely resume.

### 5. Ranking candidates are frozen and evaluated before action candidates

The ranking stage registers at most three candidates:

1. `daily_core_top10`: daily equal-weight Top10 from `daily_reconstructable_v1`;
2. `daily_core_top10_hysteresis`: Top10 with rank-retention buffer 15, at most one replacement per session, and a three-session minimum hold;
3. `daily_core_top10_hysteresis_regime`: candidate 2 plus the existing versioned FundScope regime/cash-wait and decision-eligible liquidity gates, without new outcome-tuned thresholds.

No grid or hyperparameter search is permitted. The ranking primary endpoint remains Top10, five-trading-day paired net excess return versus the same-date `all_scored` cohort under T+1 adjusted-close execution, 5 bps fee and 5 bps slippage per side. Top5/20 and 1/3/10-day cells remain exploratory.

Development uses chronological expanding or rolling windows recorded in the immutable run contract, a purge/embargo at least as long as the maximum ten-session outcome, trading-day clustered uncertainty, a maximum-drawdown non-inferiority gate, and a final holdout consumed once. The selected ranking candidate is frozen before it feeds the existing, separately frozen action-policy candidates. The system does not perform a 3-by-3 joint search.

The existing action primary endpoint remains Top20, ten-trading-day tax/fee-adjusted mean action-cycle benefit under its declared T+1 adjusted-open execution model. Ranking and action returns retain different field names and execution metadata.

Alternative considered: optimize final-score weights or choose the best Top-N/horizon. Rejected because the current evidence base is too small and the search would overfit.

### 6. Policy shadow reuses the action lifecycle without production writes

`policy_shadow` feeds replay rankings and immutable feature rows into the existing pure action state machine. It may create research events, simulated fills and `shadow_eligible` notification facts inside replay artifacts only. It does not create production action decisions, alert episodes, notification items/envelopes, SMTP attempts, tracked-position changes or ranking snapshots.

Reports show three distinct views when their evidence exists:

- full policy-shadow simulated execution benefit;
- sensitivity restricted to actual production `smtp_accepted_live` notifications, still with explicitly simulated or user-confirmed execution provenance;
- observed outcomes from `user_confirmed` execution facts.

The latter two stay N/A until real linked samples and completed future windows exist. Notification attempt frequency never becomes an action sample or a source of return.

Alternative considered: infer that every historical shadow-eligible notification was sent and executed. Rejected because it fabricates delivery and execution.

### 7. Persistence and APIs cannot pollute canonical ranking

Feature, manifest, checkpoint, ranking, policy and equity artifacts reuse the Strategy Lab artifact store with stable hashes. `EtfSignalValidationRun` gains additive typed replay provenance (`ranking_source_kind` and optional `source_replay_run_key`); its existing source contract/hash fields hold the declared replay identity when the source is research replay. Exact production validation still requires a real published source run id.

APIs expose the separate coverage dimensions: point-in-time universe, adjusted price, feature/component, score eligible, and forward outcome. Stable unavailable reasons include `no_production_published_snapshot`, `research_replay_not_materialized`, `incompatible_score_manifest`, `insufficient_point_in_time_universe`, `insufficient_score_coverage`, `insufficient_independent_dates`, `future_window_pending`, `missing_adjusted_entry_or_exit`, `no_live_notification_sample`, and `no_user_confirmed_execution`.

Database side-effect tests snapshot production ranking, allocation, tracking, risk-alert, action and notification tables before replay and require them to remain unchanged.

Alternative considered: persist replay rows as historical production signal items. Rejected because canonical selectors and evidence summaries would eventually treat them as live-compatible facts.

## Risks / Trade-offs

- [Historical universe membership is unavailable before the first factual interval] → Keep those dates `insufficient_point_in_time_universe`; backfill only externally verifiable intervals and never infer eligibility from current survival.
- [Adjusted provider history may contain later revisions] → Restrict the contract to proven scale-invariant features, persist provider/version, add invariance fixtures, and exclude unverifiable adjustment semantics.
- [A distinct daily score is less comprehensive than v3] → Display its exact component manifest and `research_replay` badge; never use it to close production v3 rollout tasks.
- [Hysteresis can miss rapid leadership changes] → Report missed upside, turnover, costs, drawdown and all exploratory slices; require validation stability rather than only higher mean return.
- [Two-stage artifacts increase storage] → Store compact canonical rows, page artifacts, retain hashes/checkpoints, and apply the existing bounded retention policy.
- [Real production email accuracy remains unavailable] → Show policy shadow separately and accumulate live notification/action provenance prospectively.

## Migration Plan

1. Add nullable replay-provenance fields and migration/rollback tests without changing existing readers.
2. Extract and contract-test the pure `daily_reconstructable_v1` scorer, including symmetric overextension and adjustment-scale invariance.
3. Add point-in-time DB loaders and Stage A feature continuation using the existing artifact/checkpoint machinery.
4. Add Stage B full-date ranking, coverage manifests, deterministic resume and Top-N forward validation.
5. Freeze and evaluate the three ranking candidates in development/walk-forward data; do not consume final holdout when sample or PIT coverage gates fail.
6. Connect the frozen ranking cohort to existing policy-shadow/action validation and expose orthogonal evidence fields.
7. Run bounded real-data replay only for dates whose provenance gates pass; keep every other result explicitly unavailable or insufficient.

Rollback disables the replay job/API and leaves immutable research artifacts unread. It does not delete or rewrite production rankings, action state, notifications or live evidence. Nullable provenance fields remain backward compatible.

## Open Questions

No product choice blocks implementation. The first data-contract task must verify the exact adjusted-provider semantics and available historical membership facts; unsupported dates remain excluded rather than weakening the contract.
