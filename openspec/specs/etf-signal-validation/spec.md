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

### Requirement: ETF Signal Validation Prevents Lookahead Bias
The ETF signal validation process SHALL only use information that would have been available at the signal timestamp.

#### Scenario: Historical signal is replayed
- **WHEN** the validator evaluates a signal generated on a historical date
- **THEN** it uses ranking inputs, quote reliability, and price history available on or before that date

#### Scenario: Future data would affect the signal
- **WHEN** a metric requires data after the signal timestamp
- **THEN** the validator excludes that metric from signal reconstruction and records a lookahead-prevention reason

### Requirement: ETF Signal Validation Tracks Recent Degradation
The ETF signal validation process SHALL compare recent label outcomes with longer-window label outcomes.

#### Scenario: Recent outcomes weaken
- **WHEN** recent forward outcomes are materially worse than the longer-window summary for the same label combination
- **THEN** the validation result marks the label as recently weakened and includes the affected horizon

#### Scenario: Recent sample is too small
- **WHEN** recent completed samples are below the configured minimum
- **THEN** the validation result marks recent comparison as insufficient instead of producing a degradation claim

### Requirement: ETF signal validation supports historical replay
The system SHALL support historical replay validation for ETF observation labels and entry timing labels, using only data available on or before each replay date.

#### Scenario: Historical replay reconstructs labels
- **WHEN** a historical replay validation run evaluates an ETF on a replay date
- **THEN** it reconstructs the observation label, entry timing label, score inputs, and data reliability using only prices and metrics dated on or before that replay date

#### Scenario: Future data is unavailable during reconstruction
- **WHEN** a metric or source value would require data after the replay date
- **THEN** the validator excludes that value from label reconstruction and records a no-lookahead exclusion reason

#### Scenario: Historical replay records rule version
- **WHEN** a historical replay validation run is created
- **THEN** it records the signal rule version, replay date range, universe source, horizons, and data coverage summary

### Requirement: ETF historical replay records forward outcomes
The system SHALL calculate forward ETF outcomes after each replayed signal for configured 1, 3, 5, and 10 trading-day horizons.

#### Scenario: Forward outcome is completed
- **WHEN** the replayed signal has enough future decision-eligible ETF daily prices for a horizon
- **THEN** the system records forward return, maximum adverse excursion, maximum favorable excursion, horizon end date, and price source

#### Scenario: Forward outcome is not completed
- **WHEN** the replayed signal lacks enough future decision-eligible ETF daily prices for a horizon
- **THEN** the system excludes that sample for the horizon and records the missing-data reason

#### Scenario: Replay uses daily close only
- **WHEN** historical replay calculates entry and future prices
- **THEN** it uses verified ETF daily close data and MUST NOT use stale intraday quotes, estimated prices, or AI-generated values

### Requirement: ETF historical replay aggregates label evidence
The system SHALL aggregate historical replay outcomes by observation label, entry timing label, horizon, asset type, and rule version.

#### Scenario: Label combination is summarized
- **WHEN** replay samples exist for a label combination and horizon
- **THEN** the system records sample count, average return, median return, win rate, worst forward drawdown, median maximum favorable excursion, data coverage, and confidence state

#### Scenario: Label combination has insufficient samples
- **WHEN** a label combination has fewer than the configured minimum completed samples
- **THEN** the system marks the evidence as `insufficient` and MUST NOT expose it as reliable evidence

#### Scenario: Recent evidence weakens
- **WHEN** recent replay outcomes are materially worse than the longer replay summary for the same label combination
- **THEN** the system marks the label evidence as recently weakened and includes the affected horizon

### Requirement: Historical replay evidence remains research-only
The system SHALL keep historical replay validation separate from live ranking, portfolio allocation, tracked-position alerts, and notification sending.

#### Scenario: Historical replay finds weak evidence
- **WHEN** a label combination has weak historical replay outcomes
- **THEN** the system displays the evidence but does not automatically rewrite current labels, scores, portfolio weights, or email alert decisions

#### Scenario: Historical replay run completes
- **WHEN** a historical replay validation run finishes successfully
- **THEN** it updates validation evidence only and MUST NOT create tracked-position alerts or notification logs

### Requirement: ETF signal validation is grouped by evidence contract
ETF signal validation SHALL group historical outcomes by the signal contract fields used by the short-term workbench.

#### Scenario: Label outcome is calculated
- **WHEN** ETF label validation calculates forward returns and drawdowns
- **THEN** it groups results by observation label, entry timing label, signal rule version, data reliability state, and signal date

#### Scenario: Current signal version changes
- **WHEN** the signal rule version changes
- **THEN** old validation results are not presented as current-version validation unless explicitly marked as old evidence

### Requirement: ETF signal validation reports evidence quality
ETF signal validation SHALL report sample sufficiency, coverage, and future-window completion for each label combination.

#### Scenario: Enough completed samples exist
- **WHEN** a label combination has enough completed future windows
- **THEN** the validation summary includes sample count, win rate, average forward return, maximum drawdown, and confidence state

#### Scenario: Future window is incomplete
- **WHEN** a label was generated recently and the required forward window has not elapsed
- **THEN** the validation excludes that item from completed-sample statistics and records it as pending

