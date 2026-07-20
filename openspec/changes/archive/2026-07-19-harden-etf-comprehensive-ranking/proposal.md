## Why

The ETF comprehensive ranking can currently mix partial and full-universe runs, stale or different-date market data, legacy scores, and mismatched validation evidence. These paths can produce misleading ranks, overstate historical support, or let display-only data influence decision-facing outputs, so the ranking needs one immutable and auditable contract before further weight calibration.

## What Changes

- Introduce an immutable ETF ranking snapshot contract covering run scope and scope hash, universe and input-snapshot hashes, score and rule versions, evidence contract hash, market-data cutoff, coverage, price basis, freshness, and idempotent publication identity.
- Score the complete eligible universe against one decision-eligible data cutoff before filtering, pagination, detail lookup, live overlays, portfolio allocation, or validation.
- Make the final score a single ordered pipeline: validated inputs and enrichment components first, one score computation second, and stale/unavailable hard caps last.
- Keep historical validation and backtest evidence research-only; remove every path that automatically rewrites live scores, labels, portfolio weights, tracked positions, alerts, or notifications.
- Require exact version/scope/hash matching for current evidence and Top-N validation; remove legacy `total_score` fallback and never synthesize a missing evidence hash.
- Harden daily and intraday market-data eligibility with same-trading-date coverage gates, total-return-aware price basis, source/reliability metadata, exchange calendar handling, and no promotion of display-only quotes into daily decision data.
- Repair sector, premium/discount, liquidity, and factor producer-consumer contracts; missing inputs remain unavailable and cannot become bullish, neutral, or silently reweighted placeholders.
- Preserve global rank before user filtering and expose filtered position separately; make static and live tie-breaking, null sorting, freshness, and rank-change semantics consistent.
- Make tracking filters and watchlist sources current-user scoped, isolate private frontend query caches across authentication changes, and correct market-session refresh, pagination, error, date, and stale-state behavior.
- Replace raw overlapping-observation confidence with point-in-time, contract-grouped validation that reports effective sample dates, coverage, excess outcomes, costs, uncertainty, and walk-forward results.
- Sequence sync, coverage validation, ranking generation, and cache publication through an auditable workflow so readers never observe a half-refreshed ranking snapshot.

## Capabilities

### New Capabilities

- `etf-ranking-snapshot`: Defines the immutable full-universe ranking snapshot, compatibility selector, publication gate, global-rank semantics, and downstream consumption contract.

### Modified Capabilities

- `short-term-research`: Changes comprehensive scoring order, cached ranking selection, filter/rank semantics, stale states, tracking filters, and workbench metadata/error behavior.
- `short-etf-research`: Changes ETF universe history, daily synchronization completeness, metric/factor input contracts, and full-universe signal generation behavior.
- `short-etf-research-reliability`: Changes data-health, prioritized refresh, freshness, adjusted-price, and incomplete-run reporting requirements.
- `market-data-reliability`: Changes daily and intraday decision eligibility, price-basis provenance, trading-date coverage, and display-only fallback rules.
- `intraday-etf-watch`: Changes daily-snapshot compatibility, live rank calculation, market calendar, time-normalized activity, user scoping, and live freshness behavior.
- `etf-research-evidence-contract`: Changes contract hash persistence and exact evidence classification, including mandatory legacy treatment for missing or mismatched hashes.
- `etf-signal-validation`: Changes score-source eligibility, point-in-time universe/data reconstruction, effective-sample reporting, and strict separation from live decisions.

## Impact

- Backend models and migrations for signal and validation snapshot metadata, point-in-time ETF eligibility, and daily price basis/provenance.
- Short-research ranking, sector/factor/theme enrichment, evidence classification, validation, portfolio consumption, and cache selection services.
- Daily and intraday market-data ingestion, scheduler/workflow orchestration, trading-session logic, and provider reliability gates.
- Existing `/api/short-research` and `/api/etf-quotes/live-rankings` paths remain compatible, with additive snapshot/rank/freshness fields and corrected filtering semantics.
- `/short-term` query construction, authentication-scoped caching, status transitions, pagination, error/empty states, and ranking explanations.
- Historical ranking and validation evidence must be recomputed under the new contract before it can be shown as current same-contract evidence.
- Relevant backend boundary, ranking, API, validation, intraday, scheduler, migration, and frontend behavior tests require regression coverage.

## Coordination

- This change exclusively owns live `final_score_v3` materialization/publication and real-environment rollout tasks 11.2/11.9.
- `repair-etf-ranking-evidence-readiness` owns the shared multi-date validation source manifest, horizon/source-date planner, bounded production adjusted-history continuation, and signed readiness projection that task 11.2/11.9 consume.
- `enable-etf-point-in-time-ranking-replay` owns research-only PIT membership, replay scoring, paired evaluation, walk-forward, purge/embargo, and holdout behavior. Replay sessions and results never replace production rollout sessions or formal production snapshot evidence.
