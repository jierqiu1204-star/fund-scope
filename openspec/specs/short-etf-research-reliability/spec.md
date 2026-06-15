# short-etf-research-reliability Specification

## Purpose
TBD - created by archiving change enhance-short-etf-research-reliability. Update Purpose after archive.
## Requirements
### Requirement: ETF Data Sync Uses Provider Fallback
The system SHALL use AKShare as the primary ETF daily-price source and SHALL attempt a configured backup public provider when the primary source fails or returns no usable rows for an ETF.

#### Scenario: Backup provider fills a primary-source failure
- **WHEN** AKShare fails for one ETF during short ETF data synchronization
- **THEN** the system attempts the backup provider for that ETF before marking it failed

#### Scenario: One ETF failure does not block the universe
- **WHEN** both providers fail for one ETF during synchronization
- **THEN** the sync result records that ETF code and readable failure reason while continuing with the remaining ETFs

### Requirement: ETF Data Health Is Visible
The system SHALL maintain per-ETF data health showing latest data date, provider used, successful row count, latest failure reason, and consecutive failure count.

#### Scenario: Data health updates after sync
- **WHEN** ETF data synchronization finishes
- **THEN** each attempted ETF has data-health status reflecting success, provider fallback, or failure

#### Scenario: Data health is shown in the web UI
- **WHEN** the user opens the short ETF tab or admin jobs page
- **THEN** the UI shows ETF data coverage, stale data warnings, provider fallback counts, and failed ETF codes in Chinese

### Requirement: Short ETF Risk Labels Are Expanded
The system SHALL identify short-term ETF risk labels for chase risk, suspicious recent surge, high volatility, low liquidity, large drawdown, stale data, and insufficient history.

#### Scenario: High recent surge is marked risky
- **WHEN** an ETF has a recent return pattern above configured surge thresholds
- **THEN** the signal item includes a prominent chase or surge risk label and cannot be presented as stronger than observation language

#### Scenario: Insufficient data limits confidence
- **WHEN** an ETF lacks enough daily history for the configured metrics
- **THEN** the ETF is marked with insufficient-history risk or excluded from ranking with a readable reason

### Requirement: Review Explains Both Opportunity And Risk
The system SHALL generate rule-first multi-role review notes that explain why each ETF may be worth observing and why it may be dangerous, without changing rank or score.

#### Scenario: Review preserves original signal ranking
- **WHEN** a short ETF review is generated
- **THEN** each review item keeps the original signal rank and total score unchanged

#### Scenario: Review uses research-only language
- **WHEN** the API returns a review item
- **THEN** the item includes data, trend, risk, opposing-view, and summary notes but no buy instruction, sell instruction, target price, or guaranteed return

### Requirement: Reliability Evaluation Tests Signal Robustness
The system SHALL support reliability evaluation for short ETF signal rules using historical replay, fee assumptions, nearby parameter sets, and comparison against simple baselines.

#### Scenario: Evaluation stores research results separately
- **WHEN** the user runs a short ETF reliability evaluation
- **THEN** the system persists evaluation summary and parameter items without modifying signal runs, paper portfolios, or virtual orders

#### Scenario: Parameter instability is flagged
- **WHEN** only one narrow parameter combination performs well while nearby combinations perform poorly
- **THEN** the evaluation marks parameter stability risk in the summary

#### Scenario: Sample size warning is enforced
- **WHEN** available ETF history is shorter than the configured observation threshold
- **THEN** the evaluation conclusion is limited to sample-insufficient language

### Requirement: Short ETF Reliability Is Operable From The Web
The system SHALL expose Chinese web controls for retrying failed ETF data, viewing data health, running reliability evaluation, and inspecting evaluation charts.

#### Scenario: User retries failed ETFs
- **WHEN** the user clicks retry failed ETF data from the web UI
- **THEN** the system reruns data sync only for failed or stale ETFs and shows the result in Chinese

#### Scenario: User views evaluation output
- **WHEN** the user opens reliability evaluation results
- **THEN** the UI shows conclusion, sample-size warning, return, drawdown, fee impact, parameter stability, and baseline comparison charts in Chinese

