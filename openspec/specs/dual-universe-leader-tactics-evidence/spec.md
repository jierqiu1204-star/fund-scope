# dual-universe-leader-tactics-evidence Specification

## Purpose
为双标的池龙头战术的每次筛选、状态变化和历史验证保存不可变证据，确保页面看到的候选可复现、不可夸大来源，且研究运行不会产生任何生产交易副作用。
## Requirements

### Requirement: Every run has an immutable reproducibility manifest
The system SHALL persist one immutable manifest per run containing code version, registry and source hashes, universe, formula candidates, decision cutoff, universe/input/feature hashes, adjustment and taxonomy versions, costs, clone policy, state policy, pagination cursor, exclusions, provider health, and runtime bounds.

#### Scenario: A run is resumed
- **WHEN** a bounded continuation resumes from a compatible checkpoint
- **THEN** it retains the same manifest identity, skips committed pages idempotently, and produces the same completed content hashes as an uninterrupted run

#### Scenario: An input contract changes
- **WHEN** code, source registry, formula, adjustment, taxonomy, cost, or state policy is incompatible with the checkpoint
- **THEN** the old checkpoint is not merged and a new run identity is required

### Requirement: Candidate and transition facts are append-only
The system SHALL persist candidate observations and lifecycle transitions as append-only research facts with universe, asset, theme, formula, source session, state, score, gate facts, cutoff, receipt time, manifest hash, and supersession linkage.

#### Scenario: The same session is replayed
- **WHEN** an idempotent replay sees identical inputs and manifest
- **THEN** it does not create duplicate candidate or transition facts

#### Scenario: Later provider data differs
- **WHEN** a later revision changes the recomputed result
- **THEN** the system preserves the original PIT fact and stores the revision under a distinct audit identity

### Requirement: Evidence exposes stable availability and exclusion reasons
The evidence API SHALL distinguish missing authoritative universe, missing adjusted prices, insufficient history, missing PIT theme, incompatible taxonomy, insufficient peers, failed breadth, raw decision violation, non-finite input, pending confirmation, missing future outcome, incompatible manifest, and insufficient independent samples.

#### Scenario: Coverage is insufficient
- **WHEN** one required data layer cannot support the selected universe and cutoff
- **THEN** metrics remain unavailable with exact numerator, denominator, threshold, and reason instead of displaying zero or stale evidence

#### Scenario: A signal is only preparing
- **WHEN** no causal confirmation transition exists
- **THEN** the API reports `pending_confirmation` and does not infer confirmation from later data outside the requested cutoff

### Requirement: Provenance cannot imply live use
Leader-tactics V2 evidence SHALL expose `ranking_source_kind=research_replay`, `policy_mode=policy_shadow` when lifecycle exits are evaluated, and notification and execution provenance exactly as `none` or simulated unless factual downstream events exist under a separately approved production contract.

#### Scenario: Historical replay completes
- **WHEN** a candidate and outcome are produced only by research replay
- **THEN** the evidence states that no live notification, provider delivery, user-confirmed order, or execution occurred

#### Scenario: Candidate passes research gates
- **WHEN** every validation gate passes
- **THEN** evidence may state `eligible_for_separate_promotion_review` but MUST NOT claim production ranking use, a buy recommendation, guaranteed return, or source-author endorsement

### Requirement: Research execution has no production side effects
The V2 pipeline SHALL write only research tables and artifacts and SHALL NOT mutate comprehensive or intraday rankings, score weights, allocations, tracked positions, risk alerts, notification logs, SMTP state, or execution state.

#### Scenario: Full research pipeline runs
- **WHEN** screening, lifecycle replay, outcomes, diagnostics, and UI evidence are generated
- **THEN** production-state hashes and row counts outside declared research stores remain unchanged

#### Scenario: A production write is attempted
- **WHEN** a V2 research path calls a production ranking, position, notification, or execution mutation
- **THEN** the operation fails closed and records a boundary violation

### Requirement: Source corpus provenance is compact and lawful
The repository SHALL store source URL or identity, publication time, captured-content hash, and concise rule paraphrases required for reproducibility, and SHALL NOT require copying the full locally captured article corpus into the project.

#### Scenario: Local source notes are available during registration
- **WHEN** an authorized researcher registers an article from the local corpus
- **THEN** only the compact source metadata and derived rule assertions required by the manifest are persisted in the repository

### Requirement: Two-stage materialization evidence is complete
Every two-stage materialization run SHALL expose its immutable run hash, signal date, source cutoff, current stage/status, expected assets, completed terminal feature assets, completed peer groups, and last update time. Resource-gated workflow results SHALL additionally carry the current and required headroom. A candidate manifest SHALL be visible only after all expected assets have compatible terminal feature facts and all peer groups pass integrity checks.

#### Scenario: Feature preparation is incomplete
- **WHEN** one or more expected assets lack a terminal feature fact
- **THEN** progress reports exact completed and expected counts and the candidate API does not expose a partial cohort

#### Scenario: Finalization completes
- **WHEN** all feature facts and the final cross-section pass compatibility and integrity checks
- **THEN** exactly one materialized manifest becomes visible and repeated finalization is idempotent

### Requirement: Theme resolution and watch distance are auditable
Candidate evidence SHALL persist the selected theme fact hash, taxonomy, hierarchy level, source, resolution mode, fallback reason, passed gate families, failed gate families, and finite normalized distances used for `turning_watch` classification.

#### Scenario: Broad industry fallback is used
- **WHEN** a candidate uses a broad industry because no cutoff-visible fine theme exists
- **THEN** evidence explicitly reports `broad_industry_fallback` and does not relabel the industry as a fine theme

#### Scenario: Turning watch is displayed
- **WHEN** an observation is returned with `state=turning_watch`
- **THEN** its evidence identifies the exact non-actionable watch contract and all remaining candidate blockers

### Requirement: ETF V2 isolation is verifiable
The materialization workflow SHALL be restricted to the V2 research namespace, and acceptance tests SHALL verify that comprehensive-ranking and other protected production identities cannot be written through this path.

#### Scenario: ETF research materialization runs normally
- **WHEN** ETF V2 creates or resumes a research manifest
- **THEN** protected ranking, allocation, position, alert, notification, and execution identities are unchanged

#### Scenario: Protected production state changes
- **WHEN** an ETF V2 path attempts or causes a protected-state mutation
- **THEN** the run fails closed with `research_boundary_violation`

### Requirement: Sentiment risk evidence is immutable and self-describing
Every A-share observation SHALL persist or deterministically project the risk contract hash, state, action mode, new-entry permission, metric values, cohort counts, thresholds, cutoff, source kind, unavailable reason, and explicit unsupported factual limit-board fields under the observation manifest.

#### Scenario: Materialized evidence is read
- **WHEN** the candidates or summary endpoint reads an A-share manifest
- **THEN** it returns only persisted risk facts from that manifest and performs no provider call or current-market recomputation

#### Scenario: Older evidence lacks the contract
- **WHEN** a legacy materialization has no compatible sentiment-risk contract hash
- **THEN** the API reports risk evidence as unavailable or absent with a stable reason rather than treating it as healthy

### Requirement: Risk evidence remains research-only
The evidence SHALL identify the action policy as shadow research and SHALL NOT claim that a candidate was bought, sold, notified, delivered, or executed.

#### Scenario: Shadow entry is allowed
- **WHEN** a healthy qualified breakout reports `shadow_entry_allowed`
- **THEN** notification and execution provenance remain `none`, production mutation remains forbidden, and the UI describes an eligible research observation rather than a recommendation
