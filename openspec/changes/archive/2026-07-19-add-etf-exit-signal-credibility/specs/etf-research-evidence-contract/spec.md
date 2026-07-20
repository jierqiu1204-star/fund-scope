## ADDED Requirements

### Requirement: Exit signal credibility evidence is tied to the ETF research contract
The system SHALL record ETF exit signal credibility with the signal version, exit rule version, execution model, data cutoff, and contract hash used to generate the evidence.

#### Scenario: Current contract evidence exists
- **WHEN** exit signal credibility was generated with the current ETF signal, allocation, exit rule, and execution model contract
- **THEN** the system marks the evidence as `同源已验证`

#### Scenario: Old contract evidence exists
- **WHEN** exit signal credibility was generated with an older signal, allocation, exit rule, or execution model contract
- **THEN** the system marks the evidence as `旧口径结果` and does not use it to prove current exit rules

#### Scenario: Execution model differs
- **WHEN** the latest credibility evidence was generated with `daily_close` but the page is explaining the intraday email workflow
- **THEN** the system marks the evidence as not directly comparable and displays it separately from `intraday_alert` evidence

#### Scenario: Evidence is missing
- **WHEN** no exit signal credibility evidence exists for the current contract
- **THEN** the system marks the exit signal evidence status as `等待验证`
