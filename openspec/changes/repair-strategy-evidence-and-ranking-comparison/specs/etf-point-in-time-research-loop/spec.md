## MODIFIED Requirements

### Requirement: Research loop uses only factual point-in-time decision data
The system SHALL use authoritative historical membership and total-return-adjusted signal inputs recorded as available no later than the signal cutoff. Future entry and exit facts SHALL instead be checked against the declared execution and outcome cutoffs, without changing what was visible when the original signal was created.

#### Scenario: Adjusted row was backfilled later
- **WHEN** a membership, taxonomy, or adjusted signal-input row was first received after the signal cutoff
- **THEN** the row is excluded from signal construction with a point-in-time visibility reason

#### Scenario: A legitimate future outcome matures
- **WHEN** an immutable signal is followed by valid adjusted execution and outcome prices available by their respective cutoffs
- **THEN** those future facts may complete the outcome and MUST NOT be rejected merely because they postdate the original signal

#### Scenario: Only raw fallback price exists
- **WHEN** only Sina, efinance, estimated, stale, or otherwise non-decision-eligible price data exists
- **THEN** the sample is excluded and decision coverage is not increased

## ADDED Requirements

### Requirement: Research phases produce calculated evidence from complete inputs
The research loop SHALL calculate candidate states, forward outcomes, paired validation, and evidence when their compatible inputs are available. Missing inputs SHALL be reported individually; a completed execution phase MUST NOT be confused with sufficient statistical evidence.

#### Scenario: Historical candidate state is available
- **WHEN** consecutive compatible signal dates are processed
- **THEN** holding age, retained assets, and replacement limits continue from the previous date's sealed state rather than resetting each day

#### Scenario: Each source date has a different run identity
- **WHEN** consecutive sources belong to different daily runs but the same frozen state-chain identity
- **THEN** the candidate state references the prior source date and state hash across those runs, while each original daily source remains immutable

#### Scenario: A regime-gated candidate is evaluated
- **WHEN** the candidate requires regime or liquidity facts
- **THEN** it uses the same-date cutoff-valid facts or records a specific exclusion, without a neutral or baseline substitution

#### Scenario: Complete forward inputs exist
- **WHEN** a frozen selection has the required exchange sessions and adjusted prices
- **THEN** the phase emits computed returns and costs instead of an unconditional pending placeholder

#### Scenario: Some research lanes lack data
- **WHEN** ranking outcomes are available but unrelated factor diagnostics, policy-shadow, or notification observations are unavailable
- **THEN** the report exposes available ranking evidence and the independent missing lanes without marking the complete experiment promotion eligible

### Requirement: Ranking validation recovery advances compatible due work without rewriting signals
The research loop SHALL schedule the oldest compatible due outcome or validation unit through its existing bounded continuation model, preserve frozen signal artifacts, and record later outcome-input revisions separately. An immature cohort SHALL release its work opportunity rather than starving other due cohorts or new capture.

#### Scenario: Later prices make old work due
- **WHEN** the original source is unchanged and a later compatible outcome cutoff supplies new facts
- **THEN** a bounded continuation appends or updates only the permitted outcome/evidence revision and leaves original signal and candidate-selection hashes unchanged

#### Scenario: Work resumes after interruption
- **WHEN** the same outcome revision resumes from a committed cursor
- **THEN** completed pages are reused exactly once and the result matches uninterrupted processing

#### Scenario: A manifest or cost contract changes
- **WHEN** resume attempts to change a frozen signal, candidate, execution, split, or cost identity
- **THEN** resume fails closed and requires a new research identity rather than overwriting old evidence

#### Scenario: The continuation budget expires
- **WHEN** a continuation approaches the existing maximum 55-second bound
- **THEN** it commits completed work, releases its lease, reports remaining due work, and does not launch another worker
