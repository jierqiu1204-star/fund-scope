## ADDED Requirements

### Requirement: Healthcheck-backed signal validation summary
ETF signal validation SHALL feed ETF strategy healthcheck with label outcome summaries for current and recent windows.

#### Scenario: Label validation linked to healthcheck
- **WHEN** label validation results exist for the same evidence contract
- **THEN** the healthcheck SHALL include those label outcome summaries in its diagnosis

#### Scenario: Contract mismatch
- **WHEN** label validation results were generated under an old contract hash
- **THEN** the healthcheck SHALL mark them as old-method evidence and MUST NOT use them as current reliability proof

### Requirement: Recent label outcome warning
ETF signal validation SHALL identify labels whose recent outcome is materially worse than their historical outcome.

#### Scenario: Label recent underperformance
- **WHEN** a label has enough recent samples and recent forward returns or drawdowns deteriorate materially
- **THEN** the system SHALL flag the label as recently weakened

#### Scenario: Sparse recent samples
- **WHEN** a label has too few recent samples
- **THEN** the system SHALL mark the recent label outcome as inconclusive instead of treating it as failure or success
