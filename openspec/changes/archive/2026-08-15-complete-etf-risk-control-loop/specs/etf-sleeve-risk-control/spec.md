## ADDED Requirements

### Requirement: Tracked ETF sleeve NAV uses only confirmed and eligible facts
The system SHALL calculate a tracked ETF sleeve NAV only from explicitly confirmed capital, owner-confirmed position/execution facts, and cutoff-consistent decision-eligible marks.

#### Scenario: Complete sleeve evidence is available
- **WHEN** configured capital is explicitly confirmed and every active ETF position has valid quantity, basis and a decision-eligible mark at the cutoff
- **THEN** the system records cash, market value, realized and unrealized P&L, total equity, coverage and source hash in an immutable sleeve snapshot

#### Scenario: Evidence is incomplete
- **WHEN** capital is unconfirmed, a position quantity is unknown, an execution chain is ambiguous, or any required mark is stale, missing, raw-only or non-finite
- **THEN** the snapshot is unavailable with stable reasons and the system MUST NOT replace it with average position P&L, a default capital amount or a fallback price

### Requirement: Sleeve drawdown is measured from an equity high-water mark
The system SHALL define sleeve drawdown as the current eligible sleeve equity relative to the maximum prior eligible sleeve equity within the same evidence contract.

#### Scenario: Positions have unequal sizes
- **WHEN** holdings have different market values and gains or losses
- **THEN** drawdown is calculated from total sleeve equity rather than an unweighted average of position return percentages

#### Scenario: Only one eligible NAV point exists
- **WHEN** no earlier comparable eligible sleeve snapshot exists
- **THEN** drawdown history is insufficient and the system does not invent a zero-risk history

### Requirement: Account risk state is persistent and asymmetric
The system SHALL persist a versioned owner-scoped risk state of `normal`, `reduce_only` or `data_halt` with trigger and recovery evidence.

#### Scenario: Risk evidence deteriorates
- **WHEN** valuation coverage fails, capital becomes unconfirmed, a real drawdown threshold is breached, or distinct eligible stop cycles exceed the protection threshold
- **THEN** the system immediately records the applicable stricter state and blocks new risk while retaining reduce/exit evidence

#### Scenario: Risk evidence recovers
- **WHEN** the cooldown has elapsed and at least the configured number of distinct eligible trade sessions pass the release conditions
- **THEN** the system may transition to a less restrictive state and records the recovery sessions without counting same-day reruns twice

### Requirement: Stop protection distinguishes signal, notification and execution
The system SHALL count repeated stop protection by distinct eligible trade session and position episode/action cycle, and SHALL preserve provenance.

#### Scenario: Same alert repeats during polling
- **WHEN** the same stop condition creates multiple audit or notification rows for one action cycle
- **THEN** it counts as one signal event for protection purposes

#### Scenario: SMTP accepts an email
- **WHEN** a stop email is accepted but the user has not confirmed execution
- **THEN** the system records a notification fact but does not increment owner-confirmed stop execution count
