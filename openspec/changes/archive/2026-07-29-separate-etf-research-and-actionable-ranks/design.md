## Context

FundScope currently exposes `final_score_v3` as the formal comprehensive ETF ranking while production evidence shows that adjusted daily history can cover far more ETFs than trustworthy intraday bid/ask, IOPV, premium/discount, and provider-consensus fields. Applying all live execution gates to the only visible ranking makes a data-quality failure look like a small research universe.

An existing pure contract, `daily_reconstructable_v1`, calculates `research_score` from exactly the latest 61 point-in-time total-return-adjusted OHLCV bars. Sixty-one bars are mathematically necessary for a 60-session return; they are not sufficient evidence of robustness. `final_score_v3` must retain its current identity while downstream action eligibility becomes explicit.

The deployment target is a 2-core/4-GB server. Full-universe processing must therefore use bounded database reads, one worker, resumable progress, and no unbounded historical synchronization.

This is the first of three ordered changes:

1. separate research and actionable ranking surfaces;
2. validate incremental factor alpha without changing production weights;
3. build point-in-time catalyst shadow evidence without adding a catalyst score.

## Goals / Non-Goals

**Goals:**

- Restore broad daily research coverage without weakening action-data gates.
- Give every ranking row an unambiguous surface, score field, version, hash, as-of time, eligibility state, and limitation reason.
- Reuse the frozen `daily_reconstructable_v1` score for research and wrap `final_score_v3` in a new actionable eligibility contract rather than changing its meaning silently.
- Make history depth a visible confidence dimension: 61-119 provisional, 120-249 standard, and at least 250 full-history context.
- Ensure portfolio allocation and rank-derived candidate emails fail closed on actionable eligibility.
- Keep page loads cache-backed and full-universe generation safe on the production server.

**Non-Goals:**

- Changing factor formulas, weights, caps, ranking labels, tracked-position exit thresholds, or SMTP delivery rules.
- Treating 61 sessions as a training period or as proof that a score predicts returns.
- Adding 120/250-day factors before the next change establishes out-of-sample incremental evidence.
- Letting catalyst, AI, validation outcomes, positions, alerts, or notifications influence either score.
- Backfilling missing intraday fields with raw close prices, estimates, stale values, or other providers' incompatible fields.

## Decisions

### 1. Use two immutable ranking identities

The research surface SHALL use the existing `daily_reconstructable_v1` contract and `research_score`. The actionable surface SHALL use a new `actionable_rank_v1` eligibility wrapper whose underlying score is `final_score_v3.ranking_score`.

The wrapper records both its own contract hash and the referenced `final_score_v3` hash. This preserves the meaning of existing snapshots and lets rollback restore the current consumer path without rewriting old evidence.

Alternative considered: rename `final_score_v3` to the actionable rank. Rejected because existing evidence and cached rows already bind that identity to a specific score contract rather than the additional downstream eligibility policy.

### 2. Keep factor calculation windows separate from history confidence

`daily_reconstructable_v1` continues to receive exactly the latest 61 eligible bars and calculate 5/10/20/60-session trend, 20-session volatility and ATR, 60-session drawdown, and relative 20-versus-prior-40 volume.

The generator separately counts all decision-eligible adjusted sessions available as of the ranking date and assigns:

- `provisional_short_history` for 61-119 sessions;
- `standard_history` for 120-249 sessions;
- `full_history_context` for at least 250 sessions.

Research ranking requires 61 sessions. Actionable ranking requires at least 120 sessions. The 250-session tier is explanatory in this change and does not add a score bonus.

Alternative considered: require 250 sessions for every rank. Rejected because it would unnecessarily remove newer but analyzable ETFs from research coverage. Alternative considered: let 61-session rows trigger action. Rejected because one quarter of history is only a computational warm-up.

### 3. Treat daily and intraday eligibility independently

Research eligibility requires point-in-time total-return-adjusted OHLCV provenance, 61 valid sessions, finite features, and a matching as-of date. It forbids intraday, theme, catalyst, validation, position, alert, and notification inputs.

Actionable eligibility additionally requires:

- at least 120 eligible adjusted sessions;
- an available finite `final_score_v3` with all mandatory components;
- fresh, same-session execution and market-structure fields required by the product policy;
- explicit provider health and consensus;
- no cap violation, non-finite rejection, stale source, or unclassified missing mandatory field.

`not_applicable` is accepted only when the versioned product-field policy declares the field inapplicable. It cannot be used as a synonym for missing.

Alternative considered: neutral-score missing intraday components. Rejected because neutral substitution turns absence of evidence into positive action evidence.

### 4. Persist one run with two ordered surfaces

A generation workflow reads a shared point-in-time daily universe, calculates research rows for all eligible ETFs, then derives actionable rows from the eligible subset. Each surface has its own ordered rank sequence and coverage summary; an ETF's research rank is never reused as its actionable rank.

The API adds a surface discriminator and surface-specific fields while retaining existing `final_score_v3` fields during migration. Cached runs remain the page-read source.

### 5. Isolate downstream consumers

The broad research surface feeds discovery, charts, explanations, and the default ETF list. Portfolio allocation and any workflow whose reason for sending email is ranking membership consume only `actionable_rank_v1`.

Tracked-position hard stop, trailing take-profit, and trend-weakening alerts remain independent of rank membership. They continue to require their own fresh decision-eligible quote and lifecycle evidence.

### 6. Use bounded full-universe execution

The generator processes at most 20 ETFs per batch with one worker, batched price-history reads, deterministic ordering, and a resumable cursor. Each command or external-provider call has a hard timeout of at most 55 seconds. Page reads never recalculate the universe.

The workflow records batch duration, peak row count, success, exclusion, and cursor state. Retrying a batch is idempotent and does not start a second concurrent full-universe run.

## Risks / Trade-offs

- [Research users may interpret a high provisional rank as actionable] → Display the history tier and action-ineligible reason beside the score; never include provisional rows in allocation or rank-derived email selection.
- [A 120-session action gate may reduce the actionable universe] → Report coverage separately and validate the threshold in the next change; do not weaken it through fallback data.
- [Two surfaces increase API and UI complexity] → Use explicit `ranking_surface` values and additive response fields, with a single default and one actionable filter.
- [Intraday provider outages may produce no actionable rank] → Preserve the daily research rank and show a fail-closed unavailable state with provider-health evidence.
- [Existing consumers may continue reading `final_score_v3` directly] → Add dependency tests and migrate consumers in a controlled shadow period before switching defaults.
- [Full-universe generation may exceed server limits] → Enforce batch size, one-worker lock, cursor persistence, and cache-only reads.

## Migration Plan

1. Add contract models, hashes, history tiers, and unit tests without changing consumers.
2. Generate both surfaces in shadow mode from the same daily run and compare counts, exclusions, hashes, and deterministic ordering.
3. Add API fields and UI states while keeping the existing view selectable.
4. Switch the default ETF list to the research surface.
5. Switch portfolio and rank-derived email candidate consumers to `actionable_rank_v1`.
6. Observe bounded production runs and record coverage, latency, exclusions, and provider health.
7. Roll back by restoring consumer selection to the existing cached `final_score_v3` path; retain additive fields and shadow snapshots for audit.

## Open Questions

- The exact product-policy mapping for fields that are legitimately `not_applicable` must be frozen before actionable publication.
- The 120-session action threshold is a conservative initial policy and may only change through the subsequent factor-validation change with out-of-sample evidence.
