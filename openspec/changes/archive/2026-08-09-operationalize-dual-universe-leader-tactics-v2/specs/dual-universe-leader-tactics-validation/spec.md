## Purpose

定义双标的池龙头战术的标签、样本外经济验证、锁定案例和晋升门槛，使任何“有效”结论都来自预注册、时间顺序且扣除成本的证据，而不是事后挑图或调参。

## ADDED Requirements

### Requirement: Source-derived labels preserve partial observability
The validation registry SHALL include only asset/date/theme labels explicitly supported by captured articles, distinguish positive mentions, core mentions, formula descriptions, and unavailable identities, and SHALL NOT treat unmentioned assets as negative labels.

#### Scenario: Article identifies two core stocks
- **WHEN** a captured article factually names two core assets and does not enumerate all non-core assets
- **THEN** those two may be positive core labels while every unmentioned peer remains unlabeled rather than negative

#### Scenario: An image identity is uncertain
- **WHEN** ticker inference cannot be verified from factual source text or image evidence
- **THEN** the label remains `unresolved` and is excluded from precision and recall denominators

### Requirement: Chronological source split and locked case are immutable
Rules and mappings SHALL be derived only from source material published through 2026-07-15, evaluated out of sample on 2026-07-16 through 2026-07-29, and finally checked once against the locked 2026-08-03 case under the frozen V2 registry.

#### Scenario: Later article suggests a useful threshold
- **WHEN** a threshold or mapping is learned after 2026-07-15
- **THEN** it cannot alter V2 and requires a separately versioned future hypothesis

#### Scenario: Locked case is opened
- **WHEN** the 2026-08-03 case has been evaluated under V2
- **THEN** the holdout-use event and result are persisted and the case cannot be reopened for V2 tuning

### Requirement: August 3 core candidates are a natural reproduction test
When factual PIT data are sufficient, the locked A-share screen SHALL evaluate whether 泛微网络 (`603039`) and 利欧股份 (`002131`) naturally appear among AI-application core candidates without identifier-specific rules, score bonuses, or post-hoc exclusions.

#### Scenario: Both assets surface naturally
- **WHEN** the frozen formulas and PIT data select both assets under the locked cutoff
- **THEN** the evidence records a successful case reproduction with their ordinary gate facts and ranks

#### Scenario: Either asset does not surface
- **WHEN** one or both assets fail a frozen gate or data are insufficient
- **THEN** the evidence records `locked_case_mismatch` or the exact unavailable reason and V2 is not tuned against the case

### Requirement: A-share economic validation uses theme-relative net excess
The A-share primary endpoint SHALL be the five-session cost-adjusted net excess return of a frozen candidate cohort relative to the equal-weight set of decision-eligible same-theme peers on common support, entering at the first eligible adjusted close after the signal and charging the declared non-zero fees and slippage on both entry and exit.

#### Scenario: Primary outcome is complete
- **WHEN** signal, entry, five-session exit, peer benchmark, and cost inputs all exist causally
- **THEN** the system reports paired net excess, cohort size, benchmark size, turnover, cost drag, drawdown, concentration, and exclusions

#### Scenario: Outcome window is incomplete
- **WHEN** an entry, exit, peer benchmark, or future five-session window is unavailable
- **THEN** the observation is pending or excluded with a stable reason and is not filled with a shorter horizon

### Requirement: ETF economic validation retains the frozen ranking primary
The ETF primary endpoint SHALL remain Top10 five-session paired net excess relative to the frozen comprehensive-ranking Top10 baseline on the same eligible dates and comparable ETFs, under the existing non-zero cost, clone, common-support, and exclusion policies.

#### Scenario: A different ETF metric is favorable
- **WHEN** Top5, Top20, absolute return, hit rate, lifecycle exit, or a 1, 3, or 10-session metric improves while the ETF primary does not
- **THEN** the result remains exploratory and cannot establish incremental ranking alpha

### Requirement: Validation reports classification and economic diagnostics separately
The system SHALL report source-label precision and recall where labels are observable, plus signal frequency, qualifying assets per date, turnover, cost erosion, maximum drawdown, theme and issuer concentration, regime dependence, coverage, non-finite exclusions, and residual overlap with existing factors.

#### Scenario: Classification match is high but returns are weak
- **WHEN** source-label precision or recall is favorable but the primary net-excess endpoint fails
- **THEN** evidence distinguishes source resemblance from economic usefulness and does not recommend promotion

#### Scenario: Returns depend on a narrow subset
- **WHEN** apparent improvement is concentrated in one theme, regime, date range, or small set of assets
- **THEN** the stability gate fails even if the pooled estimate is positive

### Requirement: Promotion requires pre-registered time-out-of-sample evidence
A V2 candidate SHALL remain research-only unless its applicable primary endpoint has at least 252 factual PIT eligible sessions, at least 40 non-overlapping five-session primary dates, at least three chronological walk-forward folds, the declared purge and embargo, a Holm-adjusted 95-percent block-bootstrap confidence-interval lower bound above zero, one-time holdout success, and passing turnover, drawdown, concentration, clone, raw-price, finite-value, and coverage gates.

#### Scenario: Evidence is below any hard gate
- **WHEN** any sample, uncertainty, stability, provenance, coverage, drawdown, concentration, or holdout gate fails
- **THEN** the candidate is `insufficient_data`, `unconfirmed`, or `rejected` with exact evidence and cannot affect production

#### Scenario: Every gate passes
- **WHEN** a frozen candidate passes every applicable gate without post-hoc tuning
- **THEN** it may be labeled `eligible_for_separate_promotion_review`, requiring a distinct manually approved production change with rollback

### Requirement: Candidate search remains bounded
The validation system SHALL compare only the three frozen V2 candidates and SHALL NOT perform threshold grids, weight search, repeated holdout access, or selection by auxiliary metrics.

#### Scenario: Optimizer proposes another configuration
- **WHEN** an experiment attempts to choose thresholds or weights after reading out-of-sample outcomes
- **THEN** the run is invalidated for V2 and its evidence cannot be merged with the pre-registered experiment
