## Why

The deployed leader-tactics surface exposes frozen proxy definitions but cannot accumulate or display observations until 252 complete point-in-time sessions already exist. That couples evidence collection to the promotion threshold, leaving the research panel empty for roughly a trading year and preventing the system from collecting the very forward outcomes required to evaluate the hypotheses.

## What Changes

- Allow the leader-tactics shadow to materialize research observations from the first complete, decision-eligible production PIT session while keeping promotion blocked until every existing statistical gate passes.
- Compose a due-aware, single-worker observation continuation from persisted PIT capture sources, factual historical membership and peer mappings, adjusted OHLCV, and the frozen market-regime contract.
- Persist immutable per-session proxy observations, exclusions, pending and matured forward outcomes, MA5 policy-shadow state, manifests, cutoffs, hashes, and idempotent checkpoints without calling live providers.
- Expose current shadow matches and accumulation progress through the existing read-only evidence API, including exact insufficient-data reasons and promotion counts.
- Show an explicitly research-only observation list on the strategy-evidence page without changing production ranking, allocation, tracked positions, risk alerts, notifications, SMTP state, or score weights.
- Keep the continuation disabled until the shared readiness rollout has produced a complete dual-95-percent publication and production PIT scheduling is explicitly enabled.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `etf-leader-tactics-shadow`: Separate observation accumulation from promotion eligibility and define first-session, pending-outcome, and idempotent daily continuation behavior.
- `etf-point-in-time-research-loop`: Compose a due-aware leader observation lane from immutable production PIT sources without expanding provider or production side effects.
- `etf-research-evidence-contract`: Expose partial leader observations, pending outcomes, exact gate progress, and immutable observation provenance before promotion eligibility.
- `etf-label-validation-dashboard`: Render current research-only leader matches, exclusions, coverage, and accumulation progress without presenting them as buy signals or validated returns.

## Impact

- Backend: leader-tactics workflow composition, PIT input loading, observation and outcome persistence, checkpoints, scheduler preflight, evidence projection, schemas, and admin orchestration.
- Frontend: the existing ETF strategy-evidence page and TypeScript evidence contract.
- Data: additive research-only observation and checkpoint records; no rewrite of historical membership, receipt timestamps, adjusted prices, production snapshots, or user state.
- Operations: one worker, pages of at most 20 ETFs, no live provider calls, bounded memory, and a hard return below 55 seconds on the 2-core/4-GB server.
- Verification: PIT cutoff, idempotency, outcome-maturity, promotion-gate, production-isolation, API/UI, domain-boundary, Ruff, and strict OpenSpec checks.
