## ADDED Requirements

### Requirement: Leader observations accumulate before promotion eligibility
The leader-tactics shadow SHALL begin accumulating research observations from the first complete decision-eligible PIT source and MUST keep collection eligibility separate from promotion eligibility.

#### Scenario: First complete PIT source is available
- **WHEN** one immutable policy-v2 PIT source passes daily 95-percent and 61-session 90-percent readiness, cutoff, adjusted-price, membership, and provider-health checks
- **THEN** the system may materialize leader proxy observations for that source with status `insufficient_data` even though fewer than 252 eligible sessions exist

#### Scenario: Promotion sample is short
- **WHEN** fewer than 252 eligible PIT sessions, 40 independent primary dates, or three chronological validation folds exist
- **THEN** observations and matured outcomes remain research-only, every missing gate is reported, and no candidate becomes promotion eligible

### Requirement: Leader observation pages preserve cross-sectional correctness
The leader-tactics shadow SHALL process at most 20 ETFs per bounded page, persist compact per-asset primitives and exclusions, and form candidate scores only after the complete same-session eligible cross-section required by each peer comparison has been sealed.

#### Scenario: Session processing is incomplete
- **WHEN** only part of a PIT source has been processed
- **THEN** the checkpoint exposes page progress and exclusions but MUST NOT publish a partial Top N cohort or fill missing peers from another date, baseline, or proxy

#### Scenario: Complete session cross-section is sealed
- **WHEN** every eligible asset page for the PIT source is complete and all peer-group inputs are finite
- **THEN** the system deterministically materializes the frozen proxy observations, clone exclusions, scores, ranks, source cutoff, and feature hashes

### Requirement: Leader forward outcomes mature without blocking new observations
The leader-tactics shadow SHALL preserve pending five-session, ten-session, and MA5 policy-shadow outcomes separately from same-session observations and SHALL mature them only from later decision-eligible adjusted facts visible by the outcome cutoff.

#### Scenario: Future window is incomplete
- **WHEN** an observation lacks the required later trading sessions or adjusted entry or exit fact
- **THEN** the outcome remains pending with an exact reason while later PIT observations may continue accumulating

#### Scenario: Future window matures
- **WHEN** all pre-registered entry, hold, cost, and MA5 lifecycle facts become decision eligible
- **THEN** the system appends a compatible matured outcome identity without rewriting the original observation or reading a one-time holdout early

### Requirement: Missing PIT taxonomy and regime facts fail closed per asset
The leader-tactics shadow SHALL use only taxonomy, peer, sector-trend, baseline-score, and market-regime facts bound to the complete source snapshot and visible by its declared replay cutoff.

#### Scenario: Historical metadata is absent
- **WHEN** a required peer mapping, sector fact, baseline fact, or regime fact is missing, stale, received late, or incompatible
- **THEN** the affected asset or routed proxy is excluded with a stable reason while other factually complete assets may continue

### Requirement: Historical proxy screening cannot masquerade as factual observation
The leader-tactics shadow SHALL keep sealed-source current-vintage historical screening outside the factual observation and outcome families.

#### Scenario: Current-vintage screening finds a match
- **WHEN** total-return-adjusted historical prices and a sealed current source snapshot produce a transparent proxy match
- **THEN** the match is stored only in the historical-proxy family, reports its current-vintage membership limitation, grants zero promotion credit, and cannot enter ranking, position, alert, email, execution, or holdout state
