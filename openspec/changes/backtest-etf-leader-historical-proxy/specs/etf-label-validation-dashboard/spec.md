## ADDED Requirements

### Requirement: Leader panel displays historical proxy outcomes separately
The strategy-evidence page SHALL display the latest complete rolling historical-proxy backtest beneath the historical proxy screen and separate it visually from factual PIT observations, formal ranking, and live trade state.

#### Scenario: Complete rolling backtest exists
- **WHEN** compatible historical backtest evidence is available
- **THEN** the panel shows signal-date range, source and eligible asset counts, match and zero-match counts, per-candidate multi-horizon net metrics, costs, exclusions, confidence intervals, and current-vintage bias warnings

#### Scenario: Evidence is missing or insufficient
- **WHEN** the rolling backtest is absent, partial, has no match, or lacks a complete horizon
- **THEN** the panel shows the exact unavailable reason and MUST NOT display zero as a return or call the screen validated
