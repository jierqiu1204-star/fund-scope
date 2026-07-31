# etf-research-evidence-contract Specification

## Purpose
TBD - created by archiving change define-etf-research-evidence-contract. Update Purpose after archive.
## Requirements
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

### Requirement: ETF Evidence Contract Records Factor Experiments
The ETF research evidence contract SHALL record factor experiment manifests, hashes, code versions, data provenance, split roles, candidate identities, execution and cost policies, sample exclusions, uncertainty methods, and production-isolation status.

#### Scenario: Factor experiment starts
- **WHEN** a pre-registered factor experiment is accepted
- **THEN** the evidence contract stores its immutable manifest hash before outcome calculation

#### Scenario: Factor sample is persisted
- **WHEN** a factor or ranked-portfolio sample is stored
- **THEN** it references the experiment hash, ranking contract, signal date, split role, source cutoff, factor inputs, eligibility state, outcome horizon, and exclusion state

#### Scenario: Evidence is displayed
- **WHEN** a user views a factor experiment report
- **THEN** the report identifies development, validation, and holdout results and states that the evidence cannot mutate production

#### Scenario: Evidence is offered as current ranking proof
- **WHEN** an experiment does not match the current ranking contract and all required holdout gates
- **THEN** it is marked research-only, version-mismatched, or insufficient and MUST NOT be shown as same-source production validation

### Requirement: ETF Evidence Contract Records Catalyst Shadow Provenance
The ETF research evidence contract SHALL record catalyst source registry versions, receipt IDs and hashes, event and mapping versions, snapshot cutoffs and hashes, coverage states, extraction methods, verification states, and production-isolation status.

#### Scenario: Catalyst shadow snapshot is created
- **WHEN** the system creates a point-in-time catalyst snapshot
- **THEN** the evidence contract records the exact receipts, event versions, taxonomy, source cutoff, coverage, limitations, and snapshot hash

#### Scenario: Catalyst event study is created
- **WHEN** a pre-registered catalyst event study runs
- **THEN** the evidence contract records its immutable manifest, cohorts, controls, execution and cost policies, splits, outcomes, exclusions, uncertainty, and contract hash

#### Scenario: Catalyst evidence is offered as score validation
- **WHEN** no separate catalyst scoring contract and matching out-of-sample evidence exist
- **THEN** the system marks the catalyst evidence as shadow research and MUST NOT present it as validation of current score, rank, allocation, alert, or notification behavior

### Requirement: ETF research evidence preserves independent provenance dimensions
ETF research evidence SHALL record `ranking_source_kind`, `policy_mode`, `notification_provenance`, and `execution_provenance` independently and MUST NOT infer a stronger provenance state from another dimension.

#### Scenario: Policy shadow emits a simulated notification
- **WHEN** a replay lifecycle produces a shadow-eligible notification
- **THEN** policy mode is `policy_shadow`, notification provenance is `simulated`, and execution provenance remains `simulated_execution` or `none` as separately observed

#### Scenario: SMTP accepted a live message
- **WHEN** a live notifier records SMTP acceptance without provider receipt or user execution
- **THEN** notification provenance is `smtp_accepted_live`, provider delivery remains unconfirmed, and execution provenance remains `none`

### Requirement: ETF research evidence exposes immutable identity and quality
ETF research evidence SHALL expose the manifest hash, data cutoff, ranking contract, candidate registry, price basis, execution and cost policy, split and holdout identity, coverage dimensions, exclusions, sample counts, uncertainty, and primary or exploratory status used by each result.

#### Scenario: Primary ranking evidence is returned
- **WHEN** the API returns a completed Top10 five-session paired result
- **THEN** it includes common-support coverage, independent dates, costs, bootstrap interval, multiplicity adjustment, drawdown comparison, holdout state, and manifest identity

#### Scenario: Evidence cannot be computed
- **WHEN** a required data, compatibility, coverage, future-window, sample, notification, or execution condition is missing
- **THEN** the API returns a stable unavailable reason instead of a fabricated metric

### Requirement: Production promotion requires separate approval
ETF research evidence SHALL distinguish promotion eligibility from production promotion and MUST NOT mutate the current score contract.

#### Scenario: Every promotion gate passes
- **WHEN** evidence satisfies all pre-registered data, sample, uncertainty, stability, drawdown, concentration, and holdout gates
- **THEN** it may be marked `promotion_eligible` while `final_score_v3` remains active until a separately approved score-version change

### Requirement: ETF evidence records source-to-proxy provenance
The ETF research evidence contract SHALL record the leader-tactics hypothesis registry version and hash, source article identities and captured-content hashes, disclosed and unavailable source rules, ETF adaptation mapping, exact proxy formulas, candidate registry, regime contract, point-in-time cutoffs, input and feature hashes, cost and execution policy, split and holdout identity, exclusions, diagnostics, uncertainty, and production-isolation state.

#### Scenario: Leader shadow evidence is returned
- **WHEN** an API returns a leader-tactics experiment result
- **THEN** it identifies the source-derived hypothesis separately from the implemented transparent proxy and states that proprietary or subjective source elements were not reproduced

#### Scenario: Source provenance is incomplete
- **WHEN** a source identity, interpretation version, formula, or captured-content hash required by the manifest is missing or incompatible
- **THEN** the evidence is unavailable with a stable provenance reason and no outcome metric is presented as matching the hypothesis

### Requirement: Leader evidence cannot imply stronger provenance
ETF evidence SHALL expose leader-tactics results with `ranking_source_kind=research_replay`, `policy_mode=policy_shadow` for MA5 lifecycle diagnostics, `notification_provenance=none` or `simulated`, and `execution_provenance=simulated_execution` or `none` exactly as observed.

#### Scenario: Only historical proxy replay exists
- **WHEN** a leader-tactics replay completes without a live notification or user-confirmed fill
- **THEN** the evidence remains historical proxy research and live notification, delivery, and confirmed execution stay unavailable

#### Scenario: Candidate is promotion eligible
- **WHEN** a candidate passes every research gate
- **THEN** the evidence may say `eligible_for_v4_proposal` but MUST NOT claim current production use, guaranteed return, or author endorsement

