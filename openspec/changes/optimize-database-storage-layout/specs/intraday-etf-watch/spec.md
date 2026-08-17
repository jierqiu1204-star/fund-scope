## MODIFIED Requirements

### Requirement: Intraday Quote Retention Is Bounded
The system SHALL keep complete unprotected intraday ETF quote details for the latest 10 trading sessions, preserve every quote referenced by immutable decision evidence without age expiry, and remove only older unprotected rows through a bounded scheduled or manually runnable cleanup job.

#### Scenario: Cleanup removes old raw intraday rows
- **WHEN** the cleanup job runs after the market day and evidence sealing has completed
- **THEN** rows older than the 10-trading-session cutoff that have no evidence reference are summarized and deleted in bounded batches

#### Scenario: Cleanup preserves protected quote evidence
- **WHEN** a quote older than the full-detail window is referenced by `etf_intraday_quote_evidence_refs`
- **THEN** the quote row, provider payload, receipt time, quote time, and evidence hash inputs remain available

#### Scenario: Evidence sealing is incomplete
- **WHEN** any eligible published snapshot lacks complete protected or explicitly unavailable evidence
- **THEN** cleanup fails closed, reports the blocking snapshot, and deletes no candidate quote rows

#### Scenario: Cleanup does not remove decision history
- **WHEN** old unprotected intraday rows are deleted
- **THEN** protected quote evidence, daily ETF price history, signal cache, alert records, persisted holding high-water state, and daily intraday summaries remain available

### Requirement: Intraday Daily Summary Is Preserved
The system SHALL preserve one durable daily intraday summary per ETF and trading day before raw unprotected intraday rows are removed.

#### Scenario: Daily summary records key intraday facts
- **WHEN** intraday quotes exist for an ETF trading day eligible for cleanup
- **THEN** the system stores or updates one daily summary containing last price, high, low, turnover, quote count, and data source before deleting any source rows

#### Scenario: Historical analysis can use summaries
- **WHEN** raw minute-level quotes are beyond the full-detail window
- **THEN** the system can display daily intraday summary information without using stale raw rows for live decisions

#### Scenario: Cleanup progress is observable
- **WHEN** a retention slice completes or stops at its time budget
- **THEN** the result reports cutoff date, protected evidence status, summarized groups, deleted rows, slice durations, next batch size, and a stable unavailable reason when incomplete
