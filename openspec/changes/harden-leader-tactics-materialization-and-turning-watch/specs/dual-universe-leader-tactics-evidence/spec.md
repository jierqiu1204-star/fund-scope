## ADDED Requirements

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
