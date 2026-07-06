## Why

Current ETF exit evidence shows hard stop, trend weakening, and exit-watch signals are weak when evaluated as short-term price prediction signals. This change upgrades the exit workflow from static sell-like labels into a mature risk system that separates insurance stops, position-state trailing protection, market protection guards, and research validation.

## What Changes

- Reframe ETF hard stop as loss insurance and tail-risk control, not a directional prediction signal.
- Convert ETF trailing take-profit into a tracked-position state machine using entry price, high-water profit, activation threshold, giveback, and data eligibility.
- Downgrade trend weakening from a direct sell signal into a reduce-risk or no-add guard unless confirmed by position loss, drawdown, or broader market deterioration.
- Add protection guards such as cooldown, repeated stop-loss guard, portfolio drawdown guard, low-profit ETF guard, and market-regime guard.
- Add a research-only ETF exit risk validation capability that compares hold baseline, current default rules, candidate policies, and protection-guard policies on real tracked-position paths.
- Keep candidate exit parameters as research evidence until explicitly approved; no automatic change to live emails, ranking, or tracked-position behavior.
- Update UI and API wording so low-confidence or guard-only signals are not presented as reliable sell instructions.

## Capabilities

### New Capabilities
- `etf-exit-risk-validation`: Research evidence for ETF exit policies, protection guards, path-based validation, confidence levels, and candidate parameter recommendations.

### Modified Capabilities
- `tracked-position-exit-strategy`: Change ETF exit requirements from static threshold alerts to position-state risk handling with insurance stops, trailing state, guard-only trend weakening, and explicit approval gating.
- `etf-research-evidence-contract`: Extend the evidence contract to record exit-policy validation versions, protection-guard evidence, baseline comparisons, and whether evidence is research-only or approved for live tracked-position use.
- `short-term-research`: Update workbench display requirements so ETF holding actions, guard states, and low-confidence exit evidence are clearly separated from observation rankings.

## Impact

- Backend services: `app.services.tracked_positions`, `app.services.risk_alerts`, `app.services.short_research.etf_exit_credibility`, `app.services.short_research.etf_exit_hyperopt`, `app.services.etf_research_evidence`, schedulers, admin jobs, and workflow orchestration.
- API schemas and payloads: tracked-position snapshots, exit alert context, exit credibility latest/run endpoints, exit hyperopt latest/run endpoints, and research evidence summaries.
- Frontend: `/short-term` holding cards, selected ETF detail panel, research evidence page, and admin task controls.
- Data: use existing JSON fields where possible for research metadata; add migration only if live position-state persistence cannot be safely stored in existing tracked-position fields.
- Dependencies: no broker integration and no automatic trading; external AI is not required for deterministic risk decisions.
