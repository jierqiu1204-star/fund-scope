## ADDED Requirements

### Requirement: ETF exit calibration runs parameter search
The system SHALL calibrate ETF exit-rule parameters by replaying historical ETF data over bounded search spaces.

#### Scenario: Calibration searches configured parameter space
- **WHEN** an ETF exit calibration run starts
- **THEN** the system evaluates configured combinations of hard-stop, take-profit-watch, trailing-start, trailing-giveback, and trend-confirmation parameters

#### Scenario: Calibration is bucketed
- **WHEN** ETF assets have type, theme, sector, or volatility bucket metadata
- **THEN** the system groups candidates by bucket and does not force one global parameter set onto all ETF types

#### Scenario: Calibration lacks enough bucket samples
- **WHEN** a bucket has fewer than the configured minimum completed samples
- **THEN** the system marks the bucket as evidence-insufficient and MUST NOT produce an approved parameter set for that bucket

### Requirement: ETF exit calibration uses no-lookahead replay
The system SHALL replay ETF exit rules using only data available at or before each simulated decision timestamp.

#### Scenario: Intraday alert replay uses past quotes only
- **WHEN** the calibration replay evaluates an intraday exit signal
- **THEN** it uses only quotes and state available at or before the simulated signal time

#### Scenario: Future price is excluded from signal calculation
- **WHEN** a metric would require prices after the simulated signal timestamp
- **THEN** the calibration excludes that metric from the signal decision and records a no-lookahead exclusion reason

#### Scenario: Missing intraday history is not replaced by daily close
- **WHEN** intraday quotes are unavailable for an intraday calibration window
- **THEN** the system marks that sample as missing intraday evidence and MUST NOT substitute daily close as an intraday trigger price

### Requirement: ETF exit calibration validates out-of-sample performance
The system SHALL split calibration evidence into training, out-of-sample, and rolling validation results.

#### Scenario: Candidate includes validation windows
- **WHEN** a parameter candidate is stored
- **THEN** it includes training range, out-of-sample range, rolling window summary, and data cutoff time

#### Scenario: Out-of-sample deterioration rejects candidate
- **WHEN** a candidate performs materially worse out-of-sample than in training
- **THEN** the candidate is marked rejected or evidence-insufficient and MUST NOT be auto-approved

#### Scenario: Rolling window reports inconsistent behavior
- **WHEN** rolling windows show materially unstable results
- **THEN** the candidate records the instability and lowers its confidence state

### Requirement: ETF exit calibration scores robustness instead of raw profit only
The system SHALL score parameter candidates with a robustness objective that includes drawdown, false exits, missed upside, email frequency, turnover, and sample sufficiency.

#### Scenario: Candidate has high false-exit rate
- **WHEN** a parameter candidate frequently exits before material rebound or missed upside
- **THEN** its score is penalized even if sample-period profit is positive

#### Scenario: Candidate sends too many emails
- **WHEN** a parameter candidate would generate excessive alerts compared with the configured frequency limit
- **THEN** its score is penalized for alert noise

#### Scenario: Candidate improves drawdown but sacrifices all participation
- **WHEN** a candidate avoids drawdown only by staying out of most opportunities
- **THEN** the objective records low participation and does not rank it as robust solely for low drawdown

### Requirement: ETF exit calibration applies confidence shrinkage
The system SHALL calibrate raw success and false-exit rates using conservative shrinkage when sample counts are limited.

#### Scenario: Low sample high success is shrunk
- **WHEN** a parameter candidate has high raw success but low sample count
- **THEN** the reported calibrated success rate is pulled toward the configured prior rate

#### Scenario: Sufficient samples rely more on observed outcomes
- **WHEN** a parameter candidate has enough completed samples
- **THEN** the calibrated rate is closer to the observed outcome rate

### Requirement: ETF exit calibration results are research-only by default
The system SHALL keep calibration results separate from live alerts until a parameter set is explicitly approved.

#### Scenario: Candidate is created
- **WHEN** a calibration run finds a high-scoring parameter set
- **THEN** the system stores it as candidate and MUST NOT change live email thresholds

#### Scenario: Calibration task completes
- **WHEN** the calibration task finishes successfully
- **THEN** it MUST NOT create tracked-position alerts, send notification emails, or mutate tracked positions

#### Scenario: Approved parameter exists
- **WHEN** a parameter set has status approved and matches the current contract
- **THEN** downstream risk evaluation may read that parameter version through the approved-parameter lookup path

### Requirement: ETF exit calibration is auditable
The system SHALL persist calibration runs, parameter candidates, validation metrics, rule versions, and data quality context.

#### Scenario: Calibration run persists metadata
- **WHEN** a calibration run is created
- **THEN** it records rule versions, data window, data cutoff, execution model, searched buckets, candidate count, status, and error message when applicable

#### Scenario: Candidate persists metrics
- **WHEN** a candidate parameter set is stored
- **THEN** it records parameter values, bucket key, sample count, return metrics, drawdown metrics, false-exit metrics, missed-upside metrics, alert count, turnover, score, confidence state, and status

#### Scenario: Candidate references source data reliability
- **WHEN** a candidate is produced from replayed data
- **THEN** it records whether the data was verified, alternate-provider verified, stale, estimated, or unavailable, and excludes ineligible data from decision metrics

### Requirement: ETF exit calibration exposes evidence through API
The system SHALL expose calibration status and latest candidate evidence through read-only API endpoints and admin job output.

#### Scenario: User reads latest calibration
- **WHEN** an approved user opens the ETF strategy evidence page
- **THEN** the system returns current approved parameters, candidate parameters, validation summaries, confidence state, and last run time

#### Scenario: Admin manually runs calibration
- **WHEN** an admin triggers the ETF exit calibration job
- **THEN** the system runs calibration, records a job result, and returns a structured summary without sending emails

#### Scenario: No calibration exists
- **WHEN** no completed calibration run exists
- **THEN** the API returns an empty evidence state with a clear waiting status rather than fabricating parameters
