## Why

The latest production ETF ranking is operationally publishable but not yet reliable enough to be interpreted as a tradable candidate list. The 2026-07-31 snapshot ranked 1,376 of 1,490 authoritative ETFs, yet 65 of the displayed Top 100 failed the existing CNY 50 million average-turnover gate, 365 ranked ETFs had an unknown asset bucket, and none of the 1,490 active membership rows carried a tracked-underlying identifier. Cross-border names such as Hang Seng Technology and Hong Kong innovative-drug ETFs are also classified as domestic equity themes.

The evidence path is weaker than the production ranking path. Only one factual production PIT date exists, later research-loop phases are explicitly unavailable, and adjusted history upserts can overwrite `source_timestamp`, preventing stable reconstruction of what was visible at an earlier cutoff. Production also contains multiple published snapshots for the same trade date with identical ranks but different contract hashes, showing that readiness policy semantics and canonical publication identity need stronger versioning.

These issues should be corrected before changing formal ranking weights or promoting theme, catalyst, leader-tactics, sentiment, or machine-learning scores. Existing leader historical proxy evidence remains `insufficient_data`, and its confidence intervals do not establish incremental alpha.

## What Changes

- Enforce a versioned absolute tradability gate on the default research ranking while keeping excluded ETFs searchable in a clearly separate low-liquidity observation view.
- Correct ETF taxonomy precedence, require auditable bucket confidence, and exclude unknown or incompatible buckets from peer-relative/actionable calculations until classified.
- Populate authoritative tracked-underlying identities and apply one-underlying clone control to ranking distributions, Top-N presentation, diagnostics, and concentration evidence.
- Repair percentile scoring before clone activation so every primitive and component remains finite and bounded in `[0, 100]`, including values that do not equal a clone-group mean.
- Separate global discovery score, within-bucket comparison, and diversification presentation so small peer buckets or repeated themes cannot manufacture globally comparable extremes.
- Preserve immutable first-seen availability and append-only revisions for adjusted history; PIT replay reads the latest revision factually visible at its cutoff.
- Connect durable candidate, forward-outcome, ranking-validation, factor-evidence, policy-shadow, and final-evidence phases to the existing bounded production PIT coordinator without creating production side effects.
- Introduce a new readiness-policy version for the existing 95-percent daily / 90-percent warm-up contract, preserve legacy semantics, and enforce one canonical published snapshot per trade date and exact surface contract.
- Bind provider-health identity, cutoff provenance, quality-gate counts, clone coverage, taxonomy coverage, and duplicate-publication evidence into the snapshot seal and research evidence API.
- Correct rank churn and implementation-cost diagnostics, including daily transitions, bid/ask or liquidity-aware costs where factual inputs exist, and explicit fallback to conservative declared costs when they do not.
- Clarify the workbench: the default surface is a daily research rank, the actionable surface is a separate intraday execution-qualified rank, and ETF sort controls MUST NOT present two names for the same score order.
- Keep all factor changes in shadow experiments. Formal weights remain frozen until the existing PIT sample, walk-forward, uncertainty, concentration, drawdown, holdout, and manual-promotion gates pass.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `short-etf-research`: Enforce research-rank tradability, taxonomy, clone, and honest display eligibility.
- `etf-ranking-surfaces`: Bound percentile calculations, define peer and clone semantics, separate score order from diversified presentation, and clarify research versus actionable behavior.
- `etf-point-in-time-research-loop`: Preserve immutable adjusted-data visibility and persist all bounded research phases after Stage A/B.
- `etf-research-evidence-contract`: Add quality, taxonomy, clone, provider-health, publication-version, and immutable-revision provenance.
- `etf-publish-readiness-sync`: Version the 95/90 readiness policy without reinterpreting old rows and prevent duplicate canonical publication.
- `etf-factor-incremental-alpha-validation`: Measure daily churn and realistic costs and require incremental evidence for any factor or ranking candidate.

## Impact

- Backend: ETF universe enrichment, taxonomy, adjusted-history persistence, daily research eligibility, V3 percentile and clone logic, dual snapshot materialization/publication/selection, PIT coordination, factor diagnostics, and evidence APIs.
- Frontend: ETF research/actionable labels, sort controls, low-liquidity and unknown-taxonomy states, confidence and concentration presentation, and stable unavailable reasons.
- Data: additive tracked-underlying provenance, taxonomy evidence, immutable first-seen/revision facts, publication policy version, provider-health hash, and PIT phase artifacts. Existing immutable snapshots are not rewritten or deleted.
- Operations: one worker, serial 5-to-20-code pages, at most 55 seconds per continuation, current RSS below the existing 512 MiB gate, and no unbounded synchronization on the 2-core/4-GB server.
- Research: current score weights, leader/catalyst weight zero, live positions, allocation, alerts, notifications, SMTP, and execution state remain unchanged until a separate manually approved promotion change.
