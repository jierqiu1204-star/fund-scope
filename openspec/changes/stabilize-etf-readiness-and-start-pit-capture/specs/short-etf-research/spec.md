## MODIFIED Requirements

### Requirement: ETF ranking publication requires current and score-ready adjusted data
The system SHALL expose no target-date ranking when readiness is `blocked`, and SHALL treat a policy-v2 dual-ranking snapshot as complete and canonical when target-date adjusted coverage is at least 95 percent and 61-session score coverage is at least 90 percent. Legacy policy-v1 evidence MUST retain its original semantics.

#### Scenario: Decision-data coverage is insufficient
- **WHEN** target-date decision-eligible adjusted coverage is below 95 percent
- **THEN** the system exposes no target-date ranking and reports `blocked` with the current coverage reason

#### Scenario: Score coverage is below the preview gate
- **WHEN** decision-data coverage reaches 95 percent but 61-session score-eligible coverage remains below 90 percent
- **THEN** the system exposes no target-date ranking and reports the warm-up blocker

#### Scenario: Score coverage reaches the policy-v2 complete gate
- **WHEN** decision-data coverage reaches 95 percent and 61-session score-eligible coverage is at least 90 percent but below 95 percent
- **THEN** the system may publication-validate one complete dual snapshot containing only eligible ETFs, with excluded counts and reasons

#### Scenario: Raw fallback rows exist
- **WHEN** Sina, efinance, intraday snapshot, stale cache, estimated, or other raw-only rows exist without complete adjusted provenance
- **THEN** those rows remain display-only or unavailable and MUST NOT increase preview, complete-publication, or PIT coverage

#### Scenario: Both complete gates pass
- **WHEN** the registered 95 percent decision-data gate and 90 percent score-warmup gate pass for the same target trade date and authoritative universe
- **THEN** the system may materialize and publication-validate one complete dual-ranking snapshot using only decision-eligible adjusted inputs
