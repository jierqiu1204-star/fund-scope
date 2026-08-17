# asset-recommendations Specification

## Purpose
TBD - created by archiving change add-asset-recommendations. Update Purpose after archive.
## Requirements

### Requirement: Recommendation Runs Are Persisted
The system SHALL persist every asset recommendation generation attempt as a recommendation run with asset type, status, timestamps, data cutoff metadata, and diagnostic details.

#### Scenario: Successful run is stored
- **WHEN** the system completes a fund recommendation generation run
- **THEN** it stores a run with asset type `fund`, status `success`, start and finish timestamps, data cutoff metadata, and generation details

#### Scenario: Failed run is stored
- **WHEN** recommendation generation fails after the job has started
- **THEN** the system stores a run with status `failed` and an error message suitable for the admin job history

### Requirement: Recommendation Items Include Explainable Scores
The system SHALL persist each recommended candidate with rank, total score, score breakdown, rationale data, risk flags, and data freshness metadata.

#### Scenario: Ranked item has score details
- **WHEN** a recommendation run produces a candidate item
- **THEN** the item includes its rank, total score, per-group score breakdown, rationale data, risk flags, and data freshness metadata

#### Scenario: Missing data is visible
- **WHEN** a candidate has missing optional metrics
- **THEN** the item records missing metric names in its data freshness or risk metadata instead of hiding the gap

### Requirement: Fund Candidate Recommendations
The system SHALL generate fund candidate recommendations from deterministic metrics including valuation fit, portfolio allocation fit, cost efficiency, risk control, liquidity or size, and news risk.

#### Scenario: Fund candidates are ranked deterministically
- **WHEN** the same fund metric snapshot, portfolio snapshot, and recommendation profile are used twice
- **THEN** the system produces the same fund candidate ordering and scores both times

#### Scenario: Portfolio gap influences fund score
- **WHEN** a fund tracks an asset exposure that is below the user's target allocation
- **THEN** the fund's portfolio fit component increases relative to otherwise similar funds that do not fill that gap

#### Scenario: Fund with critical warning is flagged
- **WHEN** a fund has recent critical news or incomplete required metrics
- **THEN** the recommendation item includes a risk flag explaining the issue

### Requirement: Stock Watchlist Candidate Scoring
The system SHALL score stock watchlist candidates as screening results using deterministic metrics including quality, valuation, momentum, risk control, liquidity, and portfolio diversity.

#### Scenario: Stock candidates are scored without holdings support
- **WHEN** stock recommendation generation runs
- **THEN** the system scores stock candidates from stock universe, price, and fundamental data without creating stock transactions or stock holdings

#### Scenario: Stock candidate lacks required fundamentals
- **WHEN** a stock candidate lacks required fundamental data for the selected profile
- **THEN** the system excludes the stock from ranked results or marks it as insufficient data according to the profile rules

#### Scenario: Stock result is framed as observation
- **WHEN** the API returns a stock recommendation item
- **THEN** the item uses observation-oriented labels and does not include buy, sell, target-price, or expected-return fields

### Requirement: Safe Recommendation Language
The system SHALL frame recommendation outputs as screening and research aids rather than personalized trade instructions.

#### Scenario: API response includes disclaimer
- **WHEN** a client requests latest recommendations
- **THEN** the response includes a disclaimer that results are for research support and do not execute or instruct trades

#### Scenario: Prohibited fields are absent
- **WHEN** a recommendation item is returned by the API
- **THEN** it does not contain buy/sell commands, target prices, or expected-return forecasts

### Requirement: LLM Explains But Does Not Rank
The system SHALL allow LLM-generated text only as an optional explanation formatter over computed scores, rationales, and risk flags.

#### Scenario: LLM is unavailable
- **WHEN** recommendation generation cannot call the LLM
- **THEN** the system still persists deterministic rankings and structured rationale data

#### Scenario: LLM output cannot change score
- **WHEN** the LLM returns explanation text for a candidate
- **THEN** the candidate's rank, total score, and score breakdown remain the values computed by the deterministic scoring service

### Requirement: Recommendation APIs
The system SHALL expose API endpoints for listing recommendation runs, retrieving latest recommendations by asset type, and retrieving a specific run with ranked items.

#### Scenario: Latest fund recommendations are returned
- **WHEN** the client requests latest recommendations with asset type `fund`
- **THEN** the system returns the latest successful fund run and its ranked items

#### Scenario: Latest recommendations do not exist
- **WHEN** the client requests latest recommendations for an asset type with no successful run
- **THEN** the system returns an empty state response instead of a server error

#### Scenario: Run history is returned
- **WHEN** the client requests recommendation runs
- **THEN** the system returns recent runs ordered from newest to oldest

### Requirement: Recommendation Jobs
The system SHALL support manual and scheduled generation of asset recommendation metrics and recommendation runs through the existing job framework.

#### Scenario: Admin triggers recommendation job
- **WHEN** the admin calls the recommendation job run endpoint
- **THEN** the system generates recommendation metrics and recommendation runs and records job status in job history

#### Scenario: Scheduled recommendation job runs after source data jobs
- **WHEN** the daily scheduler executes recommendation jobs
- **THEN** recommendation generation runs after fund NAV, valuation, holdings snapshot, and news jobs have had an opportunity to update source data

### Requirement: Recommendation Frontend View
The system SHALL provide a frontend recommendation workspace with separate fund and stock views, latest run status, ranked candidates, score breakdowns, rationales, risk flags, and empty/error states.

#### Scenario: User reviews fund recommendations
- **WHEN** the user opens the recommendations page and selects the fund view
- **THEN** the UI displays the latest fund recommendation run, ranked fund candidates, scores, rationales, and risk flags

#### Scenario: User reviews stock candidates
- **WHEN** the user opens the recommendations page and selects the stock view
- **THEN** the UI displays stock candidates as watchlist screening results with observation-oriented labels
#### Scenario: No recommendations exist
- **WHEN** the frontend receives an empty recommendation response
- **THEN** it displays an actionable empty state explaining that the recommendation job has not produced results yet

### Requirement: Stock recommendation prices use authoritative A-share facts
The system SHALL calculate stock watchlist price metrics from decision-eligible, total-return-adjusted A-share facts with explicit provider, adjustment, and receipt identities, and SHALL NOT create local synthetic price history.

#### Scenario: Authoritative facts are available
- **WHEN** a stock watchlist metric run has sufficient compatible A-share adjusted facts at its cutoff
- **THEN** the system calculates price metrics from those facts and records the source as A-share PIT adjusted evidence

#### Scenario: Authoritative facts are unavailable
- **WHEN** a stock lacks sufficient compatible A-share adjusted facts
- **THEN** the system marks the price-dependent metrics as insufficient data instead of seeding or substituting local price rows

### Requirement: Legacy stock price storage is retired safely
The system SHALL remove the legacy stock price-history schema only after all runtime and test consumers use the authoritative A-share fact source.

#### Scenario: Upgrade removes the old table
- **WHEN** the migration is applied after consumer migration
- **THEN** `stock_price_history` no longer exists while stock universe, fundamental, metric, and recommendation records remain available

#### Scenario: Rollback is required
- **WHEN** the migration is downgraded
- **THEN** the legacy table schema is recreated without changing the authoritative A-share fact tables
