## ADDED Requirements

### Requirement: ETF Evidence Contract Records Catalyst Shadow Provenance
The ETF research evidence contract SHALL record catalyst source registry versions, receipt IDs and hashes, event and mapping versions, snapshot cutoffs and hashes, coverage states, extraction methods, verification states, and production-isolation status.

#### Scenario: Catalyst shadow snapshot is created
- **WHEN** the system creates a point-in-time catalyst snapshot
- **THEN** the evidence contract records the exact receipts, event versions, taxonomy, source cutoff, coverage, limitations, and snapshot hash

#### Scenario: Catalyst event study is created
- **WHEN** a pre-registered catalyst event study runs
- **THEN** the evidence contract records its immutable manifest, cohorts, controls, execution and cost policies, splits, outcomes, exclusions, uncertainty, and contract hash

#### Scenario: Catalyst evidence is offered as score validation
- **WHEN** no separate catalyst scoring contract and matching out-of-sample evidence exist
- **THEN** the system marks the catalyst evidence as shadow research and MUST NOT present it as validation of current score, rank, allocation, alert, or notification behavior
