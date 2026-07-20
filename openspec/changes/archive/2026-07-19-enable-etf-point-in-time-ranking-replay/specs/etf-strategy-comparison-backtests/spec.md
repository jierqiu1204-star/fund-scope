## ADDED Requirements

### Requirement: Ranking Candidates Are Frozen Before Action Policy Comparison
ETF strategy comparison SHALL evaluate at most three pre-registered ranking candidates, select and freeze ranking input using only development/walk-forward evidence, and only then supply that fixed ranking contract to the separately frozen action-policy comparison.

#### Scenario: Ranking candidate set is registered
- **WHEN** the comparison pipeline is initialized
- **THEN** it records exactly the selected subset of daily-core baseline, Top-k dropout/rank-hysteresis, and hysteresis-plus-existing-regime-gate candidates with all parameters before outcomes are read

#### Scenario: Joint candidate search is requested
- **WHEN** a caller requests a ranking-by-action Cartesian grid or dynamically generated threshold variants
- **THEN** the system rejects the request and requires a new pre-registered research change

### Requirement: Ranking And Action Primary Endpoints Remain Independent
The comparison SHALL use Top10 five-trading-day paired net excess return for ranking selection and Top20 ten-trading-day tax/fee-adjusted action-cycle benefit for action-policy selection and SHALL preserve each endpoint's declared execution model.

#### Scenario: Ranking selection is calculated
- **WHEN** daily ranking candidates are compared
- **THEN** the primary return uses T+1 adjusted close, the same-date `all_scored` baseline, and the ranking cost contract

#### Scenario: Action selection is calculated
- **WHEN** action-policy candidates are compared using the frozen ranking source
- **THEN** the primary benefit uses the existing action-cycle T+1 adjusted-open model and does not reuse an unlabeled ranking `net_return`

#### Scenario: Combined final holdout is inspected
- **WHEN** the frozen ranking and action pipeline reaches its final holdout
- **THEN** the system consumes the holdout once and does not retune either stage from that result
