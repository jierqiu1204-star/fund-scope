## MODIFIED Requirements

### Requirement: ETF Data Sync Uses Provider Fallback
The system SHALL use AKShare as the primary ETF daily-price source and SHALL attempt a configured backup public provider when the primary source fails or returns no usable rows, provided that the backup result has traceable trade date, price basis, source timestamp, and decision-eligibility metadata.

#### Scenario: Backup provider fills a primary-source failure
- **WHEN** AKShare fails for one ETF during short ETF data synchronization and the backup returns compatible decision-eligible data
- **THEN** the system stores the backup result with its provider and price-basis metadata before considering the ETF synchronized

#### Scenario: One ETF failure does not block the universe sync process
- **WHEN** both providers fail for one ETF during synchronization
- **THEN** the sync records that ETF and readable failure reason while processing the remaining ETFs, but canonical ranking publication still obeys its coverage barrier

#### Scenario: Backup data uses an incompatible basis
- **WHEN** a provider returns raw or estimated data that cannot satisfy the current research price-basis contract
- **THEN** the data remains display-only and does not count as successful decision-data coverage

### Requirement: ETF Data Health Is Visible
The system SHALL maintain per-ETF and per-run data health covering latest raw and research trade dates, providers, price basis, adjustment version, successful row count, decision eligibility, latest failure reason, consecutive failure count, skipped/deferred status, and expected-universe coverage.

#### Scenario: Data health updates after sync
- **WHEN** ETF data synchronization finishes
- **THEN** each expected ETF has health status reflecting success, compatible provider fallback, display-only data, skip, defer, or failure and the run reports whether publication coverage passed

#### Scenario: Data health is shown in the web UI
- **WHEN** the user opens the short ETF tab or admin jobs page
- **THEN** the UI shows trade-date coverage, price basis, stale/display-only warnings, provider fallback counts, skipped/deferred counts, and failed ETF codes in Chinese

#### Scenario: Frontend requests issue details
- **WHEN** the workbench displays a non-zero data issue count
- **THEN** the corresponding health detail is included or retrievable and is not silently returned as an empty collection

## ADDED Requirements

### Requirement: Bounded ETF Sync Makes Persistent Forward Progress
The system SHALL order bounded daily sync batches by missing/stale priority and a persisted rotation cursor so every eligible ETF is eventually attempted.

#### Scenario: Universe exceeds one batch
- **WHEN** the eligible universe contains more ETFs than one configured batch
- **THEN** repeated scheduled runs advance the cursor and do not repeatedly process only the same first batch

#### Scenario: High-priority ETF becomes stale
- **WHEN** a tracked or default-display ETF becomes stale
- **THEN** it may move ahead of the cursor for repair without permanently starving the remaining universe

### Requirement: Research Job Status Reflects Business Completion
The scheduler and job runner SHALL map domain results to `success`, `partial`, `failed`, or `skipped` based on synchronization, coverage, and publication outcomes rather than treating every normal function return as success.

#### Scenario: Provider task returns a partial payload
- **WHEN** the task function returns normally but required batches were skipped or coverage failed
- **THEN** the persisted job status is partial or failed and no full ranking snapshot is advertised

#### Scenario: Market-dependent task is intentionally skipped
- **WHEN** a task is outside its eligible exchange session
- **THEN** the job records an explicit skipped reason rather than a misleading successful data refresh

### Requirement: Daily Ranking Cache Has A Trading-Calendar Freshness Gate
The system SHALL expire canonical ranking eligibility using the exchange trading calendar and SHALL expose stale or waiting state when scheduled generation stops or fails.

#### Scenario: Last snapshot predates the required trade date
- **WHEN** the exchange has completed a newer trading day but no compatible snapshot was published
- **THEN** the old snapshot is not selected as current and the UI receives its exact stale date and limitation

### Requirement: Supporting Research Data Jobs Are Scheduled And Auditable
ETF universe refresh, theme/catalyst refresh, daily-data repair, and intraday retention cleanup SHALL have explicit scheduler or manually runnable registrations with last-run status and freshness.

#### Scenario: Supporting job has not run within its freshness target
- **WHEN** a ranking dependency is overdue
- **THEN** health output identifies the overdue dependency and affected score components remain unavailable
