## Why

ETF formal-ranking validation can currently return `N/A` even when calculations exist because the validation run does not persist the complete multi-date ranking-source manifest, the default calendar window cannot satisfy its own non-overlapping sample gate, and the 1,400-plus ETF universe can be current-day fresh while still lacking the adjusted-history depth required for score eligibility. The repair must make these states observable and resumable on the 2-core/4-GB server without fabricating historical production evidence or merging research replay with live evidence.

## What Changes

- Persist and validate `production_published` versus `research_replay` provenance through a canonical multi-date source manifest on every validation; backfill an existing aggregate only when every immutable source event proves one coherent source kind and contract.
- Derive validation lookback requirements from the exchange calendar, actual compatible source dates, horizon, non-overlap rule, minimum independent dates, and future-window completion instead of treating 180 calendar days as universally sufficient.
- Split same-day adjusted-price freshness from 61-session score warm-up and contract-derived replay depth, with independent cursors, coverage denominators, exclusions, and publication/readiness states; retain 180 sessions only as optional operational telemetry.
- Replace unbounded all-ETF historical synchronization with a one-worker, checkpointed code/date continuation capped at 55 seconds and a frozen 2-core/4-GB resource profile, bulk existing-key reads/upserts, and small commits.
- Add a read-only readiness audit backed by a server-provisioned instance identity and production attestation that reports production snapshot/source provenance, adjusted-price depth, provider/job health, validation availability, and exact `N/A` reasons without exposing credentials.
- Keep exact historical `final_score_v3` unavailable when point-in-time quote or component evidence was never captured; route reconstructable history only through the separately identified `daily_reconstructable_v1` research replay.
- Restore a green integration baseline for the already-checked-in WIP by requiring the replay owner change to resolve its missing migration/model and walk-forward module, while this change directly resolves the intraday eligibility regression before rollout work resumes.

## Capabilities

### New Capabilities

- `etf-ranking-readiness-audit`: Defines a read-only, environment-attested readiness report for formal ranking publication, historical depth, validation provenance, and stable unavailable reasons.

### Modified Capabilities

- `short-etf-research`: Separates current-session freshness from historical warm-up completeness and requires bounded resumable historical synchronization.
- `short-etf-research-reliability`: Reports independent depth/coverage, checkpoint, resource, provider, and failure states for the large ETF universe.
- `etf-signal-validation`: Requires registered ranking-source provenance and horizon-aware exchange-session sample planning before evidence can be sufficient.
- `etf-research-evidence-contract`: Prevents production-published and research-replay evidence from merging and permits factual provenance repair only from immutable linked sources.
- `etf-point-in-time-ranking-replay`: Freezes the factual point-in-time membership contract required by the separately owned research replay path.

## Impact

- Backend ranking validation writers/readers, evidence classification, score-bucket validation, exchange-calendar planning, and readiness APIs.
- ETF adjusted-history jobs, cursors, health records, bulk persistence, scheduler/admin orchestration, and resource metrics.
- Additive migrations for any missing replay/membership/checkpoint fields; legacy rows remain nullable and untrusted unless factually attributable.
- `/short-term` and admin evidence/readiness presentation gain additive reasons and progress fields; live ranking, allocation, tracking, alerts, notifications, and SMTP behavior remain unchanged.
- Existing `harden-etf-comprehensive-ranking` rollout and `enable-etf-point-in-time-ranking-replay` research work are dependencies: this change closes their readiness gaps without relabeling replay output as production evidence.

## Change Ownership

- `repair-etf-ranking-evidence-readiness` owns the multi-date validation source manifest, factual provenance repair, exchange-session/source-date planner, separated sync lanes, bounded history continuation, readiness attestation, and the intraday regression needed to restore its integration baseline.
- `enable-etf-point-in-time-ranking-replay` remains the only owner of point-in-time membership persistence, replay scoring/materialization, the research source adapter, paired endpoint, walk-forward, purge/embargo, and holdout tasks. This change adds acceptance contracts and dependency checks but MUST NOT duplicate those implementations.
- `harden-etf-comprehensive-ranking` remains the only owner of live v3 publication behavior and real-environment tasks 11.2 and 11.9. Three qualified sessions close only that rollout gate; they do not satisfy the 20-date formal return-evidence gate.
