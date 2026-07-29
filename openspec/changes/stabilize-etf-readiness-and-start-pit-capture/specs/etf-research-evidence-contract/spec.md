## ADDED Requirements

### Requirement: ETF evidence preserves readiness and cutoff provenance
ETF research evidence SHALL record readiness state and policy version, target-date and warm-up coverage, market decision cutoff, data receipt cutoff, replay visibility cutoff, snapshot state, resource profile, and provider-health identity independently, and MUST NOT infer historical visibility or complete publication from another field.

#### Scenario: Degraded production research preview is recorded
- **WHEN** a target-date research preview is generated at degraded warm-up coverage
- **THEN** evidence records `degraded`, the two factual coverage ratios, exclusions, decision and receipt cutoffs, provisional snapshot identity, and the absence of complete or actionable publication

#### Scenario: Complete production snapshot is recorded
- **WHEN** a complete dual snapshot passes both 95 percent gates
- **THEN** evidence records `complete`, the immutable dual-snapshot identity, decision and receipt cutoffs, provider provenance, and resource telemetry

#### Scenario: Historical PIT sample is built
- **WHEN** a research continuation evaluates a historical signal date
- **THEN** evidence records the replay visibility cutoff independently and excludes rows whose factual availability is later than that cutoff even if they supported a later production run

#### Scenario: Resource evidence is recorded
- **WHEN** a bounded readiness or PIT continuation finishes
- **THEN** evidence distinguishes configured RSS ceiling, baseline RSS, current RSS, slice peak current RSS, lifetime peak RSS, elapsed time, checkpoint, and stop reason

#### Scenario: Cutoff provenance is incomplete
- **WHEN** a result lacks a required decision, receipt, or replay visibility cutoff
- **THEN** the result receives a stable unavailable reason and MUST NOT be used as PIT validation or promotion evidence
