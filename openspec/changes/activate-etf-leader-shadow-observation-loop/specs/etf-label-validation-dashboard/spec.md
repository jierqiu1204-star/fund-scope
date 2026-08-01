## ADDED Requirements

### Requirement: Leader panel shows current research observations
The strategy-evidence page SHALL show the latest complete leader shadow observation with signal date, candidate identity, ETF code and name when available, research score, matched gates, cutoff, coverage, and exclusions, plus accumulation and pending-outcome counts.

#### Scenario: Current observations are available
- **WHEN** a complete PIT session has one or more frozen-proxy matches
- **THEN** the panel lists them under `当日 Shadow 观察` and visibly labels the list as unvalidated research rather than a buy recommendation

#### Scenario: Session has no matches
- **WHEN** the complete session has zero eligible proxy matches
- **THEN** the panel shows `本日无透明代理命中` together with coverage and leading exclusion reasons

#### Scenario: Session processing is partial
- **WHEN** the current source checkpoint has remaining ETF pages
- **THEN** the panel shows progress and MUST NOT display a partial ranked cohort as a completed observation

### Requirement: Leader panel separates accumulation from validation
The strategy-evidence page SHALL display observation-session count, matured primary-date count, the 252-session and 40-independent-date gates, fold progress, and promotion state as distinct fields.

#### Scenario: Evidence is still accumulating
- **WHEN** observations exist but promotion gates are incomplete
- **THEN** the panel shows `研究积累中 / 样本不足` and keeps formal ranking, email accuracy, provider delivery, and confirmed execution unavailable
