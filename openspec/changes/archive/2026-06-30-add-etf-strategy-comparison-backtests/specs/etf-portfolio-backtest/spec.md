## MODIFIED Requirements

### Requirement: ETF portfolio backtest uses the short-term workbench strategy
The backtest SHALL use the same deterministic strategy contract shown on `/short-term`: ETF ranking, observation label, entry timing, risk-on/defensive/cash-wait portfolio mode, single-ETF cap, user ETF capital, and position handling rules. The backtest MUST call the shared strategy contract used by ETF资金配置参考 and MUST record the strategy contract version in every run.

#### Scenario: Portfolio target is generated
- **WHEN** the replay date has enough eligible ETF data
- **THEN** the system generates target ETF weights using the same risk constraints as ETF资金配置参考, including the 30% single-ETF cap

#### Scenario: Market conditions are defensive or cash-wait
- **WHEN** offensive ETF candidates are insufficient
- **THEN** the system attempts defensive allocation and, if still insufficient, records cash-wait rather than forcing high-risk ETF exposure

#### Scenario: Backtest strategy contract matches page strategy
- **WHEN** a replay date and data snapshot are used by both the ETF资金配置参考 and the ETF portfolio backtest
- **THEN** both paths produce the same portfolio mode, target weights, exclusion reasons, and strategy version for the current workbench strategy

#### Scenario: Strategy contract changes
- **WHEN** the shared ETF strategy contract version changes
- **THEN** new backtest runs record the new version and old backtest runs remain auditable with their original version metadata

## ADDED Requirements

### Requirement: ETF portfolio backtest exposes current strategy evidence to comparison backtests
The ETF portfolio backtest SHALL make the current workbench strategy executable as one strategy inside ETF strategy comparison backtests.

#### Scenario: Comparison requests current strategy
- **WHEN** a strategy comparison backtest includes `current_workbench`
- **THEN** the ETF portfolio backtest engine provides its daily equity curve, trades, positions, and metrics through the same result contract used by other comparison strategies

#### Scenario: Current strategy evidence is missing
- **WHEN** no current strategy backtest exists for the requested date range
- **THEN** the comparison result marks current strategy evidence as unavailable rather than using legacy strategy-lab simulations
