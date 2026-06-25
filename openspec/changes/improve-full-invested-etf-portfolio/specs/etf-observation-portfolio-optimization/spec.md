## ADDED Requirements

### Requirement: ETF Observation Portfolio Is Fully Invested
The ETF observation portfolio SHALL allocate 100% of the user-designated ETF account capital across decision-eligible ETFs when enough eligible candidates are available.

#### Scenario: Full-invested portfolio is feasible
- **WHEN** at least the configured minimum number of ETF candidates have decision-eligible data and pass liquidity, history, and risk gates
- **THEN** the generated observation portfolio target weights sum to 100% excluding rounding tolerance

#### Scenario: Full-invested portfolio is not feasible
- **WHEN** too few ETF candidates have decision-eligible data or constraints make allocation impossible
- **THEN** the system returns no target weights and explains why a 100% ETF observation portfolio cannot be generated

### Requirement: ETF Observation Portfolio Uses Risk-Based Weighting
The ETF observation portfolio SHALL combine short-term score, volatility, drawdown, liquidity, correlation, and theme concentration when assigning target weights.

#### Scenario: High score asset is volatile
- **WHEN** an ETF has a high observation score but materially higher volatility or drawdown than comparable candidates
- **THEN** its target weight is reduced or capped with an explanation of the volatility or drawdown adjustment

#### Scenario: Similar ETFs compete for exposure
- **WHEN** two or more ETF candidates have high recent return correlation or overlapping theme exposure
- **THEN** the portfolio reduces combined exposure, excludes the weaker candidate, or caps the theme with an explanation

### Requirement: ETF Observation Portfolio Respects Explicit Constraints
The ETF observation portfolio SHALL apply explicit constraints including single ETF cap, theme cap, minimum weight, liquidity gate, and data eligibility gate.

#### Scenario: Single ETF would exceed cap
- **WHEN** a candidate's unconstrained weight exceeds 30%
- **THEN** the final target weight is capped at 30% and the cap is recorded in the weight reason

#### Scenario: Theme concentration would exceed cap
- **WHEN** candidates from one theme would exceed the configured theme cap
- **THEN** the system reduces or excludes theme exposure and records the theme cap effect

#### Scenario: Weight is too small to be useful
- **WHEN** a candidate's final weight would fall below the configured minimum useful weight
- **THEN** the ETF is moved to watch-only or excluded from target weights with an explanation

### Requirement: ETF Observation Portfolio Explains Every Weight
The ETF observation portfolio SHALL explain each assigned, capped, reduced, watch-only, or excluded ETF.

#### Scenario: ETF receives target weight
- **WHEN** an ETF receives target weight
- **THEN** the result includes final weight, score contribution, volatility adjustment, drawdown adjustment, liquidity status, correlation effect, theme cap effect, and data reliability

#### Scenario: ETF receives no target weight
- **WHEN** an ETF is excluded or left as watch-only
- **THEN** the result includes a readable reason such as data ineligible, low liquidity, high correlation, theme cap, low score, insufficient history, or minimum weight filter

### Requirement: ETF Observation Portfolio Is Research-Only
The ETF observation portfolio SHALL present 100% weights as research references for the user's ETF account capital, not as automatic trade instructions or whole-net-worth allocation.

#### Scenario: User views full-invested weights
- **WHEN** the UI displays ETF observation portfolio weights
- **THEN** it states that weights apply to the user-designated ETF account capital and require manual judgment before any securities account action
