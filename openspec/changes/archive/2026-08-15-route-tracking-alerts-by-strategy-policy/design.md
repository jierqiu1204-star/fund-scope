## Context

Tracked positions currently persist one generic `exit_state_json` and are evaluated by a shared ETF/fund service. Late-day-turnaround and leader-tactics are research-only dual-universe screens whose read paths and materializers are deliberately isolated from holdings, SMTP, ranking, and execution. This change keeps those boundaries while adding explicit A-share tracking only for the leader exit policy and reading its prices from authoritative adjusted A-share facts.

## Goals / Non-Goals

**Goals:**

- Persist one supported, versioned alert policy per position and preserve the current policy as the default.
- Add a causal T+1 ETF exit state machine matching the short-horizon late-day entry thesis.
- Add a daily-close leader exit state machine for explicitly tracked ETF and A-share holdings without candidate or entry emails.
- Keep manual policy selection truthful while optionally attaching validated candidate evidence.
- Reuse current quote, adjusted-history, audit, cooldown, email, and ownership infrastructure.

**Non-Goals:**

- No broker orders, automatic tracking, ranking-weight changes, provider calls from read APIs, candidate-summary email, entry email, or policy parameter search.
- No claim that a manual policy choice was a model-generated signal.
- No attempt to identify the unknowable future morning high in live decisions.

## Decisions

### Persist policy identity separately from mutable exit state

`tracked_positions` gains a non-null policy id and rule version plus nullable source-strategy, source-manifest, source-cutoff, and provenance-kind fields. Existing rows and omitted requests receive the current dynamic policy. Mutable high-water and lifecycle values remain in `exit_state_json`, namespaced by policy version, so a policy change can reset incompatible state without losing immutable entry/provenance fields.

Storing everything only in JSON was rejected because filtering, migration defaults, API contracts, and audit provenance would become fragile. A separate policy table was rejected because there are only two frozen policies and no user-authored rules in scope.

### Treat candidate provenance as optional but verifiable

The user may manually select the late-day policy for an ETF; this is recorded as `manual_selection`. If a source manifest is supplied, the backend verifies the ETF, universe, completion state, and decision cutoff against persisted late-day evidence before recording `candidate_backed`. This keeps the UI useful without inventing signal provenance.

### Freeze one bounded T+1 state machine

The late-day policy uses the exchange calendar to identify the first eligible trading session after entry. Same-session evaluations are hold-only. On T+1, fresh executable quotes update a causal morning high and evaluate one full-exit reason in this order:

1. volatility-adaptive hard stop;
2. activated morning-high giveback;
3. latest closed 10-minute price crossing below closed-bar MA5;
4. mandatory exit at or after 10:30.

The evaluator reuses the current dynamic ETF volatility thresholds. Morning-high protection activates only after a positive move large enough to clear the volatility floor; the giveback allowance is bounded, deterministic, and included in audit context. The current fixed 10:00 exit remains a research comparator, not a live email trigger.

A direct use of the realized full-session morning high was rejected because it leaks future data. An unbounded “find a high” rule was rejected because it is neither executable nor testable.

### Separate signal execution evidence from tracked-position creation

The research execution contract records the first eligible quote after the decision, bounded by two minutes and 14:55. The live tracker does not create or assume that buy; the user still supplies the actual entry price/date. Candidate-backed provenance only links evidence and never overwrites the user's execution facts.

### Reuse one alert pipeline with policy-specific decision context

The policy router returns the same normalized primary-signal shape consumed by cooldown, audit, owner-recipient, and SMTP code. Late-day full-exit reasons use one policy-specific alert family plus reason codes in structured context, preventing a large parallel notification subsystem. Stale or display-only quotes remain web-only.

### Freeze one adjusted leader risk contract

`leader_tactics_exit_v1` evaluates only bounded, decision-eligible `total_return_adjusted` daily bars. The first eligible adjusted close after policy entry becomes the immutable adjusted entry reference. Entry ATR20 freezes a two-ATR disaster stop; compatible candidate provenance may tighten it to a valid lower signal-session low. One risk unit is the distance from entry reference to that stop. After the adjusted closing high reaches one risk unit, a round-trip-cost breakeven floor arms permanently. Each completed session exits at the highest of the immutable stop, armed breakeven floor, and same-session adjusted MA5.

This policy deliberately has no fixed-percentage take profit, profit-giveback trigger, candidate email, or automatic position creation. Missing, stale, forbidden-provider, mixed-adjustment, or non-finite evidence fails closed. A-share rows use the authoritative adjusted-fact table; retired raw stock-price history is never restored as a fallback.

## Risks / Trade-offs

- [Sparse intraday quotes make the morning high or MA5 incomplete] → require fresh persisted observations, expose coverage/unavailable reason, and fall back only to the mandatory exit when a fresh quote exists.
- [Manual users choose a mismatched policy] → label manual provenance clearly and show the policy's next-session lifecycle before confirmation.
- [Policy change reuses incompatible high-water state] → reset policy-scoped mutable state and audit the transition.
- [Scheduler misses 10:30 exactly] → the first later evaluation with a fresh quote still produces the overdue mandatory exit; no historical quote is substituted.
- [Migration changes existing behavior] → database and schema defaults select the current dynamic policy, with a downgrade that drops only the added columns.
- [Adjusted entry reference differs from an intraday fill] → label it as the policy reference, retain the user's execution facts separately, and never present adjusted-policy return as confirmed account P&L.

## Migration Plan

1. Add nullable provenance fields and non-null policy fields with server defaults; backfill existing rows to the current dynamic policy.
2. Deploy API compatibility first: old clients may omit all new fields.
3. Deploy the policy router and late-day evaluator while the default remains unchanged.
4. Deploy the frontend selector and evidence labels.
5. Roll back by disabling late-day selection, migrating any affected active position to the default policy with an audit record, then dropping the added columns if required.
