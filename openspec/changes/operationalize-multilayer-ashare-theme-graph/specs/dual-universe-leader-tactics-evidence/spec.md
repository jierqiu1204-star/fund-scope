## ADDED Requirements

### Requirement: Classification-graph evidence is complete
Every A-share observation SHALL persist the selected peer-context identity, hierarchy path, selected membership or industry fact hash, all alternative context identities and hashes, rejected-context reasons, theme-state hash, registry version, source snapshot date, and capture health under the immutable manifest.

#### Scenario: Broad fallback is selected
- **WHEN** all finer compatible contexts are unavailable or below the peer minimum
- **THEN** evidence lists each rejected context and identifies the exact broad source, hierarchy level, and fallback reason

#### Scenario: Multi-theme candidate is displayed
- **WHEN** a security has several cutoff-visible memberships
- **THEN** the API returns the selected context separately from alternative memberships and does not imply that alternatives were absent

### Requirement: Classification readiness and provenance are exposed without provider work
Candidate and summary reads SHALL return persisted industry coverage, theme relation coverage, complete theme-state coverage, latest snapshot identity, provider health, and stable unavailable reasons without invoking an external provider or mutating research state.

#### Scenario: Provider is currently unavailable
- **WHEN** the latest complete persisted snapshot remains valid but the next capture failed
- **THEN** the API distinguishes snapshot age from provider health and does not replace the complete snapshot with a partial attempt

