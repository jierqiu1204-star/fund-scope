## ADDED Requirements

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
