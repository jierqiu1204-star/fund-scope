## ADDED Requirements

### Requirement: ETF exit signal outcomes are evaluated after trigger
The system SHALL evaluate each ETF exit signal by comparing the trigger point with subsequent market behavior across fixed forward windows.

#### Scenario: Trailing take profit is evaluated after trigger
- **WHEN** a historical ETF path triggers `trailing_take_profit`
- **THEN** the system records the signal time, signal price, forward returns, maximum favorable excursion, maximum adverse excursion, and outcome classification for 1, 3, 5, 10, and 20 trading day windows

#### Scenario: Hard stop is evaluated after trigger
- **WHEN** a historical ETF path triggers `hard_stop`
- **THEN** the system records whether the signal avoided additional downside or was followed by a recovery that indicates a possible false stop

#### Scenario: Incomplete forward window is not scored as reliable
- **WHEN** a signal does not have enough future data for a required forward window
- **THEN** the system marks that window as `insufficient_future_window` rather than assigning a success or failure outcome

### Requirement: Exit signal credibility is aggregated by signal and ETF group
The system SHALL aggregate ETF exit signal outcomes into credibility metrics grouped by signal type, ETF, and theme or bucket when available.

#### Scenario: Signal summary is generated
- **WHEN** an exit signal credibility run completes
- **THEN** the system returns sample count, success avoidance rate, false stop rate, sold too early rate, average avoided drawdown, average missed upside, average forward return, and evidence level for each signal type

#### Scenario: Theme summary is generated
- **WHEN** ETF theme or bucket metadata is available
- **THEN** the system returns per-theme credibility metrics without mixing unrelated ETF categories into a single conclusion

#### Scenario: Sparse samples are marked as insufficient
- **WHEN** a signal group has fewer than the minimum required samples
- **THEN** the system marks the evidence level as `样本不足` and does not display the signal as reliable

### Requirement: Intraday alert evidence uses intraday data only
The system SHALL use historical ETF intraday quotes for `intraday_alert` exit credibility and MUST NOT replace missing intraday data with daily close prices.

#### Scenario: Intraday history exists
- **WHEN** intraday quotes cover the signal trigger and forward execution window
- **THEN** the system evaluates the signal using intraday prices and records `execution_model=intraday_alert`

#### Scenario: Intraday history is missing
- **WHEN** intraday quotes are unavailable for the required trigger or forward window
- **THEN** the system marks the event or run as intraday evidence insufficient and does not fall back to daily close data

#### Scenario: Daily close evidence is separate
- **WHEN** the system computes a daily-close version of exit signal evidence
- **THEN** the system labels it as `daily_close` and does not present it as proof for the live intraday email workflow

### Requirement: Exit credibility research does not affect live trading workflow
The system SHALL keep ETF exit signal credibility as research evidence only.

#### Scenario: Credibility run completes
- **WHEN** an exit signal credibility run completes
- **THEN** the system does not send email, does not write real alert records, does not modify tracked positions, and does not change live alert thresholds

#### Scenario: Candidate conclusion is displayed
- **WHEN** the UI displays an exit signal credibility conclusion
- **THEN** it labels the result as research evidence requiring manual judgment and not as an automatic sell instruction

### Requirement: Exit signal credibility is visible in strategy evidence UI
The system SHALL expose the latest ETF exit signal credibility report in the ETF strategy evidence page.

#### Scenario: Latest report is available
- **WHEN** the latest credibility report exists
- **THEN** the UI displays signal type, sample count, success avoidance rate, false stop rate, sold too early rate, average avoided drawdown, average missed upside, data window, execution model, and evidence status

#### Scenario: No report is available
- **WHEN** no credibility report exists for the current contract
- **THEN** the UI displays `等待验证` instead of implying that exit signals are historically reliable
