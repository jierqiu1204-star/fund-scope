## ADDED Requirements

### Requirement: Rule and optimized allocation side-by-side display
The ETF observation portfolio capability SHALL expose both the current rule-based allocation and any available optimized allocation comparison.

#### Scenario: Both allocations available
- **WHEN** the observation portfolio endpoint has a current rule allocation and an optimized allocation snapshot
- **THEN** it SHALL return both allocations with separate method labels, data windows, generated times, and constraint summaries

#### Scenario: Optimized allocation missing
- **WHEN** no optimized allocation snapshot exists
- **THEN** the endpoint SHALL keep returning the rule-based allocation and include a waiting status for optimized allocation

### Requirement: Allocation source clarity
The ETF observation portfolio capability SHALL identify whether each displayed allocation is rule-based, optimized, equal-weight, or unavailable.

#### Scenario: Page renders allocation
- **WHEN** the frontend displays ETF funds configuration reference
- **THEN** it SHALL show the allocation source and MUST NOT merge optimized weights into rule-based weights without a label

#### Scenario: Data window disclosure
- **WHEN** an allocation is displayed
- **THEN** the frontend SHALL show the data window or latest data time used to generate that allocation
