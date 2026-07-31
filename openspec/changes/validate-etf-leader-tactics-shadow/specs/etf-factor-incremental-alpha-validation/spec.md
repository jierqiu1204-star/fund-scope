## ADDED Requirements

### Requirement: Leader-tactics proxies prove separate incremental alpha
The factor-validation system SHALL evaluate the leader-tactics hypothesis registry as a separate pre-registered factor experiment against the current frozen Top10 baseline and SHALL NOT add its candidates to or replace the existing baseline, hysteresis, and regime/liquidity ranking-candidate registry.

#### Scenario: Leader experiment starts
- **WHEN** the exact leader-tactics registry, baseline contract, split, costs, exclusions, uncertainty method, and promotion gates are frozen
- **THEN** the existing common-support factor experiment may evaluate up to the three declared leader candidates under a distinct manifest and holdout identity

#### Scenario: Existing ranking experiment is resumed
- **WHEN** the baseline/hysteresis/regime-liquidity research loop resumes
- **THEN** its candidate registry and hashes remain unchanged by the leader-tactics experiment

### Requirement: Leader-tactics overlap and scarcity are decision gates
The factor-validation system SHALL report each leader candidate's residual relationship to existing momentum, sector-trend, risk, liquidity, and overextension factors, plus signal frequency, qualifying ETFs per date, complete Top10 dates, history-tier coverage, peer-mapping coverage, clone exclusions, regime dependence, and concentration.

#### Scenario: Proxy repeats existing momentum
- **WHEN** raw candidate performance is favorable but residual IC or marginal common-support contribution after existing factor controls is not positive and stable out of sample
- **THEN** the candidate is `unconfirmed` and cannot become promotion eligible

#### Scenario: Signal set cannot form Top10
- **WHEN** fewer than ten eligible non-clone ETFs have a finite candidate score on a signal date
- **THEN** the date is excluded from the primary comparison with `insufficient_candidate_cohort` and MUST NOT be filled from the baseline or another proxy

#### Scenario: Result depends on one regime or small ETF subset
- **WHEN** improvement is confined to one short regime, one peer group, one history tier, or a small set of ETFs
- **THEN** the stability gate fails even if the pooled point estimate is positive

### Requirement: Leader-tactics promotion uses existing hard evidence gates
A leader-tactics candidate SHALL remain research-only unless it passes the existing paired Top10 five-session net-excess endpoint, common-support coverage, at least 252 factual point-in-time sessions, at least 40 non-overlapping primary dates, at least three chronological folds, purge and embargo, Holm-adjusted block-bootstrap interval, one-time holdout, turnover, drawdown, concentration, clone, finite-value, and raw-price gates.

#### Scenario: Exploratory metric is stronger
- **WHEN** MA5 lifecycle, Top5 or Top20, absolute return, hit rate, or a 1, 3, or 10-session result is favorable but the primary gate fails
- **THEN** the evidence remains `unconfirmed` or `rejected` and MUST NOT be described as validated alpha

#### Scenario: Every gate passes
- **WHEN** a frozen candidate passes every existing promotion gate on its one-time holdout
- **THEN** it may be labeled `eligible_for_v4_proposal` but does not modify production until a separate manually approved score-version change
