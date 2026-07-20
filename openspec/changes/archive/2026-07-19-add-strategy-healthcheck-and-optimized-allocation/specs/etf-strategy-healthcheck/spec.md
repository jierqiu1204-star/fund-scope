## ADDED Requirements

### Requirement: ETF strategy healthcheck snapshots
The system SHALL generate ETF strategy healthcheck snapshots that summarize whether the current ETF workbench strategy remains valid under the latest available evidence.

#### Scenario: Generate healthcheck snapshot
- **WHEN** an admin job or scheduled job runs ETF strategy healthcheck
- **THEN** the system SHALL produce a snapshot containing the signal run id, evidence contract hash, data window, generated time, and overall conclusion

#### Scenario: Missing evidence
- **WHEN** there is no current ETF signal run or no compatible evidence contract
- **THEN** the system SHALL return a waiting/data-insufficient healthcheck status instead of fabricating a conclusion

### Requirement: Segmented performance diagnosis
The system SHALL diagnose ETF strategy performance by time window, label, entry timing, theme, market regime, and execution model.

#### Scenario: Recent window degradation
- **WHEN** the recent window underperforms the configured baseline or has materially worse drawdown
- **THEN** the system SHALL flag the recent period as degraded and include the affected labels/themes in the diagnosis

#### Scenario: Label-level diagnosis
- **WHEN** a label such as "健康回踩" or "冲高别追" has enough historical samples
- **THEN** the system SHALL report sample count, average forward return, win rate, drawdown, and confidence level for that label

#### Scenario: Insufficient label samples
- **WHEN** a label has too few historical samples
- **THEN** the system SHALL mark the label diagnosis as data insufficient and exclude it from reliability claims

### Requirement: Healthcheck conclusion levels
The system SHALL classify ETF strategy healthcheck conclusions as `可继续观察`, `待验证`, `近期失效`, or `数据不足`.

#### Scenario: Recent failure
- **WHEN** the strategy fails recent-window thresholds while the baseline does not fail the same way
- **THEN** the system SHALL classify the strategy as `近期失效` and explain the failing windows

#### Scenario: Mixed evidence
- **WHEN** full-sample results are acceptable but recent samples are weak or sparse
- **THEN** the system SHALL classify the strategy as `待验证`

#### Scenario: No buy instruction
- **WHEN** the healthcheck conclusion is displayed
- **THEN** the system SHALL present it as research evidence and MUST NOT label it as a buy or sell instruction

### Requirement: Daily and intraday evidence separation
The system SHALL separately report daily-close evidence and intraday-alert execution evidence.

#### Scenario: Intraday history unavailable
- **WHEN** intraday historical quotes are unavailable for the requested window
- **THEN** the system SHALL mark intraday-alert evidence as unavailable and MUST NOT fall back to daily-close evidence

#### Scenario: Daily evidence available
- **WHEN** daily close data is available but intraday evidence is missing
- **THEN** the system SHALL display daily evidence as daily-only and explicitly state that it does not validate intraday email execution
