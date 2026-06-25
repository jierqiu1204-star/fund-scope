## ADDED Requirements

### Requirement: Evidence data reliability labels
ETF label evidence, portfolio weights, and alert audits SHALL label data reliability consistently.

#### Scenario: Decision-eligible evidence
- **WHEN** evidence or portfolio calculations use verified or alternate-provider data
- **THEN** the result marks the data as decision-eligible and records its source

#### Scenario: Display-only evidence
- **WHEN** evidence or portfolio calculations encounter stale, estimated, or unavailable data
- **THEN** the result excludes it from decision calculations or marks it display-only with a reason

### Requirement: No fallback in financial decisions
The system SHALL NOT use fallback explanations, zero placeholders, stale quotes, or estimated prices to produce financial decision emails or observation weights.

#### Scenario: Fallback blocked
- **WHEN** only fallback or estimated data is available
- **THEN** the system shows an unavailable/display-only state instead of generating weights or email reminders
