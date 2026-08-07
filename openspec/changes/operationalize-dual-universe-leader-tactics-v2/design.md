## Context

See `proposal.md` for motivation. The repository already contains ETF PIT history, ranking research replay, factor diagnostics, lifecycle policy-shadow evidence, and the active `validate-etf-leader-tactics-shadow` V1 experiment. V1 is deliberately ETF-only and freezes three proxies, while the requested V2 must add A-share research data, dual-universe screening, candidate lifecycle state, and a user-facing filtered list without changing V1 evidence or production rankings.

The source corpus includes article text and images captured outside the repository. Some rules are explicit, including hot theme, core leader, moving-average alignment, six-month peak volume, post-signal strengthening, and an MA5 lifecycle. The proprietary `起飞信号` formula is not disclosed. The implementation therefore has to preserve a source-to-proxy boundary and cannot optimize until an image looks similar.

The production server is resource constrained. Provider work must remain single-process, resumable, memory bounded, and short enough for the existing scheduler and deployment model. A-share theme membership also creates a new PIT risk because current constituent lists cannot safely describe past sessions.

## Goals / Non-Goals

**Goals:**

- Reuse the existing PIT/replay and evidence architecture for A-share and ETF leader research.
- Produce deterministic, versioned candidate lists and causal lifecycle transitions.
- Make formula facts, data readiness, exclusions, economic evidence, and provenance inspectable from the existing evidence workbench.
- Keep source-label validation and cost-adjusted economic validation separate.
- Make bounded collection and replay safe on a two-core, four-gigabyte server.

**Non-Goals:**

- Reconstruct or claim the author's undisclosed proprietary formula.
- Modify comprehensive ranking weights, intraday ranking, real positions, notifications, or execution.
- Perform machine learning, parameter grids, post-hoc threshold tuning, or repeated holdout access.
- Backfill factual PIT receipt times or historical theme constituents from current data.
- Create a second general-purpose backtest engine.

## Decisions

### 1. Build V2 beside the immutable V1 registry

V2 receives a new source registry, formula registry, manifest identity, storage namespace, and API version. Shared utilities may be reused, but V1 formulas, candidate hashes, outcomes, and holdout identity remain unchanged. Implementation begins only after the active V1 change is completed or archived.

This is preferable to extending the V1 registry in place because changing its universe and formulas would invalidate prior sample-out evidence. A clean V2 identity also makes rollback simply a matter of disabling the V2 read and scheduler paths.

### 2. Add an A-share PIT adapter to the existing research data plane

The adapter persists logical facts for authoritative-universe snapshots, adjusted daily bars, PIT theme memberships, provider receipts, and exclusions. Every decision query applies both `effective_time <= cutoff` and `receipt_time <= cutoff`. Imported history without a factual receipt time remains historical-research-only and cannot count toward forward-capture gates.

The alternative—using today's A-share and theme membership for old dates—would create survivorship and taxonomy look-ahead. It is rejected even if it increases apparent sample size.

### 3. Use one pure formula engine with universe-specific peer adapters

The formula engine consumes a normalized session frame and produces gate facts, finite scores, and exclusions without database or network access. An A-share adapter supplies PIT theme peers. An ETF adapter supplies PIT theme/tracked-index peers and removes clones before breadth and ranking. The normalized contract allows parity tests across adapters while preserving different peer semantics.

The three candidates are registry data, not runtime configuration. Common takeoff gates apply to breakout and base-launch. Former-leader repair remains a V1-derived comparator and is explicitly labeled as such because forcing it through breakout-style trend and volume gates would change its hypothesis.

### 4. Model lifecycle as append-only state transitions

Candidate observations are immutable events. A deterministic reducer derives `preparing`, `confirmed`, and `invalidated` from observations available at each cutoff. Signal-day high and formula identity are captured at creation and cannot drift after provider revisions. Replays store revision-aware audit results without overwriting original PIT facts.

This is preferable to storing only the latest state, which would make confirmation dates and historical UI views impossible to reproduce.

### 5. Separate screening evidence from economic validation

The screen answers “which rows satisfy the frozen transparent formula at this cutoff?” The validation pipeline answers “did the candidate add cost-adjusted out-of-sample value?” Source-label precision/recall and economic endpoints remain separate because matching article examples is not the same as earning excess return.

The existing replay, forward-outcome, cost, bootstrap, and factor-diagnostic components are reused. A-share outcomes use same-theme equal weight on common support; ETF outcomes retain the frozen comprehensive Top10 comparison. Auxiliary horizons cannot choose the winner.

### 6. Treat the August 3 article as a single locked case

