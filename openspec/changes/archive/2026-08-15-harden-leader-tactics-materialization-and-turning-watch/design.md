## Context

See `proposal.md` for motivation. The current A-share materializer pages database reads but accumulates every `V2AssetInput` and roughly one million Python bar objects before screening. It therefore needs substantial free memory even though ingestion itself is bounded. The current membership provider exposes only 126 broad SW1 groups, and the formula loop applies the breakout 120-session peak-volume gate before branching to breakout versus base launch. ETF V2 already has a separate job and storage boundary, but its production flag is disabled.

The server is limited to 2 cores and 4 GB. Existing production safety rules require one worker, no raw-price fallback, deterministic immutable manifests, and invocation budgets no greater than 55 seconds.

## Goals / Non-Goals

**Goals:**

- Bound peak application memory independently of A-share universe size and resume work after interruption.
- Preserve one deterministic cross-section while splitting feature extraction from ranking/finalization.
- Resolve cutoff-visible fine themes before broad industries and make every fallback observable.
- Correct the base-launch volume behavior under a new frozen registry identity.
- Surface early strengthening without converting it into a candidate, trade, ranking input, or notification.
- Enable ETF V2 through its existing isolated research path and prove comprehensive-ranking non-interference.

**Non-Goals:**

- Reproduce the source author's unpublished proprietary formula.
- Optimize thresholds against recent outcomes or promote a shadow result to production trading.
- Change ETF comprehensive-ranking weights, eligibility, publication thresholds, snapshots, or APIs.
- Infer historical fine-theme membership when receipt-time evidence does not exist.

## Decisions

### 1. Persist compact terminal feature facts before cross-sectional finalization

Add an append-only compact feature table keyed by run identity and asset. Stage A reads deterministic pages of at most 20 assets, derives the global cross-sectional scalars and source-input digest, persists one terminal compact fact per asset, and releases page histories before advancing. Stage B starts only when terminal feature count equals the frozen expected asset count. It computes market-wide theme percentiles from compact facts, then reloads and finalizes one PIT peer group at a time so formulas and prior-leadership semantics remain byte-compatible with the pure engine without retaining full-market histories.

Input hashes continue to derive from immutable source fact hashes. Existing pure `screen_dual_universe` remains available for deterministic replay and staged-output parity tests. The production coordinator limits peak application memory to compact market facts plus the largest currently processed peer group.

Alternative considered: lower the 768 MiB guard or add `slots` to bar objects. Both reduce symptoms but still make memory proportional to the full history and do not provide a resumable finalization boundary.

### 2. Use one run identity with two explicit stage checkpoints

The run identity freezes universe snapshot, signal date, cutoff, source/formula registry, expected asset codes, taxonomy policy, and code version. Feature pages use deterministic code ordering and `ON CONFLICT DO NOTHING`; finalization uses a short transaction and a unique manifest hash. A compatible incomplete run resumes. Incompatible code, taxonomy, or formula identity starts a new run and never merges facts.

Alternative considered: create one manifest per page and merge manifests. That would make cross-sectional percentiles dependent on page boundaries and violate cohort atomicity.

### 3. Introduce a hierarchical PIT theme resolver

Theme facts gain a hierarchy level and normalized taxonomy key. The resolver chooses, in order: compatible cutoff-visible fine theme; compatible cutoff-visible tracked concept/index; broad SW1 industry fallback. The source fact hash and `resolution_mode` are copied into compact features and observations. Initial fine-theme ingestion is limited to registered, auditable provider facts; no name-based inference is permitted in decision mode.

For稀土, the provider taxonomy aliases `稀土`, `稀土永磁`, and their registered concept identifiers into one versioned peer-group family while preserving the original label. Historical sessions lacking factual receipt time continue to use the broad fallback or fail closed.

Alternative considered: hard-code a list of rare-earth stock codes. It would create survivorship bias and non-PIT membership, so it is rejected for decision evidence.

### 4. Version the formula registry and separate candidate-specific volume gates

The breakout proxy retains the source-disclosed 120-session peak-volume gate. The base-launch proxy uses a disjunctive confirmation frozen in the new registry: current volume at least 1.20 times prior-20-session mean volume, or current amount at/above its own empirical 70th percentile against the prior 20 eligible sessions. This makes the confirmation independent from the peer-liquidity component already used by the core score and avoids demanding an exceptional 120-day spike during a base transition.

All thresholds are frozen under the new registry hash; old V2 evidence remains queryable and is never rewritten. The changed formula is evaluated in shadow and cannot affect formal ranking.

### 5. Derive `turning_watch` after feature finalization, not in lifecycle state

`turning_watch` is an observation classification for non-qualifiers, not an active lifecycle transition. It requires valid PIT inputs, resolved peers, close above MA20, non-negative MA20 slope, and at least three of four gate families. Distances are normalized to their registered thresholds and persisted. Only formal qualifying observations may create `preparing`, `confirmed`, or `invalidated` transitions.

Alternative considered: relax candidate thresholds. That would silently change candidate meaning and encourage overfitting; a separate non-actionable state is safer and more informative.

### 6. Enable ETF V2 only through the existing isolated job

Set the dedicated ETF materialization flag in deployment configuration and keep its job, checkpoint, manifest, coverage, and API query separate. Acceptance tests snapshot protected comprehensive-ranking identities and assert no write path is reached. ETF readiness failures remain local to the ETF V2 panel.

## Risks / Trade-offs

- **[Fine-theme provider facts may have limited historical depth]** → expose broad fallback and receipt-time coverage; never backfill eligibility from current membership.
- **[Compact feature formulas can drift from the pure engine]** → share scalar feature and finalization helpers and add chunk-invariance/property tests comparing both paths on fixed fixtures.
- **[Two-stage rows increase database size]** → persist only compact terminal facts, index run/asset and run/group identities, and leave retention to a separate explicit change rather than introducing an unreviewed deletion path here.
- **[Turning-watch rows may be mistaken for recommendations]** → omit actionable score, use explicit research wording, and prohibit lifecycle/notification consumers by contract tests.
- **[ETF materialization adds scheduled load]** → run once per completed session, skip compatible manifests, honor the same resource gate, and never overlap A-share finalization.
- **[Relative-volume threshold may not improve returns]** → retain research-only status and require frozen historical/PIT validation before any future promotion proposal.

## Migration Plan

1. Apply the additive feature-fact and theme-hierarchy migration; leave the new formula registry and ETF flag disabled.
2. Deploy staged A-share extraction in shadow, compare compact and pure-engine outputs on fixtures, and verify bounded memory/checkpoint resume.
3. Begin capturing fine-theme facts with explicit receipt times; existing SW1 facts remain valid fallbacks.
4. Enable the new registry for new research manifests only, expose `turning_watch`, and preserve old manifests unchanged.
5. Enable ETF V2 materialization independently after readiness and protected-state hash checks pass.
6. Roll back A-share materialization with `ETF_LEADER_TACTICS_V2_MATERIALIZE_ENABLED=false`, roll back ETF materialization independently with `ETF_LEADER_TACTICS_V2_ETF_MATERIALIZE_ENABLED=false`, and disable only the read surface with `ETF_LEADER_TACTICS_V2_API_ENABLED=false`; additive tables and prior immutable evidence remain intact.
