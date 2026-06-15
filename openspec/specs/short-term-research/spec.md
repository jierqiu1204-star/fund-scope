# short-term-research Specification

## Purpose
TBD - created by archiving change add-short-term-research-home. Update Purpose after archive.
## Requirements
### Requirement: Short-Term Research Homepage Is The Main Beginner Workflow
The system SHALL provide a Chinese `/short-term` homepage focused on fund and ETF short-term research, and SHALL keep advanced strategy lab workflows separate from the beginner default path.

#### Scenario: User opens short-term homepage
- **WHEN** the user opens `/short-term`
- **THEN** the page shows latest data date, research universe size, observation counts, data health, ranked assets, filters, and a selected asset detail area in Chinese

#### Scenario: Strategy lab remains available
- **WHEN** the user needs advanced strategy, backtest, or simulation tools
- **THEN** the system still provides `/strategy-lab` without making it the primary beginner entry point

### Requirement: Unified Fund And ETF Short-Term Universe
The system SHALL maintain a unified short-term research universe containing eligible funds and ETFs, with asset type, code, name, theme labels, investment direction, trading rule, and data source status for each asset.

#### Scenario: Ineligible products are excluded
- **WHEN** the system seeds or refreshes the short-term research universe
- **THEN** one-year holding period products, closed-period products, fixed-open products, off-scope assets, and persistently missing-data assets are excluded from the active short-term ranking pool

#### Scenario: Universe contains both funds and ETFs
- **WHEN** the client requests the short-term research universe
- **THEN** the API returns fund and ETF items with a normalized asset type and beginner-readable investment direction

### Requirement: Public Data Sync Supports Short-Term Research
The system SHALL synchronize enough public market data to support short-term charts and ranking for the configured fund and ETF universe.

#### Scenario: ETF data is synchronized
- **WHEN** short-term data sync runs for an ETF
- **THEN** the system stores or updates daily open, high, low, close, volume, turnover, percentage change, provider, and latest date without duplicate asset/date rows

#### Scenario: Fund data is synchronized
- **WHEN** short-term data sync runs for a fund
- **THEN** the system stores or updates daily NAV and accumulated NAV without duplicate fund/date rows

#### Scenario: Data failure is visible
- **WHEN** one asset fails to sync from public data sources
- **THEN** the system records the readable failure reason while continuing to process other assets

### Requirement: Short-Term Scores Are Deterministic And Explainable
The system SHALL rank assets using deterministic short-term metrics and SHALL expose the score breakdown used for each rank.

#### Scenario: Ranking uses short-term windows
- **WHEN** the system generates short-term signals
- **THEN** it uses recent windows such as 5, 10, 20, and 60 trading days rather than long-term-only metrics

#### Scenario: Risk reduces conclusion strength
- **WHEN** an asset has chase risk, high volatility, large drawdown, stale data, low liquidity where applicable, or insufficient sample history
- **THEN** the conclusion is downgraded and the risk flags are returned with the ranked item

#### Scenario: Ranking does not require an API key
- **WHEN** no LLM or third-party AI API key is configured
- **THEN** the system still generates deterministic rankings, charts, labels, and rule-based explanations

### Requirement: Observation Labels Avoid Trading Instructions
The system SHALL use observation-only conclusion labels and SHALL NOT output buy, sell, stop-loss, take-profit, target price, expected return, or guaranteed-profit instructions.

#### Scenario: Ranked item uses allowed labels
- **WHEN** the API returns a short-term ranked item
- **THEN** its conclusion is one of `短线观察`, `高位观察`, `谨慎观察`, `不适合短线`, or `数据不足`

#### Scenario: Prohibited trade language is absent
- **WHEN** the frontend renders ranked results and asset details
- **THEN** the UI does not present any action as a direct buy, sell, stop-loss, take-profit, target price, or guaranteed-profit recommendation

### Requirement: Asset Detail Shows Charts And Beginner Explanations
The system SHALL provide a detail view for each ranked asset with charts, key metrics, investment direction, evidence, risks, and opposing-view explanation.

#### Scenario: ETF detail is opened
- **WHEN** the user selects an ETF from the short-term ranking
- **THEN** the detail view shows price trend, drawdown, turnover or liquidity evidence, recent return windows, theme tags, investment direction, trading rule, label reason, and risk explanation

#### Scenario: Fund detail is opened
- **WHEN** the user selects a fund from the short-term ranking
- **THEN** the detail view shows NAV trend, accumulated NAV where available, drawdown, recent return windows, category or theme, investment direction, label reason, and fund-specific short-term limitations

#### Scenario: Label reason is visible
- **WHEN** a ranked item has a conclusion such as `高位观察`
- **THEN** the detail view explains why that label was assigned using concrete metrics and risk flags rather than only showing the label

### Requirement: Short-Term Data Sufficiency Gates Replace Long-Term Gates
The system SHALL apply short-term data sufficiency gates suitable for 1-4 week and 2-3 month research, and SHALL not use the long-term 730-day strategy gate as the blocker for this homepage.

#### Scenario: Less than 20 trading days
- **WHEN** an asset has fewer than 20 usable trading days
- **THEN** it is marked `数据不足` and cannot appear as `短线观察`

#### Scenario: Between 20 and 59 trading days
- **WHEN** an asset has 20 to 59 usable trading days
- **THEN** the system may display its trend data but marks the sample as very short and limits the conclusion strength

#### Scenario: At least 120 trading days
- **WHEN** an asset has at least 120 usable trading days
- **THEN** the system may assign normal observation labels if other scoring and risk conditions allow it

### Requirement: Frontend Prioritizes Simple Charts Over Tables
The system SHALL make the short-term homepage chart-first and SHALL avoid exposing advanced backtest, strategy configuration, and paper trading controls as primary beginner content.

#### Scenario: Ranked list is shown
- **WHEN** short-term signals exist
- **THEN** the UI shows a compact ranked list with score, label, key reason, risk flags, and asset type before any raw table

#### Scenario: User changes filters
- **WHEN** the user filters by asset type, theme, or sort mode
- **THEN** the ranked list and selected detail update without requiring command-line operations

#### Scenario: No data exists
- **WHEN** no synchronized short-term data is available
- **THEN** the UI shows a clear Chinese empty state and a web button to run data preparation

