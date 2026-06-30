## ADDED Requirements

### Requirement: ETF daily history can be backfilled for long research windows
The system SHALL provide ETF daily-history backfill for configurable long ranges so that research, validation, portfolio optimization, and backtests can use verified daily data beyond the short daily refresh window.

#### Scenario: Backfill 730 days
- **WHEN** an administrator runs the ETF daily-history backfill for `730` days
- **THEN** the system fetches verified daily ETF OHLC/turnover data for eligible ETFs over that range, upserts rows by ETF code and trade date, and reports inserted, updated, skipped, and failed counts

#### Scenario: Repeat backfill is idempotent
- **WHEN** the same ETF daily-history backfill range is run more than once
- **THEN** existing rows are updated or left unchanged without duplicate `etf_code + trade_date` records

#### Scenario: Provider cannot supply full range
- **WHEN** the provider returns less than the requested history for an ETF
- **THEN** the system records available verified rows and reports the coverage gap instead of fabricating missing prices

### Requirement: Long backfill is separate from lightweight daily refresh
The system SHALL keep regular daily ETF refresh jobs lightweight while exposing explicit long-history backfill jobs for research readiness.

#### Scenario: Daily refresh runs
- **WHEN** the scheduled daily ETF data job runs
- **THEN** it refreshes the configured recent window and does not automatically run the full long-history backfill

#### Scenario: Admin triggers long backfill
- **WHEN** the admin job for `365`, `730`, or `1095` day ETF history is triggered
- **THEN** the system runs the long-history path and writes a job result that includes requested range, asset count, success count, failure count, and coverage summary

### Requirement: Backfill completion can refresh downstream ETF research
The system SHALL allow ETF history backfill to be followed by signal generation, observation portfolio generation, and backtest execution.

#### Scenario: Deployment backfill workflow
- **WHEN** the deployment workflow runs the recommended `730` day ETF history backfill
- **THEN** it can subsequently regenerate ETF signals, regenerate the ETF observation portfolio, and run an ETF portfolio backtest using the expanded history
