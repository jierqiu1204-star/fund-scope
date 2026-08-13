## ADDED Requirements

### Requirement: Sentiment risk overlay is validated as a frozen policy shadow
The validation system SHALL compare the frozen raw A-share breakout cohort with the same cohort after the sentiment-risk entry overlay on common PIT dates, using the existing five-session theme-relative cost-adjusted endpoint and separately reporting avoided losses, missed gains, coverage, state frequency, turnover, drawdown, and regime concentration.

#### Scenario: Overlay appears beneficial in a short sample
- **WHEN** warning or risk-off dates avoid losses but the existing sample, uncertainty, walk-forward, and holdout gates are incomplete
- **THEN** the result remains `insufficient_data` and cannot alter production ranking, scoring, position, alert, or execution policy

#### Scenario: Threshold tuning is attempted after outcomes are read
- **WHEN** a run changes cohort bands, component thresholds, or state counts after observing validation outcomes
- **THEN** it requires a new contract identity and its evidence cannot be merged with the frozen proxy
