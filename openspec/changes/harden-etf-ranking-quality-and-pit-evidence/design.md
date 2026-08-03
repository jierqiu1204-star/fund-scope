## Context

FundScope now publishes two ETF surfaces in one immutable dual-ranking snapshot:

- `daily_reconstructable_v1.research_score` is the default daily research surface. It uses exactly 61 decision-eligible total-return-adjusted OHLCV sessions and a frozen 55/30/15 trend, risk, and relative-volume formula.
- `actionable_rank_v1` wraps `final_score_v3` and requires at least 120 eligible adjusted sessions plus same-session bid, ask, IOPV, premium/discount, provider health, freshness, and provider-consensus evidence.

This separation is directionally correct, but the current production state exposes contract and implementation gaps:

- 65 of the latest research Top 100 fail the already-declared CNY 50 million average-turnover quality gate because research materialization checks the score contract but not `default_display_eligible`.
- `tracked_underlying_id` is absent for every active membership, so clone control is effectively disabled and repeated index/theme exposure crowds the top ranks.
- 26.53 percent of ranked ETFs are `unknown`, while cross-border keyword precedence misclassifies known Hong Kong products as domestic equity.
- The V3 percentile function assumes the scored value exists in the clone-mean distribution. Once clone metadata is populated, values between or outside clone means can produce scores below zero or above 100 before the terminal cap.
- adjusted-history upserts replace provider/version/value and `source_timestamp` in one mutable code/date row, so repeated synchronization can alter PIT visibility.
- the production PIT composition persists only inputs and Stage A/B; later phases are deliberately unavailable.
- readiness policy semantics changed to 95-percent daily / 90-percent warm-up without a new durable policy identity, and multiple same-date published runs exist under distinct contract hashes.
- the workbench correctly has research/actionable surface buttons, but its ETF sort menu still gives two labels to the same score order.

The change must preserve the modular-monolith dependency direction, the existing resource bounds, fail-closed financial-data behavior, and research-only promotion boundary.

## Goals / Non-Goals

**Goals:**

- Make the default research ranking a genuinely tradable discovery universe while retaining excluded ETFs in a separate searchable observation state.
- Establish authoritative, auditable taxonomy and tracked-underlying identities before using clone and peer-bucket logic.
- Ensure every score primitive, component, aggregate, and diagnostic is finite, bounded, deterministic, and batch-invariant.
- Make production snapshots and PIT replay reproducible from immutable availability and revision evidence.
- Connect the already-designed frozen-candidate and validation phases to bounded production PIT continuation.
- Version readiness semantics without retroactively reinterpreting immutable snapshots.
- Expose enough evidence to explain why an ETF ranked, why it was excluded, and whether the ranking is operationally available versus statistically validated.
- Preserve the current score weights until pre-registered out-of-sample gates pass.

**Non-Goals:**

- Raising expected returns by tuning the production 55/30/15 or V3 weights in this change.
- Adding leader, catalyst, theme, sentiment, valuation, AI, or machine-learning scores to production.
- Lowering the current 95-percent target-date or 90-percent 61-session publication thresholds.
- Using raw Sina/efinance prices, estimates, stale quotes, current-vintage membership, or synthetic receipt times to increase decision or PIT coverage.
- Making the actionable surface non-empty by relaxing mandatory intraday evidence.
- Changing portfolio allocation, tracked positions, risk alerts, notification policy, SMTP behavior, or execution provenance.
- Running concurrent or unbounded history synchronization on the 2-core/4-GB server.

## Decisions

### 1. Treat research eligibility, score, and presentation as separate contracts

The frozen `daily_reconstructable_v1` formula remains unchanged. A research row is canonical only when it passes:

1. authoritative universe membership;
2. 61 decision-eligible total-return-adjusted sessions ending on the target date;
3. finite adjusted OHLCV and compatible adjustment provenance;
4. the versioned absolute tradability gate, initially the existing CNY 50 million 20-session average-turnover threshold;
5. a known compatible asset bucket with auditable classification evidence.

An ETF that fails items 4 or 5 remains searchable as `observation_only` with exact reasons, but it does not receive a canonical research rank and cannot count toward actionable or Top-N portfolio selection. This resolves the current contradiction in which the score contract claims an upstream absolute-liquidity gate while the published research rank bypasses it.

The raw research score remains distinct from:

- `peer_percentile`: explanatory within-bucket placement;
- `diversified_view_position`: an optional clone/theme-controlled display position;
- `actionable_rank`: execution-qualified ordering.

None of these fields aliases or silently replaces another contract.

### 2. Make taxonomy evidence authoritative and precedence-aware

Classification uses ordered evidence rather than first matching an unconstrained keyword list:

1. verified product or tracked-index metadata;
2. manual override with version and reason;
3. cross-border jurisdiction/index markers;
4. asset-class markers such as bond, commodity, money, and broad-base;
5. domestic sector/theme keywords;
6. unknown.

`恒生科技`, `港股创新药`, and other explicit overseas markers therefore remain cross-border even when their names also contain technology or healthcare terms. Every classification records source, rule version, confidence, observed-at cutoff, and evidence hash.

