## ADDED Requirements

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
