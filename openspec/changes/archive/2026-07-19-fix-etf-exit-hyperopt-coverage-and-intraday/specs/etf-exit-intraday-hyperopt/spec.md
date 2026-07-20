## ADDED Requirements

### Requirement: Hyperopt Covers The Eligible ETF Universe
The system SHALL optimize ETF exit-rule parameters against the full short-term eligible ETF universe by default and SHALL record a coverage funnel for every run.

#### Scenario: Full default coverage
- **WHEN** the nightly ETF exit Hyperopt job runs without an explicit `max_assets` limit
- **THEN** the system evaluates all short-term eligible ETFs that satisfy the required data coverage rules

#### Scenario: Coverage funnel is recorded
- **WHEN** an ETF exit Hyperopt run completes
- **THEN** the run summary includes all ETF count, eligible ETF count, enough daily history count, enough intraday history count, final optimized count, sampled flag, and exclusion reasons

#### Scenario: Explicit sample is marked
- **WHEN** an admin manually runs ETF exit Hyperopt with a smaller `max_assets` value
- **THEN** the run summary marks the result as sampled and MUST NOT present it as full-universe evidence

### Requirement: Hyperopt Uses Intraday Alert Replay
The system SHALL use `intraday_alert` as the primary ETF exit Hyperopt execution model and SHALL replay the live email workflow using historical fresh intraday quotes.

#### Scenario: Intraday signal replay
- **WHEN** historical intraday quotes are available for an ETF
- **THEN** the optimizer evaluates theoretical hard stop, trailing take-profit, trend weakening, take-profit watch, and exit-watch signals using only quote data available at or before each signal time

#### Scenario: Delayed manual execution
- **WHEN** a theoretical intraday exit signal is triggered
- **THEN** the optimizer simulates execution after the configured manual delay and fills at the next available eligible intraday quote

#### Scenario: Missing fill is not replaced by daily close
- **WHEN** no eligible intraday quote exists after the simulated manual delay
- **THEN** the event is marked unfilled and the system MUST NOT use daily close as a replacement execution price

#### Scenario: Daily-close model remains separate
- **WHEN** a daily-close optimization or backtest is run
- **THEN** its result is labeled `daily_close` and MUST NOT be used as evidence that the intraday email strategy is optimized

### Requirement: Hyperopt Compares Candidates Against Baseline
The system SHALL compare each optimized parameter candidate against the current live default ETF exit rule before marking the candidate usable.

#### Scenario: Candidate beats baseline
- **WHEN** a candidate parameter set has sufficient samples and outperforms the current baseline on out-of-sample robustness metrics
- **THEN** the system may mark it as `candidate` for manual review

#### Scenario: Candidate fails baseline comparison
- **WHEN** a candidate has lower out-of-sample robustness, higher unacceptable drawdown, more false exits, or excessive email frequency compared with baseline
- **THEN** the system marks it `rejected` with the rejection reason

#### Scenario: Evidence is insufficient
- **WHEN** a bucket lacks enough intraday signal samples, executable events, or rolling windows
- **THEN** the system marks the bucket `evidence_insufficient` and keeps current live defaults

### Requirement: Hyperopt Remains Research-Only
The ETF exit Hyperopt engine SHALL never send emails, mutate tracked positions, or automatically change live exit rules.

#### Scenario: Research run completes
- **WHEN** ETF exit Hyperopt generates candidate parameters
- **THEN** the system persists research evidence and MUST NOT create tracked-position alerts, notification logs, or position mutations

#### Scenario: Candidate is not approved
- **WHEN** a candidate status is not `approved`
- **THEN** live tracked-position exit evaluations continue using the current approved or default rule
