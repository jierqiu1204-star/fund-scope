## ADDED Requirements

### Requirement: Innovation-drug membership is an observed fine-theme fact

The system SHALL register the exact public innovation-drug provider label, preserve effective and receipt time and a canonical fact hash, and use the fact only when it was received by the decision cutoff.

#### Scenario: Current membership is fetched after a historical signal

- **WHEN** an innovation-drug membership was received after the historical signal cutoff
- **THEN** it cannot satisfy that signal's theme gate or be backfilled as historical visibility

### Requirement: User captures preserve receipt-time limits

The system SHALL persist user-supplied captures with a content hash and actual receipt time while leaving unknown publication metadata unknown.

#### Scenario: Capture is received on 2026-08-17

- **WHEN** the capture describes an earlier market pattern but has no proven earlier publication metadata
- **THEN** it may explain the research hypothesis but cannot become pre-2026-08-17 PIT decision evidence
