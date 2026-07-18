## Why

Current ETF ranking and email-performance reports return `N/A` because the database has no compatible published historical `final_score_v3` snapshots and no completed live email/action lifecycle samples. Relaxing publication filters or reconstructing old v3 scores from today's data would introduce look-ahead and survivorship bias, so the project needs an explicitly research-only point-in-time replay path that cannot be mistaken for production evidence.

## What Changes

- Add a bounded, checkpointed ETF ranking replay that freezes each replay date's available universe, adjusted decision data, feature cutoff, score contract, and input hashes before calculating a cross-sectional ranking.
- Persist replay output as `research_replay`, never as historical `production_published`; keep legacy, incomplete, current-universe-only, or unreconstructable dates explicitly excluded with stable reasons.
- Define a daily-reconstructable ranking contract for research dates where exact production v3 intraday premium, spread, IOPV, provider-consensus, or catalyst evidence is unavailable. Its results remain visibly different from exact `final_score_v3` evidence.
- Feed eligible replay cohorts into existing Top-N validation and alert/action policy comparison without writing live ranking, allocation, position, alert, notification, or SMTP records.
- Keep `policy_shadow` outcomes separate from observed production notification/action evidence so simulated historical accuracy and returns never fill missing SMTP, verified-delivery, or user-confirmed execution evidence.
- Freeze three candidate families before outcome inspection: current research baseline, turnover-controlled Top-k dropout/rank hysteresis, and the same turnover control plus predeclared regime/liquidity execution gates.
- Keep Top10 five-trading-day paired net excess return as the ranking primary endpoint and Top20 ten-trading-day tax/fee-adjusted action-cycle benefit as the alert-policy primary endpoint; label all other Top-N/horizon cells exploratory.
- Use chronological walk-forward windows, a ten-trading-day purge/embargo, one-time final holdout consumption, point-in-time universes, explicit costs, turnover, drawdown, rank churn, bootstrap uncertainty, and sample-sufficiency gates.
- Run replay in sequential date/code batches with bounded memory, persisted checkpoints, idempotent resume, and commands capped at 55 seconds for the 2-core/4-GB deployment target.

## Capabilities

### New Capabilities

- `etf-point-in-time-ranking-replay`: Defines research-only replay identity, point-in-time input eligibility, daily-reconstructable score contracts, bounded/resumable execution, frozen candidate evaluation, and evidence separation from production publication.

### Modified Capabilities

- `etf-signal-validation`: Distinguishes exact published-snapshot validation from research-replay validation and reports provenance, limitations, primary endpoints, policy-shadow outcomes, and exclusions without filling live evidence gaps.
- `etf-research-evidence-contract`: Adds orthogonal ranking-source kind, signal/action contract compatibility, policy mode, notification provenance, execution provenance, replay contract/input/universe hashes, availability cutoff, candidate registry, and immutable holdout identity so research replay, policy shadow, SMTP facts, and user-confirmed execution cannot be merged.
- `etf-strategy-comparison-backtests`: Adds a frozen, low-cardinality ranking-candidate stage before action-policy comparison and keeps the ranking and action primary endpoints independent.
- `etf-portfolio-backtest`: Requires policy-shadow portfolio outcomes to consume immutable replay cohorts and simulated execution only, without creating production actions or notifications.

## Impact

- Backend strategy-lab/validation services gain a point-in-time replay coordinator, frozen replay contract, checkpointed batches, and research evidence persistence; production short-research scoring remains the owner of live ranking.
- Existing `harden-etf-alert-action-lifecycle` candidate, walk-forward, cost, action-cycle, and holdout logic is reused rather than duplicated; this change supplies provenance-safe ranking cohorts to it.
- Validation APIs and workbench evidence output gain additive provenance and limitation fields. Existing production endpoint paths and live notification behavior do not change.
- Historical dates without reconstructable universe, adjusted-price provenance, or required daily inputs remain excluded. Raw Sina/efinance prices, estimated IOPV/premium, and current-universe backfills cannot become decision evidence.
- Tests cover no-look-ahead cutoffs, survivor handling, evidence-kind isolation, frozen candidate/holdout guards, cost-aware outcomes, bounded resume, deterministic batching, and no decision-domain side effects.

## Coordination

- This change exclusively owns point-in-time membership persistence, replay scoring/materialization, the research source adapter, paired ranking endpoint, walk-forward, purge/embargo, and holdout behavior.
- `repair-etf-ranking-evidence-readiness` owns the shared multi-date validation source manifest, validation/source-date planner, production adjusted-history continuation, and signed readiness projection; replay code consumes those contracts without duplicating them.
- `harden-etf-comprehensive-ranking` owns live `final_score_v3` publication and real-environment rollout tasks 11.2/11.9. Research replay never satisfies those production publication or rollout gates.
