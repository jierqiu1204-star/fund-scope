## ADDED Requirements

### Requirement: MVP Spec Coverage Is Complete
The system SHALL have automated tests covering every scenario in the MVP portfolio-tracking and valuation-monitoring specs before the MVP change is archived.

#### Scenario: Portfolio spec scenarios are covered
- **WHEN** the backend test suite runs
- **THEN** it includes tests for transaction recording, sell transactions, unknown funds, holdings with latest NAV, stale NAV flags, value history, allocation data, CSV success, CSV atomic rejection, snapshot idempotency, and default portfolio seeding

#### Scenario: Valuation spec scenarios are covered
- **WHEN** the backend test suite runs
- **THEN** it includes tests for default index seed data, index watchlist add/remove behavior, successful valuation fetch, fallback valuation fetch, failed valuation handling, rolling percentile calculation, current valuation API output, and historical valuation API sorting

### Requirement: External Data Fallbacks Are Deterministic
The system SHALL test fund and index data-source fallback behavior with local stubs or recorded fixtures so CI does not require live market-data access.

#### Scenario: Fund NAV fallback is tested without live network dependency
- **WHEN** the fund data tests run
- **THEN** they verify parser and fallback behavior using local fixtures or mocked HTTP responses

#### Scenario: Index valuation fallback is tested without live network dependency
- **WHEN** the index data tests run
- **THEN** they verify retry-to-fallback behavior using stubs instead of live AKShare or website access

### Requirement: Valuation Watchlist Behavior Matches Spec
The system SHALL support index watchlist additions and removals consistently with the valuation-monitoring spec.

#### Scenario: Add index to watchlist
- **WHEN** a valid index code is submitted through the watchlist API
- **THEN** the system inserts or reactivates the index with `is_watchlist=true` and records enough metadata for future valuation jobs to include it

#### Scenario: Remove index from watchlist
- **WHEN** an index is removed from the watchlist
- **THEN** the system sets `is_watchlist=false` and retains existing historical valuation rows

### Requirement: Local Full-Stack Verification Is Reproducible
The system SHALL provide and execute a local full-stack verification path for the MVP using Docker Compose or document concrete blockers and fixes.

#### Scenario: Docker Compose verification succeeds
- **WHEN** Docker is available locally
- **THEN** the full stack starts with backend, frontend static output, Postgres, and nginx, and health/API/UI smoke checks pass

#### Scenario: Docker Compose verification is blocked
- **WHEN** Docker is unavailable or a local environment blocker prevents startup
- **THEN** the verification record names the blocker, the failing command, and the exact next action needed

### Requirement: External Deployment Checklist Is Explicit
The system SHALL document all external deployment prerequisites without storing private values in the repository.

#### Scenario: Secrets checklist is documented
- **WHEN** a user prepares CI/CD deployment
- **THEN** the docs list required secret names, where they are configured, and how to verify they are present

#### Scenario: VPS deployment checks are documented
- **WHEN** a user deploys to VPS
- **THEN** the docs include commands to verify HTTPS, basic auth, backend health, nginx proxying, scheduled jobs, and database backups

### Requirement: MVP Archive Readiness Is Auditable
The system SHALL leave a short verification record that maps the remaining `add-fundscope-mvp` tasks to evidence.

#### Scenario: Verification evidence is recorded
- **WHEN** the MVP verification pass completes
- **THEN** the repository contains a concise checklist or README section listing the commands run, manual checks performed, unresolved external dependencies, and screenshots captured or generated

#### Scenario: MVP tasks are updated
- **WHEN** evidence exists for a remaining `add-fundscope-mvp` task
- **THEN** that task is marked complete in the MVP task file, with external-only tasks handled by documented checklist evidence rather than hidden assumptions
