## MODIFIED Requirements

### Requirement: ETF Market Data Is Synchronizable

The system SHALL synchronize ETF daily market data and accepted adjusted
research values for the dynamic short-term ETF universe through prioritized,
bounded, resumable lanes.

#### Scenario: Data sync stores daily prices

- **WHEN** a bounded ETF data synchronization slice accepts provider rows
- **THEN** the system idempotently stores code/date records and preserves
  stronger decision-eligible adjusted provenance over weaker raw-only data

#### Scenario: Data source failure is reported

- **WHEN** an adjusted data source fails for one ETF
- **THEN** the slice records a bounded readable failure reason and continues
  within its code, row, memory, and time budgets

#### Scenario: Large universe sync is batched

- **WHEN** the authoritative ETF universe requires additional adjusted history
- **THEN** one worker processes 5 to 20 ETFs with durable rotation, 500-row
  pages, at most 5,000 rows, at most 512 MiB RSS, and a 60-second hard return

#### Scenario: Publication data retains priority

- **WHEN** target-date adjusted coverage is below 95 percent or 61-session
  adjusted coverage is below 90 percent
- **THEN** no 300-session or 500-session research-depth provider work starts

#### Scenario: Research history accumulates after publication readiness

- **WHEN** both publication gates pass
- **THEN** scheduled serial slices advance 300 adjusted sessions first and only
  then advance non-authoritative 500-session telemetry
- **AND** both research-depth completion gates remain 95 percent

#### Scenario: Continuation is resumed

- **WHEN** the adaptive execution profile changes after a partial or degraded
  slice
- **THEN** the next slice resumes from the durable scope cursor without
  restarting the universe
