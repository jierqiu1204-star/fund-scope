## MODIFIED Requirements

### Requirement: Daily Research Rank Uses Point-In-Time Adjusted Daily Data
The daily research surface SHALL calculate the frozen `daily_reconstructable_v1.research_score` from exactly the latest 61 decision-eligible total-return-adjusted OHLCV sessions available as of the ranking date, SHALL apply separately versioned canonical eligibility gates, and SHALL NOT consume intraday, spread, IOPV, premium, provider-consensus, theme-catalyst, outcome, position, alert, or notification inputs.

#### Scenario: Sixty-session return is calculable and quality passes
- **WHEN** an ETF has 61 valid adjusted sessions ending on the ranking date and passes canonical tradability and taxonomy eligibility
- **THEN** the research contract may calculate its 5/10/20/60-session features and include it in the canonical research rank

#### Scenario: Score exists but canonical eligibility fails
- **WHEN** a finite research score can be reconstructed but absolute tradability or compatible taxonomy fails
- **THEN** the score may be retained for observation-only explanation but the ETF has no canonical research rank

#### Scenario: Intraday data is unavailable
- **WHEN** canonical adjusted-daily research inputs exist but bid/ask, IOPV, premium, or provider consensus is unavailable
- **THEN** the research score remains available and unchanged while actionable rank remains unavailable

#### Scenario: Ineligible price fallback exists
- **WHEN** only raw, stale, estimated, display-only, or non-total-return-adjusted prices can fill a required research bar
- **THEN** the system fails the research row closed instead of substituting those values

### Requirement: ETF Ranking Surfaces Have Distinct Contracts
The system SHALL expose research, actionable, observation-only, peer-diagnostic, and diversified-presentation fields with distinct identities and SHALL prevent any diagnostic or presentation position from aliasing the canonical research or actionable rank.

#### Scenario: ETF has canonical and diagnostic positions
- **WHEN** an ETF has a canonical research rank plus peer percentile or diversified presentation position
- **THEN** the API identifies every field separately and downstream rank consumers use only their declared canonical surface

#### Scenario: ETF is research-only
- **WHEN** an ETF passes canonical research eligibility but lacks a mandatory actionable input
- **THEN** it remains ranked on the research surface and has no actionable rank, with the actionable exclusion reason recorded

#### Scenario: Contract identities cannot be substituted
- **WHEN** a consumer requests or validates one ranking surface
- **THEN** the system MUST NOT satisfy that request with another score, position, version, state, or hash

## ADDED Requirements

### Requirement: Percentile and component scores are bounded and deterministic
The system SHALL calculate peer percentiles with a versioned empirical-rank method that supports values below, between, equal to, and above reference observations and SHALL keep every primitive, component, and aggregate finite and within its declared bounds before publication.

#### Scenario: Value is absent from the peer observations
- **WHEN** a scored ETF value lies between peer observations or outside their range
- **THEN** the empirical rank remains within `[0, 100]` and does not depend on an equality match

#### Scenario: Peer group is too small
- **WHEN** a bucket has fewer than the versioned minimum eligible non-clone peers
- **THEN** the peer-relative component is unavailable with `insufficient_peer_count` rather than generating a 0 or 100 extreme

#### Scenario: Component exceeds a declared bound
- **WHEN** an intermediate component or aggregate is non-finite or would fall outside its declared bounds
- **THEN** the row fails closed, records a cap or non-finite violation, and cannot become actionable

#### Scenario: Batch shape changes
- **WHEN** identical inputs are evaluated in a different order or safe 5/10/20 batch size
- **THEN** primitive, component, aggregate, rank, exclusion, and contract hashes remain identical

### Requirement: Clone-aware ranking preserves product-specific execution quality
The system SHALL permit one tracked underlying to contribute once to shared price-factor distributions while preserving ETF-specific liquidity, spread, premium, fee, and provider evidence.

#### Scenario: Two ETFs track one underlying
- **WHEN** two ETFs have the same cutoff-valid tracked-underlying identity
- **THEN** shared price primitives contribute one deterministic clone-group observation to the peer distribution

#### Scenario: Clone wrappers differ in tradability
- **WHEN** clone ETFs have different turnover, spread, premium, or provider quality
- **THEN** their execution-quality evidence remains product-specific and the deterministic representative selection explains the winning wrapper

#### Scenario: Underlying identity is missing
- **WHEN** an ETF lacks authoritative tracked-underlying evidence
- **THEN** it is counted in unresolved clone coverage and MUST NOT be silently represented as proof that clone control passed

### Requirement: Peer diagnostics do not manufacture global comparability
The system SHALL record comparable asset bucket, peer count, bucket percentile, and concentration separately from the frozen global research score and SHALL NOT globally compare tiny-bucket extremes as validated alpha.

#### Scenario: Small bucket produces an extreme percentile
- **WHEN** a commodity, bond, or cross-border bucket lacks the minimum peer count
- **THEN** its peer percentile is unavailable and its frozen research score remains separately identified

#### Scenario: Unknown bucket is encountered
- **WHEN** an ETF has an unknown or incompatible bucket
- **THEN** no peer-relative or actionable score is produced and the ETF remains observation-only
