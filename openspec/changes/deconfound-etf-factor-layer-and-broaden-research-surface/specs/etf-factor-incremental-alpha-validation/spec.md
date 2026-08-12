## ADDED Requirements

### Requirement: Factor Peer Support Uses The Weakest Required Primitive
Factor diagnostics SHALL report per-primitive non-clone peer counts and same-date common-support counts, and SHALL treat the support of a composite factor as no greater than the minimum support of its required primitives. Formal `final_score_v3` score behavior remains frozen; stricter support is a research qualification gate until separately promoted.

#### Scenario: One primitive has fewer peers
- **WHEN** a composite candidate has 100 peers for momentum but only 8 peers for breadth
- **THEN** its common support is at most 8 and the report MUST NOT claim 100 eligible peers for the composite

#### Scenario: Shadow peer support is too small
- **WHEN** a date and peer bucket contains fewer than the frozen 20 non-clone common-support ETFs
- **THEN** that bucket-date is excluded from candidate IC and portfolio evidence with `insufficient_common_peer_support`

#### Scenario: Formal score is evaluated
- **WHEN** the stricter shadow peer-support gate is introduced
- **THEN** current `final_score_v3` weights, contract hash, production ranks, and downstream actions remain unchanged

### Requirement: Factor Redundancy Is Measured Before Incremental Alpha
The factor experiment SHALL calculate same-date, same-peer-bucket pairwise Spearman correlations, deterministic correlation clusters, effective factor count, baseline correlation, residual IC, and common-support marginal contribution for every frozen candidate.

#### Scenario: Candidate repeats existing price exposure
- **WHEN** a candidate is highly correlated with existing momentum, risk, liquidity, or sector primitives
- **THEN** the report assigns its correlation cluster and requires positive stable residual evidence instead of treating the raw factor as independent alpha

#### Scenario: Candidate has raw IC but no residual IC
- **WHEN** raw IC is favorable but residual IC or common-support marginal contribution is non-positive or unstable out of sample
- **THEN** the candidate remains `unconfirmed` regardless of its raw point estimate

#### Scenario: Diagnostics are sparse
- **WHEN** fewer than 20 non-clone common-support observations exist for a bucket-date
- **THEN** correlation and residual statistics for that unit are unavailable rather than zero-filled or pooled across later dates

### Requirement: Deconfounding Candidates Are Frozen And Research Only
The system SHALL pre-register at most three candidates under a separate immutable deconfounding registry: the current daily baseline, a residual-momentum plus breadth candidate, and a PIT flow plus constituent-breadth candidate. Missing authoritative PIT inputs SHALL make the affected candidate unavailable without falling back to baseline values.

#### Scenario: Registry is created
- **WHEN** the deconfounding experiment starts
- **THEN** candidate IDs, formulas, directions, transforms, minimum 20-peer support, required history, missing-value rules, and registry hash are frozen before outcomes are read

#### Scenario: Flow data is unavailable
- **WHEN** authoritative point-in-time ETF share-change or constituent-breadth input is absent at the signal cutoff
- **THEN** the PIT flow candidate is unavailable for that ETF-date and the baseline score is not substituted

#### Scenario: More candidates are requested
- **WHEN** a runtime request supplies a fourth candidate or changes a frozen formula after outcomes exist
- **THEN** the experiment is rejected and cannot reuse prior evidence or holdout authorization

#### Scenario: Candidate passes preliminary diagnostics
- **WHEN** a candidate has positive development or validation evidence
- **THEN** it remains research-only and MUST NOT change production ranking until all existing promotion gates pass and a separate manually approved score-version change is implemented
