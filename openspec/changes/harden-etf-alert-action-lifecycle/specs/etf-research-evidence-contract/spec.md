## MODIFIED Requirements

### Requirement: ETF research evidence contract is the shared strategy boundary
The system SHALL define an ETF research evidence contract that records the signal, allocation, alert lifecycle, action policy, replay, and evidence metadata used by ETF short-term research and tracked-position validation.

#### Scenario: Signal contract is created
- **WHEN** ETF short-term signals are generated
- **THEN** the system records asset code, signal run id, signal date, score, observation label, entry timing label, data reliability, source data time, and signal rule version

#### Scenario: Allocation contract is created
- **WHEN** ETF portfolio allocation is generated
- **THEN** the system records allocation version, portfolio mode, market regime, target weights, allocation layers, constraints, data timestamp, and source signal version

#### Scenario: Action contract is created
- **WHEN** a tracked-position alert lifecycle or action policy is evaluated
- **THEN** the system records position episode, exposure version/baseline, alert episode, action cycle/target stage, evaluation cutoff, data status, policy/rule version, input snapshot hash, absolute sell-only target semantics, action idempotency version, notification item/envelope policy version, and source signal/allocation contract hashes when required

#### Scenario: Replay contract is created
- **WHEN** ETF backtest or label/action validation replays historical behavior
- **THEN** the system records the signal version, allocation version, alert/action policy version, episode/idempotency semantics, execution model, fee/slippage model, date range, point-in-time universe, data cutoff, and contract hash used by the replay

### Requirement: Evidence status is explicit
The system SHALL expose contract compatibility, data eligibility, execution provenance, sample sufficiency, and promotion status as separate fields so same-contract simulation cannot be mistaken for user-confirmed execution.

#### Scenario: Evidence is from same contract
- **WHEN** the latest evidence summary matches the current signal, allocation, alert/action, data cutoff, and execution contract versions
- **THEN** the system marks contract compatibility as `same_contract/同源`, while independently showing data, execution, sample, and promotion statuses

#### Scenario: Evidence is unavailable
- **WHEN** the current signal, allocation, or alert/action contract has no completed matching evidence
- **THEN** the system marks sample/promotion status as `等待验证` without inventing execution provenance

#### Scenario: Evidence sample is insufficient
- **WHEN** a validation result has insufficient independent action episodes, completed future windows, decision-eligible adjusted data, or chronological coverage under its pre-registered gate
- **THEN** the system marks sample status as `样本不足` and leaves contract/execution fields independently accurate

#### Scenario: Evidence version differs
- **WHEN** historical evidence was produced by a different signal, allocation, action policy, idempotency, or execution version
- **THEN** the system marks contract compatibility as `版本不一致` or `旧口径结果` and MUST NOT present it as current-policy proof

#### Scenario: Simulation uses the current contract
- **WHEN** a backtest exactly matches the current policy but has only simulated fills
- **THEN** contract compatibility may be `same_contract` while execution provenance remains `simulated_execution`, never `user_confirmed`

## ADDED Requirements

### Requirement: Alert action evidence links decisions without fabricating execution
ETF research evidence SHALL preserve the distinction among observed input, evaluated signal, proposed action, notification SMTP/receipt provenance, simulated execution, and user-confirmed execution.

#### Scenario: Backtest executes a proposal
- **WHEN** a backtest applies a proposed action under its declared T+1 execution model
- **THEN** the evidence labels the fill `simulated_execution`, records its costs and source prices, and MUST NOT write it as a tracked-position user execution

#### Scenario: SMTP accepts an email
- **WHEN** SMTP accepts a production notification linked to a proposed action
- **THEN** the evidence records `smtp_accepted` separately, does not claim verified delivery, and leaves execution provenance unknown until an owner-confirmed fact exists

#### Scenario: Legacy email is compared
- **WHEN** a historical email lacks episode, action, or execution identifiers
- **THEN** the evidence labels it `legacy_unverified` and excludes it from confirmed-execution accuracy while allowing clearly labeled legacy notification analysis

### Requirement: Action evidence cannot promote production rules automatically
Action-policy validation output SHALL remain research evidence and MUST NOT mutate live ranking, allocation, tracked positions, policy thresholds, notification preferences, or action versions.

#### Scenario: Candidate passes historical gates
- **WHEN** a candidate satisfies its pre-registered development and validation gates
- **THEN** the system creates a reviewable evidence summary and requires a separate authorized policy-version change before production use

#### Scenario: Candidate has missing real evidence
- **WHEN** real adjusted-data coverage, final holdout, or required live-session evidence is incomplete
- **THEN** the system remains `等待验证` or `样本不足` and MUST NOT substitute old, simulated, fallback, or raw-price evidence
