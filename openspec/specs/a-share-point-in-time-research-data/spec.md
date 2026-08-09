# a-share-point-in-time-research-data Specification

## Purpose
为 A 股龙头战术研究提供可重放、可审计且严格遵守历史可见性的标的池、复权行情和主题归属数据，避免用当前成分、迟到数据或原始价格伪造历史信号。
## Requirements
### Requirement: A-share research universe is authoritative and point-in-time
The system SHALL persist an immutable A-share research-universe snapshot for every eligible session, including security code, listing state, board, effective time, receipt time, provider identity, source cutoff, and exclusion reason, and SHALL only use facts whose effective time and receipt time are no later than the decision cutoff.

#### Scenario: Historical universe is replayed
- **WHEN** a leader-tactics run evaluates session T
- **THEN** it uses the latest authoritative universe snapshot factually received by T and does not substitute the current universe

#### Scenario: Membership arrives late
- **WHEN** a universe or listing fact is first received after the decision cutoff
- **THEN** the fact is excluded from that historical decision and the run records `late_universe_fact`

### Requirement: Theme membership preserves both effective and receipt time
The system SHALL store each A-share theme or sector membership with taxonomy version, effective-from and effective-to times, receipt time, source, and confidence state, and SHALL NOT infer historical membership from a current theme constituent list.

#### Scenario: Theme membership is decision eligible
- **WHEN** the membership was effective and factually received before the cutoff under a compatible taxonomy
- **THEN** the security may participate in that theme's peer statistics with the membership provenance attached

#### Scenario: Only current theme membership exists
- **WHEN** no historical membership fact was received by the session cutoff
- **THEN** the security is excluded from theme-relative gates with `missing_pit_theme_membership`

### Requirement: Decision prices are total-return-adjusted and source governed
The system SHALL use only finite total-return-adjusted A-share OHLCV from an approved AKShare/Eastmoney/TickFlow decision-data path and SHALL persist trade date, adjustment identity, source, receipt time, and revision identity for every bar used in a signal or outcome.

#### Scenario: Approved adjusted history is available
- **WHEN** an adjusted bar was received by the cutoff and passes finite-value and continuity checks
- **THEN** the bar may be used for moving averages, ATR, returns, highs, drawdowns, and outcomes

#### Scenario: Only raw or audit-only price exists
- **WHEN** only Sina/efinance raw data, Tencent audit data, an unadjusted close, or an incompatible adjustment is available
- **THEN** decision coverage does not increase and the row is excluded with a stable provenance reason

### Requirement: Historical visibility is not backfilled by later corrections
The system SHALL distinguish event time from first receipt time and SHALL preserve the originally visible value for each research replay, while retaining later revisions as separately versioned audit facts.

#### Scenario: Provider revises an old bar
- **WHEN** a corrected adjusted bar is received after a historical signal cutoff
- **THEN** the correction may be used in a current-data audit but MUST NOT replace the value visible to the original PIT replay

#### Scenario: Historical receipt time is unknown
- **WHEN** imported history lacks a factual first-receipt timestamp
- **THEN** it is marked `historical_research_only` and cannot satisfy factual forward-capture or promotion-session counts

### Requirement: A-share ingestion is bounded, resumable, and resource safe
The system SHALL run A-share research ingestion with one worker, deterministic work ordering, adaptive batches of 5 to 20 securities, durable idempotent checkpoints, bounded memory, and a hard continuation budget no greater than 55 seconds.

#### Scenario: Batch budget is reached
- **WHEN** the time or memory budget is reached before the universe is complete
- **THEN** the system commits only complete securities, persists the next cursor, releases its lease, and exits without starting a concurrent continuation

#### Scenario: A batch is retried
- **WHEN** the same checkpoint is resumed after timeout, restart, or provider failure
- **THEN** completed facts are not duplicated and the final content hashes match an uninterrupted run

### Requirement: Data readiness is observable without fallback inflation
The system SHALL report authoritative-universe coverage, adjusted-daily coverage, each required history tier, PIT-theme coverage, provider health, raw-decision violations, non-finite violations, and exclusions separately for A shares.

#### Scenario: A required layer is incomplete
- **WHEN** any formula input lacks sufficient eligible coverage
- **THEN** the screen returns the exact layer count and stable unavailable reason without filling the gap from an audit-only or raw source
