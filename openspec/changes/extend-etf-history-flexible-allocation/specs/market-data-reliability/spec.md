## ADDED Requirements

### Requirement: Long ETF history is decision-eligible only when verified
The system SHALL apply the same market-data reliability rules to long-history ETF backfill data as it applies to recent daily and intraday data.

#### Scenario: Verified daily row is backfilled
- **WHEN** a backfilled ETF daily row has valid date, price fields, provider source, and passes sanity checks
- **THEN** it may be used for research signals, portfolio weights, label validation, and backtesting

#### Scenario: Backfilled row is incomplete
- **WHEN** a provider returns missing close price, invalid date, or non-positive price
- **THEN** the row is skipped or marked unusable and MUST NOT contribute to scores, portfolio weights, or backtest trades

#### Scenario: Coverage gap exists
- **WHEN** an ETF lacks verified data for part of the requested backfill range
- **THEN** the system records a coverage gap and downstream research treats that interval as unavailable rather than filling prices

### Requirement: Long-history backfill reports coverage
The system SHALL expose coverage summaries for long ETF history backfills.

#### Scenario: Backfill completes
- **WHEN** a long ETF history backfill job finishes
- **THEN** the job result includes earliest date, latest date, row count, ETF count, failed ETF count, and coverage limitations
