## ADDED Requirements

### Requirement: ETF Evidence Contract Records Factor Experiments
The ETF research evidence contract SHALL record factor experiment manifests, hashes, code versions, data provenance, split roles, candidate identities, execution and cost policies, sample exclusions, uncertainty methods, and production-isolation status.

#### Scenario: Factor experiment starts
- **WHEN** a pre-registered factor experiment is accepted
- **THEN** the evidence contract stores its immutable manifest hash before outcome calculation

#### Scenario: Factor sample is persisted
- **WHEN** a factor or ranked-portfolio sample is stored
- **THEN** it references the experiment hash, ranking contract, signal date, split role, source cutoff, factor inputs, eligibility state, outcome horizon, and exclusion state

#### Scenario: Evidence is displayed
- **WHEN** a user views a factor experiment report
- **THEN** the report identifies development, validation, and holdout results and states that the evidence cannot mutate production

#### Scenario: Evidence is offered as current ranking proof
- **WHEN** an experiment does not match the current ranking contract and all required holdout gates
- **THEN** it is marked research-only, version-mismatched, or insufficient and MUST NOT be shown as same-source production validation
