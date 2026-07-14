## ADDED Requirements

### Requirement: ETF exit risk evidence is part of the research contract
The ETF research evidence contract SHALL record exit-policy validation metadata alongside signal, allocation, replay, and label evidence, including immutable exposure-baseline semantics, absolute-target action cycles, execution provenance, and notification-policy provenance.

#### Scenario: Exit validation contract is created
- **WHEN** ETF exit risk validation completes
- **THEN** the system records exit policy version, action-cycle/idempotency version, immutable exposure-baseline and absolute-target semantics, protection guard version, execution and notification models, data window, universe scope, source signal run, selected codes, baseline policies, candidate policy id, evidence status, and contract hash

#### Scenario: Exit evidence uses current signal source
- **WHEN** validation is scoped to latest comprehensive TopN ETFs
- **THEN** the contract records the source signal run id, source signal date, score basis, requested TopN, selected count, excluded count, and selected codes

#### Scenario: Evidence is research-only
- **WHEN** exit evidence has not been manually approved for live tracked-position use
- **THEN** the contract records `research_only=true` and `approved_for_live=false`

#### Scenario: Evidence uses legacy lifecycle semantics
- **WHEN** evidence compounds relative reductions, treats recommendation/email state as execution, derives reentry cooldown from notification history, or counts repeated notifications as trades
- **THEN** the contract labels it `legacy_current_semantics/old_contract`, keeps it research-only, and MUST NOT present it as proof for the current action lifecycle

### Requirement: Exit evidence approval status is explicit
The ETF research evidence contract SHALL distinguish default rules, candidate parameters, approved parameters, guard-only evidence, and unavailable evidence.

#### Scenario: Candidate policy is stored
- **WHEN** validation produces a candidate exit policy
- **THEN** the contract records its status as `candidate`, the metrics that supported it, and the reason it was not automatically applied

#### Scenario: Approved policy is stored
- **WHEN** a user or admin approves an exit policy for live tracked-position evaluation
- **THEN** the contract records approver context, approval time, approved policy version, scope, and rollback target

#### Scenario: Evidence is insufficient
- **WHEN** validation lacks enough samples, rolling windows, tracked-position paths, or decision-eligible price data
- **THEN** the evidence status is `样本不足` or `等待验证` and MUST NOT expose fallback confidence as real evidence

### Requirement: Exit evidence status follows current rule versions
The ETF research evidence contract SHALL mark exit evidence as current only when rule versions, execution model, universe scope, and approval status match the current workflow.

#### Scenario: Evidence is from same contract
- **WHEN** the latest exit evidence matches the current signal source, exit policy version, protection guard version, execution model, and universe scope
- **THEN** the system marks the exit evidence status as `同源已验证`

#### Scenario: Evidence version differs
- **WHEN** exit evidence was produced by a different policy version, guard version, execution model, or universe scope
- **THEN** the system marks the evidence as `版本不一致` or `旧口径结果`

#### Scenario: Approved policy is missing current evidence
- **WHEN** live tracked-position evaluation uses an approved policy but no current validation result exists for the same policy version
- **THEN** the UI and API expose the approval status separately from validation freshness
