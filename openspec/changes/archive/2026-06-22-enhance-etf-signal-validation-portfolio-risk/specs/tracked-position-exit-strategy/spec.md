## ADDED Requirements

### Requirement: Exit thresholds are volatility-adaptive
The tracked position exit strategy SHALL calculate hard stop and trailing take-profit thresholds using recent realized volatility, recent drawdown, current profit state, and data reliability.

#### Scenario: ETF has high recent volatility
- **WHEN** a tracked ETF has high recent realized volatility and decision-eligible prices
- **THEN** the exit strategy uses wider volatility-adjusted thresholds and records the calculation reason

#### Scenario: ETF has low recent volatility
- **WHEN** a tracked ETF has low recent realized volatility and decision-eligible prices
- **THEN** the exit strategy uses tighter volatility-adjusted thresholds and records the calculation reason

#### Scenario: Volatility data is insufficient
- **WHEN** recent volatility cannot be calculated from decision-eligible data
- **THEN** the system falls back to conservative fixed thresholds marked as `fixed_fallback` or suppresses the alert if the price is not decision-eligible

### Requirement: Adaptive threshold context is persisted
The tracked position exit strategy SHALL persist high-water profit, current threshold values, threshold mode, and explanation for each active tracked position.

#### Scenario: Position reaches a new high-water profit
- **WHEN** a tracked ETF reaches a new high-water profit
- **THEN** the persisted exit state updates high-water profit and recalculates trailing threshold context

#### Scenario: Alert is generated
- **WHEN** an exit alert is generated
- **THEN** the alert includes threshold mode, threshold value, current profit, high-water profit, and human-readable reason
