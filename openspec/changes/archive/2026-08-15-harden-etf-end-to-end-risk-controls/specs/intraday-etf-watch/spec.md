## ADDED Requirements

### Requirement: Intraday quote decision eligibility is explicit
The intraday ETF watch SHALL treat quote decision eligibility as an explicit positive fact rather than inferring eligibility from a missing field or a usable display price.

#### Scenario: Quote explicitly passes decision checks
- **WHEN** a fresh quote records `decision_eligible=true` together with an allowed reliability state and valid quote time
- **THEN** downstream risk workflows may use it subject to their remaining bid, ask, spread, and provider checks

#### Scenario: Legacy quote lacks eligibility field
- **WHEN** an older stored quote has no explicit `decision_eligible` value
- **THEN** the quote remains displayable but is decision-ineligible and MUST NOT trigger an ETF action email

#### Scenario: Display price exists without executable quote
- **WHEN** a quote has a positive latest price but lacks fresh valid bid or ask evidence required for an action
- **THEN** the system MUST NOT infer executable buy or sell evidence from the latest price
