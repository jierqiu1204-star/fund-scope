## MODIFIED Requirements

### Requirement: ETF signal outcomes are validated against forward returns
The system SHALL validate exact-contract ETF ranking and label outcomes against future 1, 3, 5, and 10 trading-day total-return-aware outcomes using point-in-time decision-eligible historical data and a versioned execution-cost model.

#### Scenario: Validation run computes label outcomes
- **WHEN** a validation run processes compatible historical ETF snapshot items with enough future adjusted prices
- **THEN** it records independent signal-date count, asset count, coverage, average and median net return, paired excess return, win rate, worst forward drawdown, horizon, execution model, costs, and contract identity

#### Scenario: Missing future price excludes a sample
- **WHEN** a signal item does not have decision-eligible compatible future prices for a horizon
- **THEN** the system excludes that sample from that horizon and records the code, date, and exclusion reason

#### Scenario: Historical score source is incompatible
- **WHEN** a source item lacks the score field declared by its ranking contract or belongs to a partial, legacy, or mismatched run
- **THEN** it is excluded before Top-N or baseline construction and is not replaced by another stored score

### Requirement: Validation confidence is explicit
The system SHALL report sample sufficiency separately from effect direction using independent signal dates, completed windows, coverage, freshness, and uncertainty rather than treating raw asset-row count as confidence or positive evidence.

#### Scenario: Label has too few independent samples
- **WHEN** a label or Top-N comparison has fewer than 20 non-overlapping completed signal dates or less than 95 percent required coverage
- **THEN** the validation result is marked `insufficient` and MUST NOT be displayed as reliable evidence

#### Scenario: Validation data is stale
- **WHEN** the latest validation run is older than the configured freshness window
- **THEN** the UI and API indicate that validation evidence is stale

#### Scenario: Confidence interval crosses zero
- **WHEN** sufficient evidence has a 95 percent date-block bootstrap interval that includes zero
- **THEN** effect direction is `inconclusive` and sample sufficiency remains a separate field

#### Scenario: Evidence is negative
- **WHEN** the entire paired excess-return confidence interval is below zero
- **THEN** effect direction is `negative` and the result MUST NOT be converted into a positive score contribution

### Requirement: Validation does not alter live labels automatically
The system SHALL keep all validation and backtest evidence separate from live ranking, allocation, tracking, risk-alert, and notification domains and MUST NOT automatically change any current decision output based on validation results.

#### Scenario: Validation shows weak evidence
- **WHEN** a label combination or Top-N bucket has poor historical forward outcomes
- **THEN** the system displays the weak evidence but does not rewrite the current label, score, or rank

#### Scenario: Validation run completes
- **WHEN** forward, replay, label, score-bucket, backtest, or comparison validation finishes
- **THEN** current portfolio weights, tracked positions, alert records, notification records, signal values, and their update timestamps remain unchanged

#### Scenario: Signal generation runs later
- **WHEN** a new ranking snapshot is generated after validation exists
- **THEN** signal generation does not read mutable latest-validation output as a score-bearing input

### Requirement: ETF signal validation is grouped by evidence contract
ETF signal validation SHALL isolate historical outcomes by ranking contract hash, source snapshot scope, universe hash, score field, score/rule versions, price basis, reliability policy, observation label, entry timing label, horizon, asset bucket, and signal date.

#### Scenario: Label outcome is calculated
- **WHEN** ETF label validation calculates forward returns and drawdowns
- **THEN** it uses only compatible full-scope source snapshots and records every grouping identity in the result

#### Scenario: Current signal version changes
- **WHEN** the score, rule, universe, price basis, or ranking contract changes
- **THEN** old validation results are not merged with or presented as current-version validation

#### Scenario: Source contract hash is missing
- **WHEN** a historical run has no persisted contract hash
- **THEN** it remains legacy and cannot enter a current-contract aggregation

## ADDED Requirements

