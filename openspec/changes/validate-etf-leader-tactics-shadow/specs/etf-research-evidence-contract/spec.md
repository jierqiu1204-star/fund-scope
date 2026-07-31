## ADDED Requirements

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
