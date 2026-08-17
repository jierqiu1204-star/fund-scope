## ADDED Requirements

### Requirement: ETF trade sizing requires explicit capital confirmation
The tracked-position exit strategy SHALL produce actionable ETF amount and share sizing only when the owner has explicitly confirmed a finite positive ETF sleeve capital.

#### Scenario: Capital is unconfirmed
- **WHEN** an owner still has a legacy/default capital value without explicit confirmation
- **THEN** the system may show target weight but returns null trade amount and shares with a stable configuration reason

#### Scenario: Capital or price is non-finite
- **WHEN** capital, price, current value, quantity or target weight is NaN, infinite, zero where positive is required, or otherwise invalid
- **THEN** sizing fails closed and does not emit an actionable amount

### Requirement: Owner risk state controls only increases in risk
The tracked-position strategy SHALL apply owner risk state before add or reentry recommendations without suppressing valid risk-reduction evidence.

#### Scenario: State is reduce-only
- **WHEN** the sizing result would add or reenter an ETF while the owner state is `reduce_only`
- **THEN** the result becomes `no_add` with state reasons, while trim/reduce/exit results remain available

#### Scenario: State is data-halt
- **WHEN** owner sleeve evidence is unavailable
- **THEN** new-risk sizing remains unavailable and existing exit warnings state the evidence limitation instead of claiming that no risk exists

### Requirement: ETF liquidity capacity is size-aware
The tracked-position strategy SHALL compare the proposed ETF trade amount with decision-eligible turnover and executable quote structure.

#### Scenario: Entry capacity is adequate
- **WHEN** proposed amount, ADV participation, spread, structure and quote eligibility pass the versioned entry policy
- **THEN** the add recommendation includes normal and stressed capacity evidence and its contract identity

#### Scenario: Entry capacity is inadequate or unavailable
- **WHEN** required capacity evidence is missing or breaches the policy
- **THEN** the strategy returns `no_add` or unavailable and MUST NOT use current turnover alone as proof that the planned amount is executable

#### Scenario: Exit capacity is poor
- **WHEN** a valid reduce or exit signal has a wide spread, low capacity or long estimated liquidation time
- **THEN** the system keeps the risk-reduction signal and labels execution as stressed/unavailable rather than converting it to hold
