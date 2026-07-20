## ADDED Requirements

### Requirement: ETF synchronization separates daily freshness from historical depth
The system SHALL maintain independent synchronization lanes and cursors for target-trade-date freshness, minimum 61-session score warm-up depth, and contract-derived validation/replay depth using decision-eligible `total_return_adjusted` exchange sessions. A fixed 180-session depth MAY be reported as operational telemetry but MUST NOT stand in for contract-derived readiness.

#### Scenario: ETF has today's row but insufficient warm-up
- **WHEN** an ETF has an eligible adjusted row for the target trade date but fewer than the required warm-up sessions
- **THEN** the daily freshness lane marks it current while the history-depth lane keeps it pending

#### Scenario: Raw rows do not satisfy depth
- **WHEN** an ETF has raw, fallback, duplicate, non-finite, or decision-ineligible historical rows
- **THEN** those rows MUST NOT increase its adjusted-history depth coverage

#### Scenario: One lane advances
- **WHEN** a synchronization slice completes work for one lane
- **THEN** it advances only that lane's cursor and MUST NOT mark another lane complete

#### Scenario: Main replay endpoint requests sufficient depth
- **WHEN** the registered primary replay endpoint requires a 61-session warm-up and 20 non-overlapping five-session samples
- **THEN** the derived depth requires at least 200 exchange sessions, and a request including the ten-session horizon requires at least 300 exchange sessions, subject to additional exclusions and actual source availability

#### Scenario: Warm-up coverage is below the publication gate
- **WHEN** an ETF has current adjusted data but fewer than 61 eligible sessions
- **THEN** that ETF is not score-eligible, is excluded with an explicit warm-up reason, and a full v3 run may publish only if its remaining decision-data and score-eligible coverage ratios both meet the registered 95 percent gates; no degraded score is synthesized

### Requirement: ETF historical synchronization is bounded and resumable
The system SHALL process historical adjusted-price gaps with one worker, stable code/date pages, provider timeouts, bounded rows and memory, atomic checkpoints, and a hard continuation deadline no greater than 55 seconds. Under the 2-core/4-GB acceptance profile, a slice SHALL stop admitting provider work by 45 seconds, use no more than 10 codes, 500 rows per page, 5,000 fetched rows, or 768 MiB process RSS, and reserve the remaining time for cancellation, commit/rollback, checkpoint, and process exit before the 60-second command limit.

#### Scenario: Continuation reaches its deadline
- **WHEN** a history continuation reaches its time or row budget after committing one or more pages
- **THEN** it returns `partial`, persists the last complete checkpoint, and resumes idempotently from the next unit

#### Scenario: Provider work reaches the admission cutoff
- **WHEN** elapsed time reaches 45 seconds or the remaining budget cannot contain the provider timeout and safety reserve
- **THEN** the worker starts no new provider request, cancels bounded in-flight work, leaves no orphan process, and either atomically commits the complete page/checkpoint or rolls back both

#### Scenario: Continuation is interrupted before page commit
- **WHEN** a process stops before a code/date page and checkpoint commit completes
- **THEN** retrying the continuation reprocesses that page without skipping rows or duplicating code/date records

#### Scenario: Contract changes during resume
- **WHEN** universe, date range, provider policy, price basis, or adjustment-contract hash differs from the checkpoint
- **THEN** the system rejects the incompatible resume and requires a new backfill identity

### Requirement: Historical persistence avoids per-row database amplification
The system SHALL load existing code/date keys once per bounded page, persist inserts and updates in batches, commit small pages, and calculate derived metrics from a bounded trailing window after a code is complete.

#### Scenario: One history page is persisted
- **WHEN** a provider returns a bounded page of historical rows for one ETF
- **THEN** persistence uses no more than eight database statements for the page independent of its row count and records inserted, updated, unchanged, and excluded counts

#### Scenario: Metric recomputation follows a partial code
- **WHEN** only part of an ETF's requested history is committed
- **THEN** the system waits to mark the requested depth complete and does not recompute final warm-up readiness from an incomplete target range

### Requirement: Eligible universe and provider fetch scope cannot drift silently
The system SHALL reconcile the authoritative eligible universe with the provider fetch scope before counting a synchronization batch as processed.

#### Scenario: Eligible ETF is absent from fetch scope
- **WHEN** an eligible ETF is excluded by a watchlist or provider-scope filter before any request is attempted
- **THEN** the result records `eligible_fetch_scope_mismatch`, does not count the ETF as processed, and does not advance past it silently
