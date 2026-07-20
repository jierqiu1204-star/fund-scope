## ADDED Requirements

### Requirement: Optimized ETF allocation snapshots
The system SHALL generate optimized ETF allocation snapshots as a research-only comparison to the existing rule-based ETF allocation.

#### Scenario: Generate optimized allocation
- **WHEN** enough eligible ETF return data is available
- **THEN** the system SHALL generate at least one optimized allocation with method name, data window, constraints, selected ETFs, weights, and explanation

#### Scenario: Optimization unavailable
- **WHEN** the eligible ETF set or return matrix is insufficient
- **THEN** the system SHALL return an unavailable optimization result with a clear reason and MUST NOT output placeholder weights

### Requirement: Optimization constraints
The optimized allocation SHALL respect the same risk boundaries as the ETF workbench unless explicitly shown as a comparison-only relaxation.

#### Scenario: Single ETF cap
- **WHEN** optimized weights are generated
- **THEN** no single ETF weight SHALL exceed 30%

#### Scenario: Theme concentration cap
- **WHEN** multiple ETFs belong to the same theme group
- **THEN** the combined optimized theme exposure SHALL NOT exceed the configured theme concentration cap

#### Scenario: Ineligible data
- **WHEN** an ETF has stale data, insufficient history, low liquidity, or unreliable prices
- **THEN** the optimizer SHALL exclude that ETF from decision weights

### Requirement: Stable optimization methods
The system SHALL prioritize stable optimization methods over expected-return-sensitive maximum Sharpe optimization.

#### Scenario: Default optimization
- **WHEN** the optimized allocation job runs with default settings
- **THEN** it SHALL generate minimum-volatility and/or risk-parity style allocations before any maximum-Sharpe-style allocation

#### Scenario: Expected return sensitivity
- **WHEN** an optimization method requires expected returns
- **THEN** the system SHALL display the expected-return source and mark the method as higher sensitivity

### Requirement: Allocation comparison
The system SHALL compare optimized allocation against rule-based and equal-weight allocations.

#### Scenario: Portfolio comparison output
- **WHEN** optimized allocation is available
- **THEN** the response SHALL include comparable summary metrics for rule-based, optimized, and equal-weight allocations

#### Scenario: Optimization not default
- **WHEN** optimized allocation outperforms in a backtest
- **THEN** the system SHALL still treat it as comparison evidence until manually promoted by a future explicit change
