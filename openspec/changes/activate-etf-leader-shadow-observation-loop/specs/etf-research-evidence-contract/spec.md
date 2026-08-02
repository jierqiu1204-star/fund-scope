## ADDED Requirements

### Requirement: Leader evidence exposes partial observation accumulation
The ETF research evidence contract SHALL expose leader observation progress before promotion eligibility, including observed PIT sessions, completed cross-sections, current matches, pending and matured outcomes, independent primary dates, chronological folds, exact exclusions, cutoffs, manifests, and feature hashes.

#### Scenario: Observation exists without mature outcome
- **WHEN** at least one complete session observation exists but its forward window is pending
- **THEN** the API returns the current research-only matches and exact pending counts with status `insufficient_data`, and no return metric is fabricated

#### Scenario: No candidate passes a frozen proxy
- **WHEN** a complete session produces zero finite candidate matches
- **THEN** the API returns an explicit zero-match observation with coverage and exclusion counts instead of treating the evidence as unavailable

### Requirement: Observation progress cannot satisfy promotion by implication
Leader observation evidence MUST keep accumulation state, statistical validation state, and production promotion state independent.

#### Scenario: Early observations appear favorable
- **WHEN** current matches or immature exploratory outcomes look favorable before every promotion gate passes
- **THEN** evidence remains `research_replay`, notification and execution provenance remain `none` or simulated as observed, and the production score contract remains unchanged

### Requirement: Current-vintage historical screening remains a separate zero-credit evidence family
The ETF research evidence contract SHALL expose any sealed-source historical-price screening under a distinct experiment family and SHALL assign it zero eligible PIT sessions, zero independent primary dates, and zero walk-forward folds.

#### Scenario: Historical adjusted prices produce a proxy candidate
- **WHEN** a bounded artifact combines a sealed current source snapshot with decision-eligible total-return-adjusted history and passes immutable contract validation
- **THEN** the API may expose the candidate as `research_replay` with membership mode `sealed_source_snapshot_current_vintage_proxy`, while factual observation counts, notification provenance, execution provenance, and promotion state remain unchanged

#### Scenario: Peer classification or price provenance is unsafe
- **WHEN** a candidate uses an unknown, other, or unclassified peer group, a raw-price fallback, or a mismatched source or feature hash
- **THEN** the historical proxy evidence is rejected or shown as incompatible and contributes no candidate or gate credit
