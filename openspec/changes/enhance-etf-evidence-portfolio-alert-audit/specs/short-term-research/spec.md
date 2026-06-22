## ADDED Requirements

### Requirement: ETF label evidence display
The short-term research page SHALL display evidence quality for ETF buy-observation labels and entry timing labels.

#### Scenario: Sufficient evidence shown
- **WHEN** an ETF label has enough valid historical samples
- **THEN** the page shows sample count, forward performance summary, and confidence wording

#### Scenario: Insufficient evidence shown
- **WHEN** an ETF label has too few valid samples or stale evidence
- **THEN** the page labels it as sample-insufficient and does not present it as reliable

### Requirement: Label degradation warning
The short-term research page SHALL show when recent label outcomes have degraded versus historical averages.

#### Scenario: Recent degradation
- **WHEN** recent forward outcomes are materially worse than the long-run label summary
- **THEN** the page shows a warning that the label is weakening and needs observation
