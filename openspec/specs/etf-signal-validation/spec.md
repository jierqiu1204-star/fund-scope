# etf-signal-validation Specification

## Purpose
TBD - created by archiving change enhance-etf-signal-validation-portfolio-risk. Update Purpose after archive.
## Requirements
### Requirement: ETF signal outcomes are validated against forward returns
The system SHALL validate ETF ranking labels and entry timing labels against future 1, 3, 5, and 10 trading-day outcomes using decision-eligible historical price data.

#### Scenario: Validation run computes label outcomes
- **WHEN** a validation run processes historical ETF signal items with enough future prices
- **THEN** it records sample count, average return, median return, win rate, worst forward drawdown, and horizon for each label combination

#### Scenario: Missing future price excludes a sample
- **WHEN** a signal item does not have decision-eligible future prices for a horizon
- **THEN** the system excludes that sample from that horizon and records the exclusion reason

### Requirement: Validation confidence is explicit
The system SHALL classify validation summaries as sufficient, limited, or insufficient based on sample count and data freshness.

#### Scenario: Label has too few samples
- **WHEN** a label combination has fewer than the configured minimum sample count
- **THEN** the validation result is marked `insufficient_samples` and MUST NOT be displayed as reliable evidence

#### Scenario: Validation data is stale
- **WHEN** the latest validation run is older than the configured freshness window
- **THEN** the UI and API indicate that validation evidence is stale

### Requirement: Validation does not alter live labels automatically
The system SHALL keep validation evidence separate from live ranking labels and MUST NOT automatically change labels or scores based only on validation output.

#### Scenario: Validation shows weak evidence
- **WHEN** a label combination has poor historical forward outcomes
- **THEN** the system displays the weak evidence but does not silently rewrite the live label

