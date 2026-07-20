## MODIFIED Requirements

### Requirement: ETF Data Sync Uses Provider Fallback
The system SHALL use an ordered policy of accepted adjusted-price providers, SHALL bound each provider attempt independently within the remaining slice budget, and SHALL attempt another accepted provider only when enough budget remains and the result can carry complete `total_return_adjusted` provenance.

#### Scenario: Backup adjusted provider fills a primary-source failure
- **WHEN** the preferred adjusted-price provider fails, times out, is cooling down, or returns no usable rows for one ETF
- **THEN** the system attempts the next accepted adjusted provider within the ETF and slice budgets before marking that ETF attempt incomplete

#### Scenario: One ETF failure does not block the universe
- **WHEN** all accepted adjusted providers fail for one ETF
- **THEN** the sync result records that ETF code and readable bounded failure reason, rotates progress, and continues with remaining candidates while resource budgets allow

#### Scenario: Raw-only fallback succeeds
- **WHEN** Sina, efinance, an intraday snapshot, or another provider returns raw prices without complete adjusted value, price basis, provider version, adjustment version, and source timestamp
- **THEN** the system may record display-only health but MUST NOT mark the row decision-eligible or increase publication coverage

#### Scenario: Provider attempt exhausts its budget
- **WHEN** one provider reaches its per-attempt timeout
- **THEN** the attempt is cancelled without consuming the entire continuation budget, and fallback is attempted only if the remaining admission and checkpoint reserve is sufficient

#### Scenario: Provider repeatedly fails
- **WHEN** a provider reaches the configured consecutive failure threshold across bounded slices
- **THEN** the system persists a retry cooldown and skips that provider until `retry_after` rather than retrying it for every ETF

## ADDED Requirements

### Requirement: Adjusted-price provider connections and health are slice scoped
The system SHALL reuse a bounded connection pool within one synchronization slice, SHALL close it when the slice ends, and SHALL expose provider attempts, successes, timeouts, cooldowns, fallback counts, latency, and accepted-provenance counts in compact health telemetry.

#### Scenario: Multiple ETFs use the same HTTP provider
- **WHEN** one slice requests adjusted history for multiple ETFs from the same HTTP provider
- **THEN** it reuses the slice connection pool without creating an unbounded number of connections

#### Scenario: A slice ends
- **WHEN** a synchronization slice completes, fails, reaches a resource limit, or is cancelled
- **THEN** provider clients and in-flight requests are closed or cancelled before the 60-second process limit

#### Scenario: Provider health is inspected
- **WHEN** an administrator inspects the latest publication-readiness task
- **THEN** the response distinguishes accepted adjusted successes, raw-only results, provider errors, timeouts, cooldown state, and fallback usage
