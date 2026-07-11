## ADDED Requirements

### Requirement: Black-Litterman uses eligible ETF inputs only
The system SHALL generate Black-Litterman ETF allocation only from ETFs with decision-eligible market data, sufficient daily history, and valid research signals.

#### Scenario: ETF has stale or display-only data
- **WHEN** an ETF has stale, estimated, unavailable, or display-only market data
- **THEN** the Black-Litterman allocator assigns it zero decision confidence and excludes it from target weights with an explanation

#### Scenario: ETF has insufficient return history
- **WHEN** an ETF does not have enough daily return observations for covariance estimation
- **THEN** the allocator excludes it and records the insufficient-history reason

### Requirement: Black-Litterman explains prior weights
The system SHALL record and display the prior weight source used by the Black-Litterman allocation.

#### Scenario: AUM prior is available
- **WHEN** reliable ETF AUM or fund-size data is available for eligible candidates
- **THEN** the allocator uses it as the market prior and records `prior_source=aum`

#### Scenario: AUM prior is unavailable
- **WHEN** reliable ETF AUM or fund-size data is unavailable
- **THEN** the allocator uses a documented deterministic fallback prior and records the fallback reason

### Requirement: Black-Litterman views are structured and reproducible
The system SHALL derive Black-Litterman views from structured FundScope signals rather than AI prose.

#### Scenario: ETF has a compatible published ranking
- **WHEN** an ETF has a compatible current short-term score, valid current labels, and decision-eligible market inputs
- **THEN** the allocator converts only those current-contract fields into a reproducible view with numeric direction and confidence

#### Scenario: AI explanation exists
- **WHEN** an AI explanation exists for an ETF
- **THEN** the allocator may display the explanation beside the result but MUST NOT use AI prose as a mathematical view input

### Requirement: Validation evidence cannot change allocation confidence
The system SHALL set Black-Litterman confidence from current decision-eligible data reliability and market-history coverage and SHALL keep label validation, replay, backtest, and strategy healthcheck evidence display-only.

#### Scenario: Evidence is weak
- **WHEN** label validation or strategy healthcheck indicates weak, unstable, or insufficient evidence
- **THEN** the allocator displays the evidence state but leaves views, confidence, weights, and all other current decision outputs unchanged

#### Scenario: Evidence is unavailable
- **WHEN** there is no usable evidence for a candidate ETF
- **THEN** the allocator displays evidence as unavailable and continues to determine eligibility only from the published ranking and current market-input contract

### Requirement: Black-Litterman enforces FundScope allocation constraints
The system SHALL apply FundScope ETF allocation constraints to Black-Litterman output.

#### Scenario: Posterior return favors one ETF
- **WHEN** the Black-Litterman posterior return strongly favors one ETF
- **THEN** the final target weight still respects the single ETF 30% cap

#### Scenario: Candidates are theme concentrated
- **WHEN** Black-Litterman weights concentrate in one theme or near-duplicate exposure group
- **THEN** the allocator reduces concentration or marks the excess as excluded with a theme/correlation reason

### Requirement: Black-Litterman output is research-only
The system SHALL present Black-Litterman weights as research comparison output and SHALL NOT use them to trigger emails, auto-trade, or modify tracked positions.

#### Scenario: User views Black-Litterman allocation
- **WHEN** the UI displays Black-Litterman weights
- **THEN** it states that the output is an allocation comparison requiring manual judgment

#### Scenario: Black-Litterman weight differs from current holding
- **WHEN** the Black-Litterman target weight differs from a user's tracked position
- **THEN** the system does not create a sell, reduce, buy, or email alert solely because of that difference

### Requirement: Black-Litterman unavailable state is explicit
The system SHALL return an explicit unavailable result instead of fabricated weights when inputs are insufficient or optimization fails.

#### Scenario: Covariance is unusable
- **WHEN** covariance estimation fails or produces an invalid matrix
- **THEN** the allocator returns `status=unavailable` with a covariance reason and no target weights

#### Scenario: Constraints make the solution infeasible
- **WHEN** allocation constraints prevent a valid solution
- **THEN** the allocator returns `status=unavailable` or a clearly labeled comparison fallback without pretending it is Black-Litterman output

### Requirement: Black-Litterman evidence is comparable
The system SHALL store enough method metadata for Black-Litterman results to be compared with rule allocation, minimum-volatility, and risk-parity methods.

#### Scenario: Allocation snapshot is generated
- **WHEN** a Black-Litterman allocation snapshot is generated
- **THEN** it records prior source, view count, confidence summary, covariance window, constraints, excluded assets, and generated weights

#### Scenario: Backtest evidence is available
- **WHEN** historical evidence exists for the Black-Litterman allocation method
- **THEN** the UI shows its performance and evidence status beside other allocation methods
