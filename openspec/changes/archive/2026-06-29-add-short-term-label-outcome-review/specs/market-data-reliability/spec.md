## ADDED Requirements

### Requirement: Label Outcome Review Uses Verified Future Daily Data Only
The system SHALL use only verified or alternate-provider daily close data to calculate ETF label outcome review results.

#### Scenario: Verified future close exists
- **WHEN** a future daily close has verified or alternate-provider reliability and matches the expected trading-day horizon
- **THEN** it may be used to calculate forward outcome metrics

#### Scenario: Only fallback or stale future data exists
- **WHEN** future price data is fallback, estimated, stale, display-only, missing timestamp, or otherwise not decision-eligible
- **THEN** the validator excludes that outcome and records a data reliability reason

### Requirement: Label Outcome Review Prevents Lookahead Bias
The system SHALL separate original signal context from future outcome data and MUST NOT use future data to reconstruct the original label.

#### Scenario: Outcome is computed
- **WHEN** a label outcome is calculated for a historical signal
- **THEN** future prices are used only for outcome metrics and not for changing the original signal label, score, or reason

#### Scenario: Future window is incomplete
- **WHEN** the future horizon is not fully available
- **THEN** the result stays pending and is excluded from confidence summaries
