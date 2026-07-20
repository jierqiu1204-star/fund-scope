## MODIFIED Requirements

### Requirement: ETF research evidence contract is the shared strategy boundary
The system SHALL define an immutable ETF research evidence contract that records the exact ranking snapshot, score field, universe snapshot, price basis, data cutoff, reliability policy, allocation, replay, and evidence metadata used by ETF short-term research.

#### Scenario: Signal contract is created
- **WHEN** ETF short-term signals are generated
- **THEN** the system records asset code, signal snapshot id, signal date, global rank, score field and value, observation label, entry timing label, data reliability, source data time, score/rule versions, universe hash, and ranking contract hash

#### Scenario: Allocation contract is created
- **WHEN** ETF portfolio allocation is generated
- **THEN** the system records allocation version, portfolio mode, market regime, target weights, allocation layers, constraints, data timestamp, and exact source ranking snapshot and contract hash

#### Scenario: Replay contract is created
- **WHEN** ETF backtest or label validation replays historical behavior
- **THEN** the system records source snapshot ids, score and rule versions, universe hash, price basis, execution model, fee model, date range, data cutoff, reliability policy, and exact contract hash used by the replay

### Requirement: Evidence status is explicit
The system SHALL classify ETF evidence by exact persisted contract identity and sample readiness and SHALL NOT fill a missing historical identity from the current contract.

#### Scenario: Evidence is from same contract
- **WHEN** completed evidence has a non-null hash that exactly matches the current ranking and allocation contract versions and satisfies sample requirements
- **THEN** the system marks the evidence status as `同源已验证`

#### Scenario: Evidence is unavailable
- **WHEN** the current signal or allocation contract has no completed matching evidence
- **THEN** the system marks the evidence status as `等待验证`

#### Scenario: Evidence sample is insufficient
- **WHEN** an exact-contract validation result has insufficient independent dates, coverage, or completed future windows
- **THEN** the system marks the evidence status as `样本不足`

#### Scenario: Evidence version differs
- **WHEN** historical evidence has a present but different ranking, signal, allocation, universe, price-basis, or contract version
- **THEN** the system marks the evidence status as `版本不一致` or `旧口径结果`

#### Scenario: Evidence hash is missing
- **WHEN** a validation, backtest, signal, or allocation result has no persisted contract hash or mandatory version
- **THEN** the system marks it `旧口径结果`, keeps the hash absent, and MUST NOT copy the current contract hash into the result

## ADDED Requirements

### Requirement: Contract Hashes Use Canonical Inputs
The ranking contract hash SHALL be derived from a canonical serialization of score and rule versions, score-bearing component manifest, universe snapshot hash, price basis, data-cutoff semantics, and decision-eligibility policy.

#### Scenario: Score-bearing input contract changes
- **WHEN** a component weight, required field, eligibility rule, universe identity, or price basis changes
- **THEN** the canonical contract hash changes and previous evidence cannot be classified as current

#### Scenario: Explanatory text changes only
- **WHEN** presentation wording changes without changing the declared ranking or evidence inputs
- **THEN** the ranking contract hash remains stable

### Requirement: Evidence Production Has No Decision-Side Effects
Validation, replay, and backtest workflows SHALL write only research evidence records and SHALL NOT update current ranking scores or ranks, allocation weights, tracked positions, risk alerts, or notification records.

#### Scenario: Validation completes with negative outcomes
- **WHEN** a current-contract validation run finishes and reports negative evidence
- **THEN** the evidence is displayed with its direction while all current decision-domain records and timestamps remain unchanged

#### Scenario: Validation is rerun
- **WHEN** the same source contract is validated again
- **THEN** only versioned evidence results are added or updated and no signal generation workflow is triggered

### Requirement: Consumers Reject Incompatible Evidence
Every evidence consumer SHALL compare the persisted source snapshot and contract identity before presenting an evidence result as current.

#### Scenario: API builds selected ETF evidence summary
- **WHEN** available validation belongs to a different universe hash or score field
- **THEN** the summary exposes the mismatch and does not merge its sample count or outcomes with current evidence
