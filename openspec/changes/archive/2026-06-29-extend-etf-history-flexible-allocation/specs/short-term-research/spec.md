## ADDED Requirements

### Requirement: Short-term workbench shows partial ETF allocation clearly
The short-term research workbench SHALL show that ETF allocation is a maximum-exposure reference and may intentionally leave cash waiting.

#### Scenario: Partial ETF allocation exists
- **WHEN** the ETF observation portfolio allocates less than 100% exposure
- **THEN** the UI shows selected ETF weights, waiting-cash weight, and the reason that remaining funds are not forced into weaker candidates

#### Scenario: Full ETF allocation exists
- **WHEN** enough decision-eligible ETFs qualify for full exposure
- **THEN** the UI may show 100% ETF allocation while still stating that it is a research reference requiring manual judgment

#### Scenario: No ETF qualifies
- **WHEN** the portfolio mode is `cash_wait` and `cash_weight=1.0`
- **THEN** the UI states that no ETF currently qualifies and avoids buy/recommendation wording

### Requirement: Short-term workbench exposes ETF history readiness
The short-term research workbench SHALL expose whether ETF daily history is sufficient for research and backtesting.

#### Scenario: History coverage is shallow
- **WHEN** ETF daily history is insufficient for the requested research or backtest horizon
- **THEN** the UI shows a data-readiness warning and points to the long-history backfill task

#### Scenario: Long history is available
- **WHEN** ETF daily history covers the configured long window
- **THEN** the UI shows the earliest/latest dates and indicates that backtests can use the expanded verified history
