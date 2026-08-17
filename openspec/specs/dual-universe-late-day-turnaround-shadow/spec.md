# dual-universe-late-day-turnaround-shadow Specification

## Purpose
为 ETF 与 A 股提供可审计、可复现且与正式综合排名隔离的尾盘转强研究筛选，确保候选只由截止时间前真实可见的日线和盘中证据产生。

## Requirements

### Requirement: Dual universes remain independent
The system SHALL screen `etf` and `ashare` as separate universes with independent denominators, manifests, observations, exclusions, and unavailable reasons.

#### Scenario: User switches the universe
- **WHEN** a user requests ETF or A-share late-day-turnaround evidence
- **THEN** the response contains only the requested universe and never merges candidates or coverage across universes

### Requirement: Formal signals require factual point-in-time inputs
The system SHALL create a formal late-day-turnaround candidate only from a prior-session daily fact and closed intraday bars whose source and receipt timestamps are no later than the candidate decision cutoff.

#### Scenario: ETF inputs are complete
- **WHEN** an ETF has a causally visible prior-session price basis, sufficient closed intraday bars, current-session open and turnover, and all frozen formula gates pass between 14:30 and 14:50 Asia/Shanghai
- **THEN** the system may materialize an ETF research candidate with the exact input and contract identities

#### Scenario: A-share minute inputs are missing
- **WHEN** an A-share has adjusted daily history but lacks factual intraday bars received by the decision cutoff
- **THEN** the system returns a stable minute-data unavailable reason and does not infer a formal signal from daily data

#### Scenario: Intraday and daily price bases differ
- **WHEN** raw intraday prices must be compared with an adjusted prior-session series
- **THEN** the system records and applies an explicit normalization identity or excludes the observation as an incompatible price basis

### Requirement: Daily proxy remains non-actionable
The system MAY expose an A-share daily proxy observation pool, but it SHALL label that pool as `daily_proxy_watchlist`, set historical live-signal eligibility to false, and keep it separate from formal candidates.

#### Scenario: Only daily facts are available
- **WHEN** the user requests A-share evidence and only daily PIT facts are complete
- **THEN** the API may return a clearly labeled watchlist while the formal candidate list remains empty and unavailable

### Requirement: Checkpoint materialization is bounded and immutable
The system SHALL attempt research materialization only at 14:30, 14:40, and 14:50 Asia/Shanghai, enforce a hard job timeout of at most 55 seconds, and persist one immutable manifest per completed universe and cutoff.

#### Scenario: One checkpoint completes
- **WHEN** all required inputs for a universe are read within the bounded job
- **THEN** the system stores the cutoff, universe hash, input hash, formula hash, coverage, provider health, observations, exclusions, and completion status

#### Scenario: Capture or materialization is incomplete
- **WHEN** provider work, pagination, memory, or coverage cannot complete within the bound
- **THEN** the system persists a partial or unavailable status without publishing partial candidates and resumes only from a compatible checkpoint

### Requirement: Read APIs never trigger provider work
The system SHALL provide read-only contract, readiness, and candidate endpoints whose requests only read compatible persisted evidence.

#### Scenario: Evidence has not been materialized
- **WHEN** a client queries a universe without a compatible completed manifest
- **THEN** the API returns an empty candidate page with a stable unavailable reason instead of fetching market data

#### Scenario: Candidate evidence exists
- **WHEN** a compatible completed manifest exists at the requested cutoff
- **THEN** the API returns bounded, deterministic candidate pages with decision time, source provenance, formula metrics, exclusions, notification provenance `none`, and execution provenance `none`

### Requirement: The user interface preserves evidence boundaries
The system SHALL provide an independent late-day-turnaround research view with ETF/A-share selection, evidence cutoff, coverage, candidates, daily proxy observations, and exclusion diagnostics.

#### Scenario: User opens the strategy view
- **WHEN** the strategy feature is enabled and the page loads
- **THEN** the page displays persisted research evidence and visibly states that it does not alter comprehensive ranking, holdings, reminders, or execution

### Requirement: Comprehensive ranking and production actions are isolated
The late-day-turnaround workflow SHALL NOT write ETF comprehensive-ranking scores or snapshots and SHALL NOT create holdings, transactions, alerts, SMTP state, or broker execution state.

#### Scenario: A candidate is materialized
- **WHEN** either universe produces a research candidate
- **THEN** only late-day-turnaround research tables change and all ranking and production-action stores remain unchanged
