## MODIFIED Requirements

### Requirement: ETF research evidence contract is the shared strategy boundary
The system SHALL define an ETF research evidence contract that records the ranking surface, signal, allocation, replay, and evidence metadata used by ETF short-term research.

#### Scenario: Research rank contract is created
- **WHEN** a daily ETF research rank is generated
- **THEN** the system records `daily_reconstructable_v1`, `research_score`, rank, history-confidence tier, adjusted-data provenance, source cutoff, as-of date, contract hash, and eligibility limitations

#### Scenario: Actionable rank contract is created
- **WHEN** an actionable ETF rank is generated
- **THEN** the system records `actionable_rank_v1`, its eligibility-policy version and hash, the referenced `final_score_v3` version and hash, score, rank, mandatory input status, source times, and exclusion summary

#### Scenario: Signal contract is created
- **WHEN** ETF short-term signals are generated
- **THEN** the system records asset code, signal run id, signal date, ranking surface, score, observation label, entry timing label, data reliability, source data time, and signal rule version

#### Scenario: Allocation contract is created
- **WHEN** ETF portfolio allocation is generated
- **THEN** the system records allocation version, portfolio mode, market regime, target weights, allocation layers, constraints, data timestamp, source actionable-rank version, and source actionable-rank hash

#### Scenario: Replay contract is created
- **WHEN** ETF backtest or label validation replays historical behavior
- **THEN** the system records the ranking surface, signal version, allocation version, execution model, fee model, date range, warm-up range, data cutoff, and contract hash used by the replay

### Requirement: Evidence status is explicit
The system SHALL expose whether ETF labels, ranking surfaces, allocation, and backtest or validation evidence are based on the same contract.

#### Scenario: Evidence is from same contract
- **WHEN** the latest evidence summary matches the current ranking surface, signal, and allocation contract versions and hashes
- **THEN** the system marks the evidence status as `同源已验证`

#### Scenario: Evidence is unavailable
- **WHEN** the current ranking surface, signal, or allocation contract has no completed matching evidence
- **THEN** the system marks the evidence status as `等待验证`

#### Scenario: Evidence sample is insufficient
- **WHEN** a validation result has insufficient completed samples, history depth, or future windows
- **THEN** the system marks the evidence status as `样本不足`

#### Scenario: Evidence version differs
- **WHEN** historical evidence was produced by a different ranking surface, signal version, allocation version, or contract hash
- **THEN** the system marks the evidence status as `版本不一致` or `旧口径结果`

#### Scenario: Research evidence is offered for actionable rank
- **WHEN** evidence matches `daily_reconstructable_v1` but not `actionable_rank_v1`
- **THEN** the system MUST NOT present it as validation of actionable selection, allocation, or rank-derived email behavior