Unknown classifications remain visible in diagnostics but are excluded from peer-relative and actionable ranking. Publication records unknown counts and bounded samples, and production acceptance includes a declared taxonomy-coverage threshold rather than silently treating unknown as a comparable bucket.

### 3. Populate tracked-underlying identity before enabling clone control

Tracked-underlying identity is sourced from authoritative index/product metadata and persisted with provider, provider version, source identifier, observation cutoff, and evidence hash. A normalized identity may not be guessed solely from a fund name.

Clone semantics are:

- one tracked underlying contributes one observation to price-derived peer distributions;
- clone-group price primitives use the same deterministic group aggregate when calculating their shared price component;
- product-specific execution primitives such as spread, premium, provider consensus, fees, and liquidity remain ETF-specific;
- canonical Top-N discovery may show one representative per underlying, selected by a frozen tradability order, while all clones remain searchable;
- clone coverage, unresolved identities, representative choices, and concentration are evidence fields.

Until tracked-underlying coverage passes its declared threshold, clone-aware results are shadow diagnostics and cannot claim complete clone control.

### 4. Replace the unsafe percentile formula with a bounded empirical rank

The percentile primitive uses a deterministic empirical midrank that is defined whether or not the scored value appears in the reference distribution. It MUST:

- return unavailable when fewer than the declared minimum peer count exists;
- handle values below, between, equal to, and above observed peer values;
- remain within `[0, 100]` before aggregation;
- preserve direction reversal after the bounded rank is calculated;
- produce identical results across clone grouping, input order, and 5/10/20 batch sizes;
- record a cap violation if any component or aggregate would otherwise leave its declared bounds.

The minimum peer count becomes a versioned contract parameter rather than the implicit value two. Small buckets remain unavailable or shrink toward a declared neutral diagnostic; they do not generate artificial 0/100 global extremes.

### 5. Keep production weights frozen and test structural improvements in shadow

No production score weight changes in this change. Strategy Lab pre-registers at most three structural candidates against the current baseline:

1. current canonical Top10 after corrected eligibility and clone handling;
2. the same score with the already-frozen Top15 retention buffer, maximum one daily replacement, and three-session minimum hold;
3. candidate 2 with the existing regime and liquidity gate.

Additional diagnostics measure, without production promotion:

- non-overlapping or residualized momentum versus nested 5/10/20/60-day returns;
- sector momentum residualized against technical momentum;
- turnover or amount-based liquidity robust to share splits and unit changes;
- 61-119, 120-249, and 250-plus history tiers;
- bucket, theme, underlying, and provider concentration.

Leader and catalyst candidates retain production weight zero until they independently pass the existing promotion contract.

### 6. Preserve first-seen availability and append-only adjusted revisions

The mutable current-price row remains the efficient latest view, but PIT truth moves to append-only evidence:

- `first_seen_at` is immutable once a code/date observation is accepted;
- each material provider/version/adjustment/value change creates a revision row with `observed_at`, payload hash, supersession link, and decision-eligibility evidence;
- an idempotent replay selects the latest compatible revision whose observation time is no later than its visibility cutoff;
- resynchronization may update the current projection but cannot overwrite historical first-seen or revision facts;
- existing rows without factual first-seen evidence receive no synthetic historical credit.

Migration backfills only structural links that are provable from existing immutable timestamps. Ambiguous old rows remain current-production data and are excluded from historical PIT credit.

### 7. Persist all production PIT phases through the existing coordinator

The workflow layer replaces unavailable handlers with bounded durable adapters for:

- frozen candidates;
- forward outcomes;
- ranking validation;
- factor evidence;
- policy shadow;
- final evidence.

Each adapter consumes only prior immutable artifacts, advances at most one page, persists its artifact outside the mutable checkpoint, and returns below 55 seconds. Outcome phases wait for the declared future exchange sessions instead of shifting dates. Research execution remains unable to mutate production ranking, allocation, positions, alerts, notifications, SMTP, or holdout state outside its declared contract.

Operational availability and statistical readiness stay separate:

- `ranking_ready` means the current target-date ranking can publish under the 95/90 data contract;
- `research_ready` requires at least 252 factual PIT sessions, 40 independent five-session primary dates, three chronological folds, adjusted uncertainty, locked holdout state, and all existing quality gates.

### 8. Introduce a new readiness version and canonical-publication constraint

The 95-percent daily / 90-percent warm-up thresholds remain unchanged, but their semantics receive a new policy version. Historical v1/v2 rows are evaluated under the exact policy version persisted when they were created; the new implementation MUST NOT reinterpret an old policy identifier using new thresholds or state transitions.

Canonical publication identity includes trade date, scope, research contract hash, actionable contract hash, readiness-policy version, universe hash, input hash, cutoff, and surface-group hash. Publication uses a database-level uniqueness guard or equivalent locked canonical registry so two workers cannot publish the same exact identity. A later contract version may publish a new immutable row, but selectors return exactly one current-compatible canonical row and expose supersession lineage.