### Requirement: Score-Bucket Validation Uses The Declared Final Score Only
Top-N and score-bucket validation SHALL read only the finite score field declared by each compatible full ranking snapshot and SHALL NOT fall back to `total_score`, opportunity score, technical score, or another version.

#### Scenario: Legacy item has a high total score
- **WHEN** an item has `total_score=99` but lacks the declared current final-score field
- **THEN** it is counted under `unavailable_final_decision_score` and does not enter Top-N or `all_scored`

#### Scenario: Later partial run exists on the same date
- **WHEN** a code or theme-scoped run follows the compatible full snapshot
- **THEN** validation continues using the full snapshot and records the partial run as ineligible

#### Scenario: Score is NaN or infinite
- **WHEN** the declared score field is non-finite
- **THEN** the item is excluded with a non-finite score reason before ordering

### Requirement: Top-N Validation Uses Date-Level Paired Portfolios
For each source date and horizon, the system SHALL construct one equal-weight Top-N portfolio observation and one same-universe `all_scored` baseline observation before aggregating across dates.

#### Scenario: One date contains many ETFs
- **WHEN** a Top-10 source date has ten ETF rows
- **THEN** those rows form one date-level portfolio observation and do not count as ten independent time samples

#### Scenario: Same rows are duplicated
- **WHEN** duplicate ETF rows for one date are introduced into a test fixture
- **THEN** the independent signal-date count does not increase and duplicates are rejected or deduplicated explicitly

#### Scenario: Horizon windows overlap
- **WHEN** candidate signal dates would create overlapping five- or ten-day outcome windows
- **THEN** the primary validation uses non-overlapping dates for that horizon and reports skipped overlapping dates

### Requirement: Validation Execution Model Is Conservative And Versioned
The primary ETF validation SHALL model a signal generated after trade-date close, entry at the next trading day's eligible adjusted close, exit after the declared trading-day horizon, and fixed versioned two-sided fee and slippage assumptions.

#### Scenario: Future outcome is calculated
- **WHEN** a source snapshot on date T has compatible prices through horizon H
- **THEN** net return uses T+1 entry, the versioned H-day exit definition, and the stored fee/slippage parameters

#### Scenario: T+1 entry is unavailable
- **WHEN** the next trading-day eligible entry price is missing
- **THEN** the observation is excluded and the validator does not substitute the signal-date close

### Requirement: Primary Validation Endpoint Is Predeclared
The system SHALL treat Top 10 five-trading-day paired net excess return versus `all_scored` as the primary comprehensive-ranking endpoint and SHALL label other Top-N sizes and horizons exploratory.

#### Scenario: Validation summary is displayed
- **WHEN** a completed validation includes Top 5, 10, 20, and 50 across multiple horizons
- **THEN** the API and UI identify the primary endpoint and do not present the best exploratory combination as if it were preselected

### Requirement: Historical Replay Uses Point-In-Time Universe Membership
Historical validation SHALL use the universe membership and eligibility known on each source date, including products that later delisted, liquidated, or became ineligible.

#### Scenario: ETF later leaves the universe
- **WHEN** an ETF was eligible on a historical signal date but is inactive today
- **THEN** it remains in that date's expected universe and coverage denominator

#### Scenario: Current ETF did not yet exist
- **WHEN** an ETF is active today but was not listed or eligible on a historical signal date
- **THEN** it is absent from that historical source universe

### Requirement: Validation Reports Uncertainty And Exclusions
Every validation result SHALL report independent sample dates, asset count, data and universe coverage, mean and median net outcomes, paired excess outcomes, win rate, drawdown, turnover, cost assumptions, 95 percent date-block bootstrap interval, contract identity, and exclusion counts by reason.

#### Scenario: Consumer inspects evidence quality
- **WHEN** validation output is returned by API or shown in the workbench
- **THEN** the consumer can distinguish insufficient, inconclusive, negative, and supportive evidence without inferring confidence from raw sample count alone
