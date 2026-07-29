# short-etf-research-reliability Specification

## Purpose
TBD - created by archiving change enhance-short-etf-research-reliability. Update Purpose after archive.
## Requirements
### Requirement: ETF Data Sync Uses Provider Fallback
The system SHALL use an ordered policy of accepted adjusted-price providers, SHALL bound each provider attempt independently within the remaining slice budget, and SHALL attempt another accepted provider only when enough budget remains and the result can carry complete `total_return_adjusted` provenance.

#### Scenario: Backup adjusted provider fills a primary-source failure
- **WHEN** the preferred adjusted-price provider fails, times out, is cooling down, or returns no usable rows for one ETF
- **THEN** the system attempts the next accepted adjusted provider within the ETF and slice budgets before marking that ETF attempt incomplete

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

#### Scenario: A completion request contains a partial gap

- **WHEN** persisted accepted adjusted rows leave only a subset of frozen
  required sessions missing
- **THEN** each provider is evaluated against those missing required dates
  inside their enclosing date span
- **AND** extra raw, invalid, or non-required dates cannot falsely satisfy the
  missing-session minimum

#### Scenario: A short-history observation belongs to one lane calendar

- **WHEN** an accepted provider proves a shortfall for one scope and frozen
  required-calendar hash
- **THEN** its cooldown defers only that exact lane/calendar request
- **AND** it cannot suppress a 61, 300, or 500-session request with a different
  scope or frozen calendar

#### Scenario: Provider attempt exhausts its budget
- **WHEN** one provider reaches its per-attempt timeout
- **THEN** the attempt is cancelled without consuming the entire continuation budget, and fallback is attempted only if the remaining admission and checkpoint reserve is sufficient

#### Scenario: Provider repeatedly fails
- **WHEN** a provider reaches the configured consecutive failure threshold across bounded slices
- **THEN** the system persists a retry cooldown and skips that provider until `retry_after` rather than retrying it for every ETF

### Requirement: ETF Data Health Is Visible
The system SHALL maintain per-ETF data health showing latest data date, provider used, successful row count, latest failure reason, and consecutive failure count.

#### Scenario: Data health updates after sync
- **WHEN** ETF data synchronization finishes
- **THEN** each attempted ETF has data-health status reflecting success, provider fallback, or failure

#### Scenario: Data health is shown in the web UI
- **WHEN** the user opens the short ETF tab or admin jobs page
- **THEN** the UI shows ETF data coverage, stale data warnings, provider fallback counts, and failed ETF codes in Chinese

### Requirement: Short ETF Risk Labels Are Expanded
The system SHALL identify short-term ETF risk labels for chase risk, suspicious recent surge, high volatility, low liquidity, large drawdown, stale data, and insufficient history.

#### Scenario: High recent surge is marked risky
- **WHEN** an ETF has a recent return pattern above configured surge thresholds
- **THEN** the signal item includes a prominent chase or surge risk label and cannot be presented as stronger than observation language

#### Scenario: Insufficient data limits confidence
- **WHEN** an ETF lacks enough daily history for the configured metrics
- **THEN** the ETF is marked with insufficient-history risk or excluded from ranking with a readable reason

### Requirement: Review Explains Both Opportunity And Risk
The system SHALL generate rule-first multi-role review notes that explain why each ETF may be worth observing and why it may be dangerous, without changing rank or score.

#### Scenario: Review preserves original signal ranking
- **WHEN** a short ETF review is generated
- **THEN** each review item keeps the original signal rank and total score unchanged

#### Scenario: Review uses research-only language
- **WHEN** the API returns a review item
- **THEN** the item includes data, trend, risk, opposing-view, and summary notes but no buy instruction, sell instruction, target price, or guaranteed return

### Requirement: Reliability Evaluation Tests Signal Robustness
The system SHALL support reliability evaluation for short ETF signal rules using historical replay, fee assumptions, nearby parameter sets, and comparison against simple baselines.

#### Scenario: Evaluation stores research results separately
- **WHEN** the user runs a short ETF reliability evaluation
- **THEN** the system persists evaluation summary and parameter items without modifying signal runs, paper portfolios, or virtual orders

#### Scenario: Parameter instability is flagged
- **WHEN** only one narrow parameter combination performs well while nearby combinations perform poorly
- **THEN** the evaluation marks parameter stability risk in the summary

#### Scenario: Sample size warning is enforced
- **WHEN** available ETF history is shorter than the configured observation threshold
- **THEN** the evaluation conclusion is limited to sample-insufficient language

### Requirement: Short ETF Reliability Is Operable From The Web
The system SHALL expose Chinese web controls for retrying failed ETF data, viewing data health, running reliability evaluation, and inspecting evaluation charts.

#### Scenario: User retries failed ETFs
- **WHEN** the user clicks retry failed ETF data from the web UI
- **THEN** the system reruns data sync only for failed or stale ETFs and shows the result in Chinese

#### Scenario: User views evaluation output
- **WHEN** the user opens reliability evaluation results
- **THEN** the UI shows conclusion, sample-size warning, return, drawdown, fee impact, parameter stability, and baseline comparison charts in Chinese

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
