# etf-publish-readiness-sync Specification

## Purpose
TBD - created by archiving change accelerate-etf-publish-readiness-sync. Update Purpose after archive.
## Requirements
### Requirement: Post-close ETF publication readiness is coordinated by bounded continuations
The system SHALL coordinate post-close ETF publication by measuring target-session decision-data freshness and 61-session score warm-up coverage separately, running at most one bounded continuation when either coverage is below its gate, and generating a ranking snapshot only after both coverage ratios meet 95 percent.

#### Scenario: Daily freshness is below the gate
- **WHEN** fewer than 95 percent of the authoritative ETF universe have decision-eligible `total_return_adjusted` data for the target trade date
- **THEN** the coordinator runs one bounded publication-readiness slice and leaves publication in an explicit waiting state

#### Scenario: Daily freshness passes but warm-up does not
- **WHEN** target-date adjusted coverage is at least 95 percent but fewer than 95 percent of the universe have 61 eligible exchange sessions
- **THEN** the coordinator prioritizes warm-up gaps and MUST NOT generate degraded scores for incomplete ETFs

#### Scenario: Both coverage gates pass
- **WHEN** target-date adjusted coverage and 61-session score-eligible coverage are both at least 95 percent
- **THEN** the coordinator generates and publication-validates one full dual-ranking snapshot without running another synchronization slice

#### Scenario: Authoritative universe is unavailable
- **WHEN** the target trade date lacks a successful authoritative universe receipt
- **THEN** the coordinator records `universe_not_authoritative`, starts no history provider work, and publishes no ranking

### Requirement: Publication-readiness continuations are resource bounded
The system SHALL use one worker and one non-overlapping database lease, SHALL process no more than 20 ETF codes, 500 rows per page, 5,000 rows per slice, or 512 MiB process RSS, SHALL stop admitting provider work by 45 seconds, SHALL durably commit or roll back and checkpoint by 55 seconds, and SHALL return before a 60-second hard limit.

#### Scenario: A slice reaches an admission limit
- **WHEN** the slice reaches its code, row, RSS, provider, or 45-second admission budget
- **THEN** it starts no new provider request, finishes the current atomic page if safe, persists a durable partial checkpoint, and returns `partial`

#### Scenario: A previous slice is still running
- **WHEN** the scheduler triggers while a non-stale publication-readiness lease exists
- **THEN** the new invocation returns `overlapping_worker_lease` without starting a second worker

#### Scenario: The process stops before page commit
- **WHEN** a process is interrupted before a code/date page and its checkpoint are committed
- **THEN** the next compatible invocation reprocesses that page idempotently without skipping or duplicating code/date rows

#### Scenario: The server exceeds the RSS gate
- **WHEN** observed process RSS exceeds 512 MiB before a new provider request or page
- **THEN** the slice stops with `rss_limit`, persists the last complete checkpoint, and MUST NOT admit more work

### Requirement: One bounded fetch may improve independent readiness lanes
The system SHALL allow a target-date gap request to fetch a bounded date range covering the most recent 61 exchange sessions so the same provider response can persist both current and warm-up rows, but SHALL calculate daily freshness and warm-up completion independently from persisted decision-eligible data.

#### Scenario: One response contains current and historical adjusted rows
- **WHEN** an accepted provider returns valid target-date data and at least 61 eligible adjusted sessions for one ETF
- **THEN** persistence may improve both coverage ratios while each lane is independently recomputed from stored rows

#### Scenario: Historical depth exists without the target date
- **WHEN** an ETF has 61 eligible historical sessions but no eligible row for the target trade date
- **THEN** warm-up may be complete while daily freshness remains pending

#### Scenario: Target-date data exists without warm-up
- **WHEN** an ETF has an eligible target-date row but fewer than 61 eligible exchange sessions
- **THEN** daily freshness is complete while warm-up remains pending and the ETF is not score-eligible

### Requirement: Continuation identity and progress are durable
The system SHALL bind a continuation checkpoint to target trade date, requested date range, ordered authoritative universe and hash, ranking/history contract hash, provider policy version, adjustment contract, price basis, and resource profile, and SHALL atomically persist the last complete page and progress counters.

#### Scenario: Compatible continuation resumes
- **WHEN** a later invocation presents the same continuation identity
- **THEN** it resumes after the last attempted or completed rotation point and derives remaining gaps from indexed persisted data

#### Scenario: Continuation identity changes
- **WHEN** the universe, target date, contract, provider policy, price basis, adjustment version, or resource profile differs from the checkpoint identity
- **THEN** the system starts a new continuation identity and MUST NOT silently advance the incompatible cursor

#### Scenario: A provider failure is recorded
- **WHEN** an accepted provider times out, returns invalid provenance, or fails for one ETF
- **THEN** the checkpoint records a bounded reason, rotates to other candidates, and preserves a retry cooldown without marking the ETF complete

### Requirement: Catch-up scheduling adapts without concurrency
The system SHALL schedule bounded publication-readiness slices only during the configured post-close catch-up window, SHALL use a conservative initial profile before enabling the maximum profile, and SHALL stop catch-up scheduling after publication succeeds or a hard prerequisite becomes unavailable.

#### Scenario: Conservative rollout begins
- **WHEN** the new continuation is first enabled in production
- **THEN** it uses at most 10 codes and a five-minute cadence until real slices demonstrate acceptable elapsed time, RSS, provider errors, and monotonic checkpoints

#### Scenario: Maximum profile is enabled
- **WHEN** production evidence satisfies the configured performance and provider-health gates
- **THEN** the scheduler may use at most 20 codes and a two-minute cadence while retaining one worker and all hard resource limits

#### Scenario: Resource or provider health degrades
- **WHEN** elapsed time, RSS, rate limiting, circuit state, or checkpoint health exceeds its configured gate
- **THEN** the scheduler returns to the conservative profile rather than increasing concurrency or extending the 60-second limit

#### Scenario: Ranking is published
- **WHEN** a valid target-date dual-ranking snapshot is published
- **THEN** subsequent catch-up triggers for that trade date skip provider work

### Requirement: Publication-readiness telemetry is compact and auditable
The system SHALL persist counts, ratios, reason aggregates, bounded code samples, provider health, durable checkpoint, elapsed time, peak RSS, rows per second, and remaining estimates without storing repeated full-universe code arrays in every job result.

#### Scenario: Coverage remains below the gate
- **WHEN** a slice finishes while either coverage ratio is below 95 percent
- **THEN** its JobRun stores compact coverage and progress telemetry plus at most a bounded sample of excluded codes

#### Scenario: Publication validation begins
- **WHEN** compact aggregate coverage reaches the publication threshold
- **THEN** the system constructs the complete expected and eligible code sets only for final publication validation

#### Scenario: An administrator inspects progress
- **WHEN** the admin API returns publication-readiness status
- **THEN** it reports the two coverage ratios, remaining counts, ETA inputs, latest stop reason, provider health, checkpoint identity, and resource peaks without expanding all universe members by default
