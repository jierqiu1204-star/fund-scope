# etf-ranking-surfaces Specification

## Purpose
TBD - created by archiving change separate-etf-research-and-actionable-ranks. Update Purpose after archive.
## Requirements
### Requirement: ETF Ranking Surfaces Have Distinct Contracts
The system SHALL expose a daily research ranking surface and an actionable ranking surface with distinct contract IDs, score fields, versions, hashes, ordered ranks, eligibility states, coverage summaries, and as-of timestamps.

#### Scenario: ETF is present on both surfaces
- **WHEN** an ETF satisfies both daily research and actionable eligibility
- **THEN** the system records its `daily_reconstructable_v1` research score and rank separately from its `actionable_rank_v1` score and rank

#### Scenario: ETF is research-only
- **WHEN** an ETF has eligible adjusted daily history but lacks a mandatory actionable input
- **THEN** the ETF remains ranked on the research surface and has no actionable rank, with the actionable exclusion reason recorded

#### Scenario: Contract identities cannot be substituted
- **WHEN** a consumer requests or validates one ranking surface
- **THEN** the system MUST NOT satisfy that request with the other surface's score, rank, version, or hash

### Requirement: Daily Research Rank Uses Point-In-Time Adjusted Daily Data
The daily research surface SHALL calculate `daily_reconstructable_v1.research_score` from exactly the latest 61 decision-eligible, total-return-adjusted OHLCV sessions available as of the ranking date and SHALL NOT consume intraday, spread, IOPV, premium, provider-consensus, theme, catalyst, outcome, position, alert, or notification inputs.

#### Scenario: Sixty-session return is calculable
- **WHEN** an ETF has 61 valid adjusted sessions ending on the ranking date
- **THEN** the research contract may calculate its 5/10/20/60-session features and include it in the research rank

#### Scenario: Adjusted history is insufficient
- **WHEN** an ETF has fewer than 61 decision-eligible adjusted sessions
- **THEN** it has no research score and the system records `insufficient_decision_eligible_adjusted_sessions`

#### Scenario: Intraday data is unavailable
- **WHEN** eligible adjusted daily inputs exist but bid/ask, IOPV, premium, or provider consensus is unavailable
- **THEN** the research score remains available and unchanged

#### Scenario: Ineligible price fallback exists
- **WHEN** only raw, stale, estimated, display-only, or non-total-return-adjusted prices can fill a required research bar
- **THEN** the system fails the research row closed instead of substituting those values

### Requirement: History Depth Is A Confidence Gate Rather Than A Score Bonus
The system SHALL assign a versioned history-confidence tier from all decision-eligible adjusted sessions available as of the ranking date while keeping the frozen 61-session research formula unchanged.

#### Scenario: ETF has 61 to 119 sessions
- **WHEN** an ETF has between 61 and 119 eligible adjusted sessions
- **THEN** it is marked `provisional_short_history`, may appear in the research rank, and is not actionable

#### Scenario: ETF has 120 to 249 sessions
- **WHEN** an ETF has between 120 and 249 eligible adjusted sessions
- **THEN** it is marked `standard_history` and may pass the history portion of actionable eligibility

#### Scenario: ETF has at least 250 sessions
- **WHEN** an ETF has at least 250 eligible adjusted sessions
- **THEN** it is marked `full_history_context` without receiving an automatic score increase

#### Scenario: Backtest period is evaluated
- **WHEN** ranking evidence is replayed historically
- **THEN** the 61-session warm-up MUST NOT be represented as the replay date range or as proof of predictive reliability

### Requirement: Actionable Rank Fails Closed On Missing Execution Evidence
The actionable surface SHALL wrap an available finite `final_score_v3` result with at least 120 eligible adjusted sessions and the complete, fresh, same-session market-structure, execution, provider-health, and risk inputs required by its versioned product policy.

#### Scenario: All actionable gates pass
- **WHEN** an ETF has sufficient adjusted history, a complete finite `final_score_v3`, fresh mandatory execution fields, healthy provider evidence, and no cap violation
- **THEN** the system may publish an `actionable_rank_v1` row with its own rank and contract hash

#### Scenario: Mandatory actionable field is missing
- **WHEN** a mandatory bid/ask, IOPV, premium/discount, provider, freshness, or consensus field is missing or stale
- **THEN** the ETF has no actionable rank and the exact field-level exclusion is recorded

#### Scenario: Field is not applicable
- **WHEN** the versioned product policy explicitly declares a field `not_applicable` for that ETF type
- **THEN** the system records the policy identifier and does not treat `not_applicable` as a missing-field substitute

#### Scenario: Score is non-finite or violates a cap
- **WHEN** an actionable component is non-finite or a configured cap is violated
- **THEN** the ETF is excluded and the rejection appears in the run summary

### Requirement: Ranking Consumers Respect Surface Boundaries
The system SHALL use the research surface for discovery and explanation and SHALL use only actionable rows for portfolio allocation or rank-derived candidate email selection.

#### Scenario: User browses the ETF universe
- **WHEN** the `/short-term` workbench loads the default ETF list
- **THEN** it uses the cached research surface and shows action eligibility separately

#### Scenario: Portfolio is generated
- **WHEN** ETF observation allocation selects candidate ETFs
- **THEN** every non-zero-weight candidate references an eligible `actionable_rank_v1` row from the required as-of context

#### Scenario: Email candidate is selected by rank
- **WHEN** a workflow proposes an email because an ETF belongs to a top-ranked candidate set
- **THEN** the ETF MUST have an eligible actionable row and matching contract context

#### Scenario: Tracked-position risk alert is evaluated
- **WHEN** an existing holding triggers its independent risk-alert lifecycle
- **THEN** research-rank membership neither creates nor suppresses that alert, while the alert's own decision-data gates still apply

### Requirement: Full-Universe Ranking Is Bounded And Resumable
The system SHALL generate ranking surfaces in deterministic batches of at most 20 ETFs with one worker, batched database reads, an exclusive run lock, and a resumable cursor.

#### Scenario: Large ETF universe is ranked
- **WHEN** a ranking run processes the full active ETF universe
- **THEN** no batch contains more than 20 ETFs and the run records processed, eligible, excluded, failed, and remaining counts

#### Scenario: A batch is interrupted
- **WHEN** a command, database operation, or provider call reaches its hard timeout of at most 55 seconds
- **THEN** the run persists its cursor and error summary without starting a concurrent replacement run

#### Scenario: Workbench reads ranking
- **WHEN** the frontend requests ranking pages
- **THEN** the backend reads completed cached rows and does not recompute full-universe price history
