## ADDED Requirements

### Requirement: ETF Label Snapshots Are Reviewed After Future Windows Complete
The system SHALL review historical ETF short-term label snapshots after their 1, 3, 5, and 10 trading-day future windows have completed.

#### Scenario: Future window is complete
- **WHEN** a historical ETF signal item has verified future close prices for a configured horizon
- **THEN** the validator records forward return, maximum adverse drawdown, maximum favorable excursion, and completion status for that horizon

#### Scenario: Future window is not complete
- **WHEN** the required future trading-day close is not available yet
- **THEN** the validator keeps the outcome pending and does not estimate or fabricate the result

### Requirement: ETF Label Validation Uses Stored Signal Context
The system SHALL validate labels using the label, entry timing, score, metrics, and rule version stored at the original signal time.

#### Scenario: Signal rules changed later
- **WHEN** current scoring rules differ from the historical signal item's rule version
- **THEN** the validator uses the stored historical label context and records the rule version used for grouping

#### Scenario: Historical signal context is incomplete
- **WHEN** a historical signal item lacks required label or price context
- **THEN** the validator excludes it from validation and records an exclusion reason

### Requirement: ETF Label Confidence Is Classified
The system SHALL classify label evidence confidence using sample count, data freshness, and recent degradation.

#### Scenario: Sufficient sample count
- **WHEN** a label combination has at least the configured sufficient sample count and validation is fresh
- **THEN** the result may be marked `样本充足` unless recent degradation is detected

#### Scenario: Limited sample count
- **WHEN** a label combination has some completed samples but fewer than the sufficient threshold
- **THEN** the result is marked `样本有限` and MUST NOT be presented as strong evidence

#### Scenario: Insufficient sample count
- **WHEN** a label combination has fewer than the minimum sample count
- **THEN** the result is marked `样本不足` and MUST NOT be presented as reliable evidence

#### Scenario: Recent degradation detected
- **WHEN** recent completed outcomes are materially worse than the longer-window summary for the same label combination
- **THEN** the result is marked `近期走弱` with the affected horizon and metric

### Requirement: ETF Label Review Is Idempotent
The system SHALL update label outcome records idempotently without duplicate rows for the same signal item and horizon.

#### Scenario: Review job runs repeatedly
- **WHEN** the label review job processes the same signal item and horizon multiple times
- **THEN** it updates or preserves one outcome record instead of inserting duplicate outcomes
