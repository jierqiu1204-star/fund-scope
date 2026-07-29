## Context

The current catalyst implementation is based on a small manual seed set and theme proxies. An archived proposal suggested subjective strength/confidence scores and a fixed opportunity-score weight, but production coverage and timestamp provenance are not sufficient to support that behavior.

This third ordered change starts only after ranking surfaces are separated and factor evidence has a point-in-time, research-only protocol. It creates an event fact layer and shadow evidence; it does not create a sixth score-bearing ranking layer.

Official announcements and public articles may be corrected, removed, published in different time zones, discovered late, duplicated, or unavailable. Historical evidence must therefore retain both when a source says an event was published and when FundScope first received that exact version.

## Goals / Non-Goals

**Goals:**

- Capture immutable, deduplicated source receipts from registered official and approved public sources.
- Normalize verified event facts and direct/proxy theme mappings without assigning strength points or rank weights.
- Reconstruct exactly what event evidence FundScope possessed at each historical cutoff.
- Distinguish a successful observation of no event from unavailable or inapplicable coverage.
- Allow AI-assisted extraction while keeping verification, scoring, and production decisions deterministic and separate.
- Produce pre-registered event-study evidence before considering any catalyst formula.

**Non-Goals:**

- Adding catalyst to `daily_reconstructable_v1`, `final_score_v3`, or `actionable_rank_v1`.
- Reordering rankings, changing allocation, generating tracked-position actions, or sending event-driven email.
- Treating manual seed values, search snippets, AI prose, or proxy themes as verified direct events.
- Claiming causal price impact from observational event studies.
- Backfilling a missed historical receipt as if FundScope had received it at publication time.

## Decisions

### 1. Store source receipts before event interpretation

A versioned source registry declares source ID, source class, allowed endpoint/domain, timezone, expected cadence, fetch policy, and active status. Each fetch produces an immutable receipt with:

- stable receipt ID and provider external ID when available;
- canonical URL and source ID;
- source-published time and server first-received time;
- fetch time, fetch result, content hash, raw-content reference, and parser version;
- correction or supersession lineage.

Deduplication uses source identity, external ID or canonical URL, and content hash. A changed hash creates a new receipt version; it does not edit the prior receipt.

Alternative considered: store only normalized events. Rejected because an event row without the original received version cannot prove point-in-time availability or support re-extraction.

### 2. Keep event facts score-free

A structured event version records stable event ID, event type, entities, title, receipt IDs, published and received cutoffs, effective start/end, direction (`positive`, `negative`, `neutral`, or `uncertain`), direct theme IDs, proxy theme IDs, taxonomy version, extraction method, verification state, and supersession lineage.

There is no strength score, catalyst score, sentiment heat score, rank delta, or default `±2.5` adjustment. Direction is a categorical description for event studies and UI, not a trading conclusion.

### 3. Make receipt time the historical availability boundary

A shadow snapshot as of cutoff T may include only an event version whose supporting receipt was first received by T, whose published time is no later than T, and whose version was not superseded as of T. A late-discovered old article first becomes available at its received time, not its publication time.

Corrections create new versions with new received times. Historical snapshots retain the earlier version and can show that it was later corrected.

Alternative considered: use publication time alone. Rejected because it creates lookahead whenever ingestion discovered an event late.

### 4. Represent coverage explicitly

For each registered source/theme/session observation the system records one of:

- `active`: at least one verified, effective event is present;
- `observed_none`: required sources were fetched successfully and no qualifying event was found;
- `unavailable`: a required source was not successfully observed;
- `not_applicable`: the frozen source/theme policy says the observation does not apply.

The aggregate state is deterministic and never converts `unavailable` to `observed_none`. Coverage carries source counts and reasons.

### 5. Constrain AI to extraction and classification

The model receives bounded untrusted source content and a schema. It may propose event type, entities, direction, dates, theme mapping, and summary, with receipt citations. Model output is schema-validated and stored as `pending` until deterministic required-field checks and, where configured, human review establish `verified`.

An LLM failure leaves the receipt available for deterministic or later extraction. AI output without a registered receipt, with conflicting dates, or with unsupported theme mapping remains unverified and cannot enter a verified shadow snapshot.

### 6. Treat manual and proxy evidence conservatively

Existing manual seeds without registered receipts are migrated as `manual_display_only`. Proxy theme mappings are explicit, versioned, and labeled. A verified event may be displayed through a proxy mapping, but it is excluded from direct-theme event evidence unless a pre-registered study explicitly defines that proxy cohort.

Neither manual nor proxy status can create a ranking or actionable state.

### 7. Reuse the research evidence protocol for event studies

Event studies use immutable manifests, point-in-time cohorts, decision-eligible adjusted prices, next-session execution, 1/3/5/10-session outcomes, same-date peer or sector controls, common support, exclusion reporting, chronological splits, embargo, block-bootstrap uncertainty, and multiplicity control.

Results report observed matched excess returns and limitations. They do not derive a catalyst score. Any later proposal for a formula or weight requires stable out-of-sample evidence across event types, regimes, and direct theme mappings.

### 8. Keep ingestion and reads bounded

Each registered source is fetched with one worker, a cursor, idempotent receipt writes, bounded item counts, and a hard timeout of at most 55 seconds. Extraction and mapping use small batches. Workbench reads cached shadow snapshots and never fetch external sources or run AI during page load.

## Risks / Trade-offs

- [Official sources may lack reliable publication times] → Record source precision and exclude ambiguous receipts from point-in-time studies while retaining them for display.
- [Late ingestion reduces historical coverage] → Preserve received time honestly and report gaps; never backdate availability.
- [AI may hallucinate mappings or direction] → Require receipt citations, schema validation, verification state, and no scoring authority.
- [Proxy mappings can create spurious evidence] → Label them explicitly and exclude them from direct cohorts unless pre-registered.
- [Event types may have very small samples] → Use sample-sufficiency states and avoid scores or confident conclusions.
- [Source outages may look like quiet news days] → Keep `unavailable` distinct from `observed_none`.
- [Storage grows with immutable versions] → Store hashes and raw-content references with bounded retention policy while preserving audit metadata.

## Migration Plan

1. Verify the first two ordered changes provide distinct ranking and immutable research-evidence boundaries.
2. Add source registry, receipts, event versions, theme mappings, coverage observations, and contract hashes.
3. Migrate existing manual seeds to `manual_display_only` without score-bearing fields.
4. Run a small allowlisted source set in shadow mode and verify receipt timestamps, duplicates, corrections, empty observations, and outages.
5. Add cached shadow snapshots and separate `/short-term` display.
6. Freeze and run event-study manifests only after sufficient verified direct-event coverage exists.
7. Retain zero catalyst ranking weight; create a separate proposal only if out-of-sample evidence later passes declared gates.

## Open Questions

- The initial official/public source allowlist, cadence, timezone, and raw-content retention policy must be approved before ingestion.
- Minimum verified direct-event coverage and event-study promotion thresholds must be frozen before outcomes are evaluated.