Provider-health snapshot hash, provider check time, covered fields, and source range become part of the draft seal. A complete research snapshot may still contain zero actionable rows, but it is labeled `research_complete_actionable_unavailable`, not generically `complete dual`, and the exact actionable blocker summary is retained.

### 9. Measure daily implementation churn and factual costs

Turnover and rank churn are separate metrics:

- turnover measures membership/weight changes between every eligible daily portfolio transition;
- rank churn measures normalized rank movement among common members;
- non-overlapping primary outcome sampling does not erase intervening daily transitions;
- costs use factual spread and liquidity/impact evidence when it was available at the execution cutoff;
- otherwise the frozen conservative fee/slippage model remains explicit and no missing execution evidence is reconstructed.

All cost models, minimum commission assumptions, position size, and unavailable reasons are versioned in the experiment manifest.

### 10. Make workbench semantics match backend surfaces

The default button and heading use `日线研究榜`; the second surface uses `盘中可行动榜`. The ETF sort selector contains only metrics that actually change order within the selected surface. It does not present `综合榜单` and `盘中买点榜` as two sort keys when both map to the same loaded score.

Each row shows:

- research/actionable rank and score identity;
- history tier;
- absolute tradability state;
- taxonomy and clone representative state;
- snapshot age and cutoffs;
- stable exclusion reasons.

Low-liquidity, unknown-taxonomy, provisional, stale, and no-actionable-candidate states remain visually and semantically distinct.

## Risks / Trade-offs

- [Applying the existing liquidity gate reduces the canonical research count and can materially reorder Top N] → Publish before/after coverage, Top-N diff, theme/clone concentration, and common-support backtest evidence; retain excluded ETFs in observation-only search.
- [Underlying metadata may be sparse or provider-specific] → Require provenance and an explicit unresolved state; do not infer identities from names for formal clone evidence.
- [Clone aggregation can hide the best product wrapper] → Aggregate only shared price primitives and keep ETF-specific structure/execution fields; expose every clone and deterministic representative rationale.
- [Taxonomy corrections change peer distributions] → Version the taxonomy, materialize old/new shadow comparisons, and require deterministic regression fixtures for known cross-border, bond, commodity, broad-base, and sector cases.
- [Append-only revisions increase storage] → Store compact hashes and changed fields, index code/date/observed-at, and retain the current projection for fast production reads.
- [PIT completion will take many real trading days] → Expose monotonic dates, independent samples, pending windows, folds, and ETA inputs; do not synthesize history or weaken promotion gates.
- [A uniqueness migration can conflict with existing same-date rows] → Preserve all immutable rows, choose the current-compatible canonical row through an additive registry, and mark identical older identities superseded without deleting or mutating their evidence.
- [More conservative costs may reduce already weak returns] → Treat that as truthful evidence rather than tuning the model to recover the prior result.

## Migration Plan

1. Add regression tests for the current production failures: low-liquidity Top-N inclusion, unknown and cross-border taxonomy, missing underlying coverage, percentile out-of-range, duplicate policy version, duplicate canonical publication, mutable receipt time, and duplicate ETF sort semantics.
2. Add taxonomy and tracked-underlying provenance storage, populate only authoritative facts in bounded serial pages, and expose coverage without enabling clone scoring.
3. Repair percentile, component bounds, cap-violation propagation, peer-count policy, and contract parser validation; verify deterministic results before enabling clone groups.
4. Enforce the versioned research tradability/taxonomy gate and introduce observation-only results; compare old/new production-shaped rankings without changing score weights.
5. Add immutable first-seen and adjusted-revision evidence; switch PIT replay to cutoff-aware revision selection while retaining the current fast projection.
6. Add the new readiness-policy version, canonical publication registry/constraint, provider-health seal fields, supersession lineage, and honest research/actionable snapshot states.
7. Connect candidate through final-evidence PIT phase adapters one bounded page at a time with production-isolation tests.
8. Correct daily turnover, rank churn, and cost evidence, then run the three frozen structural candidates in shadow only.
9. Update the API and workbench labels, filters, sort controls, quality states, and evidence panels.
10. Deploy with new ranking publication disabled, run bounded production shadow verification, then enable the new policy only after one complete target-date snapshot passes rollback, identity, coverage, resource, and API checks.

Rollback selects the prior compatible published snapshot and disables new publication/PIT adapters. It does not delete taxonomy, underlying, revision, snapshot, or research evidence; restore raw-price fallback; rewrite legacy policy meaning; or change production decision domains.

## Open Questions

- Which authoritative source supplies stable tracked-index identifiers for every exchange ETF, and what minimum coverage should be required before clone-aware canonical presentation is enabled?
- Should the existing CNY 50 million threshold remain universal or become a separately pre-registered position-size/impact policy after sufficient factual spread and turnover evidence exists?
- What peer-count minimum and shrinkage rule best balance commodity, bond, and cross-border buckets without making them incomparable to large equity buckets? This must be selected before outcome inspection and validated in shadow.
