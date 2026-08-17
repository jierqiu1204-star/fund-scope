## Context

See `proposal.md` for motivation. The production snapshot currently has three distinct concepts that are partially collapsed in materialization: adjusted-daily score readiness, research discovery quality, and same-session action safety. The score-ready numerator already excludes raw/fallback prices and enforces the immutable adjusted-data contract, while a later quality policy applies turnover and taxonomy gates before rows are persisted. `final_score_v3` is used only by the actionable surface; its primitive graph and hash must remain frozen during this evidence-building change. The existing Strategy Lab already provides PIT panels, IC, residual diagnostics, purged walk-forward validation, block bootstrap, multiple-testing control, immutable evidence, and bounded checkpoints.

## Goals / Non-Goals

**Goals:**

- Persist all score-ready daily research rows and label action-quality failures explicitly.
- Preserve every current action gate and prevent observation-only rows from influencing allocation or rank-derived notifications.
- Make snapshot coverage semantics non-overlapping and auditable.
- Correct peer-count diagnostics and add a frozen, bounded deconfounding research registry that reuses the existing experiment engine.
- Keep reads indexed and memory bounded on the 2-core/4-GB server.

**Non-Goals:**

- No production score-weight, formula, `final_score_v3` contract-hash, allocation, alert, or notification change.
- No provider substitution, lower readiness threshold, dynamic regime weight, parameter grid, ML model, or historical timestamp inference.
- No synchronous full-universe recomputation from an API request.

## Decisions

### 1. Treat score readiness, research quality, and action eligibility as separate states

Materialization will build the research rank from rows that pass only the adjusted-daily score contract. The existing quality evaluator remains authoritative for turnover, taxonomy, default-display, and related action-quality reasons. A score-ready row that fails quality is persisted with `research_quality_eligible=false`, `observation_only=true`, and stable reasons. Action materialization receives both score failures and quality failures, so its current fail-closed semantics become at least as strict as before.

Alternative considered: lower the 50-million-yuan threshold. Rejected because it would weaken action safety and mix discovery coverage with tradability.

### 2. Add layered summary counters without a database migration

The immutable run summary and item metrics JSON already carry versioned evidence. The new policy version adds `research_quality` and scalar surface counters while retaining existing fields for compatibility. Compact API metadata exposes counts and ratios but strips row arrays. Persisting all score-ready rows increases the latest expected row count from roughly 442 toward the existing score-ready numerator, but remains bounded by 1,500 rows and uses the existing indexed `(run_id, global_rank)` page path.

Alternative considered: a second research-quality table. Rejected because the state is immutable per snapshot, JSON evidence is already sealed, and another table would add migration and join cost without changing cardinality.

### 3. Keep legacy snapshots readable and write the broadened policy under a new quality-policy identity

The daily score and dual-surface rule remain compatible. The canonical quality policy and observation-state versions advance, changing the new snapshot's manifest and idempotency identity. Readers continue to accept prior published snapshots; publication atomically advances only after the new snapshot passes the unchanged readiness and seal checks.

Alternative considered: immediately invalidate the old rule version. Rejected because a deploy between market sessions could blank the production ranking.

### 4. Correct formal peer diagnostics but apply stricter support only in shadow

Formal composite peer reporting uses the minimum count across its required primitive distributions, not the maximum. This corrects diagnostics and fail reasons without changing successful scores because every primitive already requires at least two observations independently. A separate shadow policy freezes 20 non-clone common-support ETFs per bucket-date; candidate IC, redundancy, and portfolio evidence exclude smaller units.

Alternative considered: raise `final_score_v3` directly from two to twenty peers. Rejected because that changes production eligibility before PIT evidence exists.

### 5. Build deterministic redundancy clusters from rank correlation

For each factual signal date and peer bucket, diagnostics use only finite common-support rows. Pairwise Spearman correlations are aggregated across eligible bucket-dates. A deterministic union-find groups factors whose absolute aggregate correlation meets the frozen threshold. The report includes pair counts, cluster members, singleton and effective cluster counts, and unavailable reasons. Residual diagnostics continue to fit within each date and bucket, avoiding cross-date leakage.

Alternative considered: PCA or learned orthogonalization. Rejected because it is less interpretable, less stable with sparse PIT history, and unnecessary before the simple candidates have evidence.

### 6. Use a separate three-candidate deconfounding registry

The existing baseline/hysteresis/regime-liquidity registry remains unchanged. A new immutable registry contains:

1. `daily_reconstructable_baseline`: existing score, control only;
2. `residual_momentum_breadth_v1`: residualized momentum plus same-cutoff breadth, unavailable below 20 common peers;
3. `pit_flow_constituent_breadth_v1`: authoritative share-flow plus constituent breadth, unavailable unless both PIT inputs exist.

The registry records formulas and hashes but does not fabricate candidate values. It plugs into existing manifest and evidence structures and cannot mutate production.

## Risks / Trade-offs

- [Broader research rank exposes illiquid ETFs] → Mark rows observation-only, surface quality reasons prominently, and keep all action consumers fail-closed.
- [Larger persisted snapshots increase response or memory cost] → Reuse indexed pagination, avoid row arrays in compact metadata, cap materialization batches at 20, and add query-count/row-count tests.
- [Old callers interpret `eligible_count` as tradable] → Add explicit quality/action counters, retain compatibility fields, and update UI wording and API types.
- [Correlation clusters appear authoritative with little data] → Require 20 non-clone common-support observations per bucket-date, expose sample counts, and return unavailable rather than zero.
- [A diagnostic correction changes exclusion text] → Preserve score outputs and contract hash, version quality evidence, and cover successful-score parity in tests.

## Migration Plan

1. Deploy compatibility readers, layered schemas, and broadened materialization together.
2. Allow the next bounded canonical run to write a new policy identity; do not rewrite prior snapshots.
3. Verify score coverage, research coverage, quality coverage, observation-only count, action coverage, indexed read latency, and unchanged action exclusions before publishing.
4. Roll back by selecting the prior canonical publication and reverting the materializer; no destructive data migration is required.
5. Accumulate factual PIT captures and run the deconfounding registry in research-only mode. Any production factor change requires a later manual OpenSpec proposal.
