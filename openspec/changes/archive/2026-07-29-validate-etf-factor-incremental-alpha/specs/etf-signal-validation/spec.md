## ADDED Requirements

### Requirement: ETF Signal Validation Supports Factor And Rank Evidence
ETF signal validation SHALL support factor-level and ranked-portfolio outcome evidence in addition to label-combination evidence.

#### Scenario: Factor evidence is generated
- **WHEN** a registered factor experiment completes valid point-in-time samples
- **THEN** validation stores factor IC, quantile outcomes, paired baseline comparisons, costs, turnover, rank churn, coverage, exclusions, and uncertainty by experiment hash

#### Scenario: Ranking-surface evidence is generated
- **WHEN** a ranked candidate is evaluated
- **THEN** validation records the ranking contract ID and hash, score field, signal cutoff, execution convention, future horizon, and common-support sample identity

#### Scenario: Factor evidence lacks matching contract
- **WHEN** samples mix ranking contracts, experiment hashes, execution rules, or data-provenance policies
- **THEN** the validator separates or rejects them instead of aggregating incompatible evidence

## MODIFIED Requirements

### Requirement: Validation does not alter live labels automatically
The system SHALL keep label, factor, and ranking evidence separate from live ranking labels and MUST NOT automatically change labels, scores, factor weights, ranking contracts, allocation, or notification behavior based only on validation output.

#### Scenario: Validation shows weak evidence
- **WHEN** a label combination or candidate factor has poor historical forward outcomes
- **THEN** the system displays the weak evidence but does not silently rewrite the live label, score, or downstream rule

#### Scenario: Validation shows strong factor evidence
- **WHEN** a candidate factor passes the registered research gates
- **THEN** the system may mark it eligible for a separate proposal but MUST NOT publish or weight it automatically
