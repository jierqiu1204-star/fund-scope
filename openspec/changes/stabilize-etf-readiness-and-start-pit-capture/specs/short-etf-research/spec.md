## MODIFIED Requirements

### Requirement: ETF ranking publication requires current and score-ready adjusted data
The system SHALL expose no target-date ranking when readiness is `blocked`, MAY expose an explicitly provisional research preview when target-date adjusted coverage is at least 95 percent and 61-session score coverage is at least 90 percent, and SHALL treat a dual-ranking snapshot as complete and canonical only when both coverage ratios are at least 95 percent.

#### Scenario: Decision-data coverage is insufficient
- **WHEN** target-date decision-eligible adjusted coverage is below 95 percent
- **THEN** the system exposes no target-date ranking and reports `blocked` with the current coverage reason

#### Scenario: Score coverage is below the preview gate
- **WHEN** decision-data coverage reaches 95 percent but 61-session score-eligible coverage remains below 90 percent
- **THEN** the system exposes no target-date ranking and reports the warm-up blocker

#### Scenario: Score coverage supports a degraded research preview
- **WHEN** decision-data coverage reaches 95 percent and 61-session score-eligible coverage is at least 90 percent but below 95 percent
- **THEN** the system may expose only eligible ETFs through a provisional research preview marked `degraded`, with excluded counts and reasons, and MUST NOT represent it as a complete dual or actionable publication

#### Scenario: Raw fallback rows exist
- **WHEN** Sina, efinance, intraday snapshot, stale cache, estimated, or other raw-only rows exist without complete adjusted provenance
- **THEN** those rows remain display-only or unavailable and MUST NOT increase preview, complete-publication, or PIT coverage

#### Scenario: Both complete gates pass
- **WHEN** both registered 95 percent gates pass for the same target trade date and authoritative universe
- **THEN** the system may materialize and publication-validate one complete dual-ranking snapshot using only decision-eligible adjusted inputs
