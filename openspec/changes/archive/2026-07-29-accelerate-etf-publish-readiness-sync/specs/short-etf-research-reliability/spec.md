## MODIFIED Requirements

### Requirement: ETF Data Sync Uses Provider Fallback
The system SHALL use an ordered policy of accepted adjusted-price providers, SHALL bound each provider attempt independently within the remaining slice budget, and SHALL attempt another accepted provider only when enough budget remains and the result can carry complete `total_return_adjusted` provenance.

#### Scenario: Backup provider fills a primary-source failure
- **WHEN** the primary provenance-valid adjusted provider fails or returns no usable adjusted rows for one ETF
- **THEN** the bounded provider chain attempts the next configured adjusted provider before marking that ETF failed

#### Scenario: Backup adjusted provider fills a primary-source failure
- **WHEN** the preferred adjusted-price provider fails, times out, is cooling down, or returns no usable rows for one ETF
- **THEN** the system attempts the next accepted adjusted provider within the ETF and slice budgets before marking that ETF attempt incomplete

#### Scenario: One ETF failure does not block the universe
- **WHEN** all accepted adjusted providers fail for one ETF
- **THEN** the sync result records that ETF code and readable bounded failure reason, rotates progress, and continues with remaining candidates while resource budgets allow

#### Scenario: Adjusted fallback fills a provider failure
- **WHEN** the first adjusted provider times out, fails, or returns no valid adjusted rows
- **THEN** the bounded provider chain may try the next configured adjusted provider within its per-attempt and slice deadlines

#### Scenario: Eastmoney hosts are unreachable
- **WHEN** Eastmoney and efinance cannot reach their shared history host
- **THEN** an independently hosted Tencent raw-plus-hfq response may satisfy the adjusted lane only under its explicit provider version and existing per-row provenance checks

#### Scenario: Primary adjusted history is shallower than the lane target
- **WHEN** the first provider returns valid adjusted rows but fewer unique sessions than the 61, 300, or 500-session lane request
- **THEN** the provider chain continues to eligible adjusted fallbacks and uses the first response meeting the target, or the deepest valid partial response when every provider is short

#### Scenario: Raw fallback is available
- **WHEN** Sina, raw efinance, intraday, estimated, or display-only prices exist
- **THEN** those rows do not satisfy 61, 300, or 500 adjusted-session depth

#### Scenario: Raw-only fallback succeeds
- **WHEN** Sina, efinance, an intraday snapshot, or another provider returns raw prices without complete adjusted value, price basis, provider version, adjustment version, and source timestamp
- **THEN** the system may record display-only health but MUST NOT mark the row decision-eligible or increase publication coverage

#### Scenario: A response mixes valid and invalid rows
- **WHEN** one provider response contains both provenance-valid adjusted rows and rows with an incompatible version or price basis
- **THEN** only the individually valid rows are returned to persistence

#### Scenario: Provider history is factually short
- **WHEN** an accepted adjusted provider returns fewer eligible sessions than requested
- **THEN** the system persists the observed boundaries and retry-after without inferring a listing date or removing the ETF from the coverage denominator

#### Scenario: A different history lane is active
- **WHEN** any 61, 300, or 500-session history worker holds a live lease
- **THEN** another history lane cannot start provider work

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
