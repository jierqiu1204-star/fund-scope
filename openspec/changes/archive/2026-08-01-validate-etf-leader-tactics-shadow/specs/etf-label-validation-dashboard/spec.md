## ADDED Requirements

### Requirement: ETF evidence UI separates leader-tactics proxy research
The ETF evidence page SHALL render a separately labeled `龙头战术透明代理（研究）` panel containing hypothesis registry identity, source links, proxy non-equivalence notice, formulas, cutoffs, coverage, exclusions, sample counts, primary paired result, uncertainty, overlap diagnostics, concentration, regime stability, holdout state, and MA5 exploratory policy result when available.

#### Scenario: Evidence is insufficient
- **WHEN** the experiment lacks required PIT dates, complete Top10 cohorts, peer mappings, adjusted history, completed outcomes, or independent samples
- **THEN** the panel shows the exact counts and a stable `insufficient_data` reason without displaying a historical winner

#### Scenario: Candidate fails validation
- **WHEN** a candidate fails residual-alpha, cost, uncertainty, stability, drawdown, concentration, clone, or holdout gates
- **THEN** the panel labels it `未确认` or `已否决` and keeps the failed gate visible

#### Scenario: Candidate becomes proposal eligible
- **WHEN** every frozen research gate passes
- **THEN** the panel labels it `可提出 V4 变更` and states that formal ranking and email behavior are still unchanged

### Requirement: Leader-tactics UI does not imply the original method or live results
The ETF evidence page MUST NOT label a transparent proxy as the source author's proprietary signal and MUST keep production ranking, historical replay, MA5 policy shadow, live notification, provider delivery, and user-confirmed execution visually and textually separate.

#### Scenario: User views a proxy formula
- **WHEN** a source rule was proprietary, subjective, or omitted
- **THEN** the UI shows the implemented proxy and limitation together rather than implying exact replication

#### Scenario: No live notification evidence exists
- **WHEN** only factor replay and simulated MA5 lifecycle evidence exist
- **THEN** email accuracy, provider delivery, and real execution remain explicitly unavailable
