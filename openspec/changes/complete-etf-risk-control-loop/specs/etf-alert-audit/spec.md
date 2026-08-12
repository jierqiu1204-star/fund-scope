## ADDED Requirements

### Requirement: Alert audit records owner risk and capacity context
The alert audit SHALL record the owner risk state, sleeve evidence identity, capital confirmation, liquidity capacity and whether the state changed the proposed action.

#### Scenario: Add is blocked by owner risk
- **WHEN** an otherwise eligible add or reentry recommendation is blocked by `reduce_only` or `data_halt`
- **THEN** the audit records original action, final action, state, trigger reasons, NAV/source hash and policy version

#### Scenario: Exit has poor capacity
- **WHEN** an exit signal remains valid but liquidity capacity is stressed or unavailable
- **THEN** the audit preserves the exit signal and records spread, participation, estimated liquidation time and unavailable reasons without inferring a fill

### Requirement: Risk context is reused within one owner evaluation run
The system SHALL build owner aggregate risk context once per bounded evaluation run and reuse it for every position in that run.

#### Scenario: Owner has many tracked positions
- **WHEN** a list or scheduled workflow evaluates multiple positions for one owner
- **THEN** owner-level NAV/state/stop aggregates are loaded in batches and do not issue the same aggregate queries once per position
