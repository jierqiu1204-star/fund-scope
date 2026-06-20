## ADDED Requirements

### Requirement: Intraday Watch Reads Only Latest Quote Per ETF
The system SHALL retrieve the latest intraday quote for each watched ETF in SQL without loading all historical intraday quote rows into application memory.

#### Scenario: Live ranking fetches latest quotes
- **WHEN** the live ETF ranking is built for a watchlist of ETF codes
- **THEN** the backend returns at most one latest quote row per ETF code from the database query

#### Scenario: Query remains efficient as quote history grows
- **WHEN** `etf_intraday_quotes` contains many days of minute-level rows
- **THEN** the latest quote query uses indexed per-code lookup and does not perform a full table scan plus application-side de-duplication

### Requirement: Intraday Quote Retention Is Bounded
The system SHALL keep raw intraday ETF quote details for the latest 60 trading days and remove older raw intraday rows through a scheduled or manually runnable cleanup job.

#### Scenario: Cleanup removes old raw intraday rows
- **WHEN** the cleanup job runs after the market day
- **THEN** rows older than the 60-trading-day cutoff are deleted from `etf_intraday_quotes`

#### Scenario: Cleanup does not remove decision history
- **WHEN** raw intraday rows are deleted
- **THEN** daily ETF price history, signal cache, alert records, and persisted holding high-water state remain available

### Requirement: Intraday Daily Summary Is Preserved
The system SHALL preserve daily intraday summaries for ETF analysis before or while raw intraday rows are removed.

#### Scenario: Daily summary records key intraday facts
- **WHEN** intraday quotes exist for an ETF trading day
- **THEN** the system stores or updates one daily summary containing last price, high, low, turnover, quote count, and data source

#### Scenario: Historical analysis can use summaries
- **WHEN** raw minute-level quotes are beyond the retention window
- **THEN** the system can still display daily intraday summary information without using stale raw rows for live decisions