The source registry stores the two disclosed core identifiers, publication/cutoff facts, and article hash. The evaluator runs the ordinary A-share formula and checks whether those identifiers naturally appear. It does not pass target identifiers to the formula engine. Opening the case records an irreversible V2 holdout-use event.

This prevents the common failure mode of adjusting thresholds until a known screenshot is reproduced.

### 7. Use compact source provenance instead of copying the corpus

Repository artifacts contain source URL or identity, publication time, capture hash, concise paraphrased assertions, and mapping to proxy gates. Full article content remains outside the project. This is sufficient for reproducibility and reduces copyright, repository-size, and secret-leak risk.

### 8. Keep provider work serial and adapt batch size from observed cost

One leased worker processes deterministic pages. It starts between 5 and 20 assets per page, reduces size after timeout or memory pressure, and increases only after multiple safe pages. Each continuation stops before 55 seconds, commits complete assets transactionally, persists a cursor, and never launches another continuation itself.

Formula calculation, lifecycle reduction, and API reads operate only on persisted data and do not trigger providers. This isolates slow external calls from the user-facing path and avoids overlapping sync storms.

For the production A-share research path, TickFlow's free API supplies the current `CN_Equity_A` universe, instrument metadata, and explicit backward-adjusted daily bars. BaoStock supplies only a current industry snapshot in a physically terminable child process. Both are stamped from their factual receipt time and cannot be backdated. The A-share research materialization threshold is 90 percent, reflecting the separately measured current-industry coverage; this exception does not change any ETF publication or comprehensive-ranking threshold.

### 9. Extend the existing evidence workbench through a bounded read API

The V2 endpoint uses enumerated universe/formula/state filters, an explicit `as_of`, stable cursor pagination, deterministic sorting, and capped page size. A summary envelope returns per-layer coverage, candidate/exclusion counts, availability reasons, registry identity, and manifest hash. Candidate details return gate facts and transition history.

The front end defaults to ETF for compatibility, stores filters in URL state where practical, cancels stale requests, and never falls back across universes. Production ranking and V2 research remain visually separate.

### 10. Bootstrap current A-share classification in two bounded layers

TickFlow SW1 universe batches provide the primary current classification. A
single BaoStock child process supplements at most 20 still-unclassified symbols
per page under a durable database checkpoint. Classification pages are
persisted from their factual receipt time and never backdated. Screening remains
unavailable until the A-share-only 90-percent theme gate passes; the ETF ranking
pool, thresholds, tables, and publication path are not inputs to this bootstrap.

## Risks / Trade-offs

- [Historical A-share theme membership is sparse] → Exclude unknown PIT memberships, expose coverage honestly, and accumulate factual forward snapshots; do not infer from today's theme pool.
- [A 120-session peak-volume gate makes new listings and short-history assets unavailable] → Report history-tier exclusions and retain the frozen gate; evaluate a different window only in a future version.
- [Batch breadth may be too strict for small ETF peer groups] → Apply clone removal before the declared denominator and expose peer counts; do not silently weaken the 3-and-20-percent rule.
- [Provider latency can delay full A-share coverage] → Use adaptive serial batches, durable cursors, provider cooldowns, and no provider calls on read paths.
- [Article examples create anchoring pressure] → Lock date splits and candidate formulas before the August 3 case; persist mismatches without tuning.
- [Classification evidence may look good while returns are poor] → Present label fidelity and economic alpha in separate sections and require the economic primary for promotion.
- [Shared utilities could accidentally mutate V1 evidence] → Add registry-hash, storage-namespace, and parity tests; block writes when manifest versions are incompatible.
- [Users may interpret `confirmed` as a buy instruction] → Label it “透明代理已确认（研究）”, keep notification/execution provenance unavailable, and retain an explicit non-advice notice.

## Migration Plan

1. Complete or archive `validate-etf-leader-tactics-shadow` and snapshot its V1 registry and evidence hashes.
2. Add additive A-share PIT and V2 research storage with no production-table migration dependency; deploy with V2 scheduler and routes disabled.
3. Backfill only historical-research data with honest receipt provenance, then start factual forward universe/theme capture. Verify bounded continuation, restart idempotency, resource limits, and raw-price isolation.
4. Register the frozen source/formula/state contracts and run deterministic parity tests without future outcomes.
5. Enable research-only V2 screening and lifecycle capture, then materialize historical replay and validation evidence under separate manifests.
6. Enable the read-only API and dual-universe workbench behind a feature flag after coverage and contract tests pass.
7. Keep all production ranking, notification, and execution paths unchanged. Any later promotion requires a separate OpenSpec change and manual approval.

Rollback disables the V2 scheduler, API route, and UI flag while retaining append-only research facts for audit. Because storage and routes are additive and production state is never mutated, rollback does not require restoring rankings or positions.
