## ADDED Requirements

### Requirement: ETF research evidence contract is the shared strategy boundary
The system SHALL define an ETF research evidence contract that records the signal, allocation, replay, and evidence metadata used by ETF short-term research.

#### Scenario: Signal contract is created
- **WHEN** ETF short-term signals are generated
- **THEN** the system records asset code, signal run id, signal date, score, observation label, entry timing label, data reliability, source data time, and signal rule version

#### Scenario: Allocation contract is created
- **WHEN** ETF portfolio allocation is generated
- **THEN** the system records allocation version, portfolio mode, market regime, target weights, allocation layers, constraints, data timestamp, and source signal version

#### Scenario: Replay contract is created
- **WHEN** ETF backtest or label validation replays historical behavior
- **THEN** the system records the signal version, allocation version, execution model, fee model, date range, data cutoff, and contract hash used by the replay

### Requirement: Evidence status is explicit
The system SHALL expose whether ETF labels, allocation, and backtest or validation evidence are based on the same contract.

#### Scenario: Evidence is from same contract
- **WHEN** the latest evidence summary matches the current signal and allocation contract versions
- **THEN** the system marks the evidence status as `同源已验证`

#### Scenario: Evidence is unavailable
- **WHEN** the current signal or allocation contract has no completed matching evidence
- **THEN** the system marks the evidence status as `等待验证`

#### Scenario: Evidence sample is insufficient
- **WHEN** a validation result has insufficient completed samples or future windows
- **THEN** the system marks the evidence status as `样本不足`

#### Scenario: Evidence version differs
- **WHEN** historical evidence was produced by a different signal or allocation version
- **THEN** the system marks the evidence status as `版本不一致` or `旧口径结果`

### Requirement: Domain dependencies follow one direction
The backend SHALL enforce the ETF research layer dependency direction and prevent lower layers from importing higher layers.

#### Scenario: Market data layer is checked
- **WHEN** architecture dependency validation runs
- **THEN** market data modules do not import research, portfolio allocation, tracking, risk alert, or notification modules

#### Scenario: Research signal layer is checked
- **WHEN** architecture dependency validation runs
- **THEN** research signal modules do not import tracking, risk alert, or notification modules

#### Scenario: Portfolio allocation layer is checked
- **WHEN** architecture dependency validation runs
- **THEN** portfolio allocation modules do not import tracking, risk alert, or notification modules

#### Scenario: Notification layer is checked
- **WHEN** architecture dependency validation runs
- **THEN** notification modules do not import market data, research signal, portfolio allocation, or tracking modules

### Requirement: Orchestration is separated from domain rules
The system SHALL keep scheduler, admin, API, and workflow modules as orchestration layers rather than places for core research or risk algorithms.

#### Scenario: Workflow invokes multiple domains
- **WHEN** a workflow needs to run market data sync, signal generation, allocation, validation, or notification
- **THEN** it calls domain services and records results without embedding duplicate scoring, allocation, or alert algorithms
