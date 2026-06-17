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

### Requirement: Short-Term Research Uses A Workbench Layout
The system SHALL present `/short-term` as a research workbench with a compact status area, a paginated ranked list, a focused selected-asset detail area, and a separate tracked-holdings observation area.

#### Scenario: User opens short-term research on desktop
- **WHEN** the user opens `/short-term` on a desktop viewport
- **THEN** the page shows mode switching, latest data status, update actions, ranked assets, selected asset details, and tracked holdings without requiring the user to scroll through unrelated advanced strategy content first

#### Scenario: User opens short-term research on mobile
- **WHEN** the user opens `/short-term` on a mobile viewport
- **THEN** the same workbench content is shown in a single-column order that keeps mode, data status, ranked list, selected asset detail, and tracked holdings readable without overlapping text or controls

### Requirement: Ranked Asset Cards Are Compact And Scannable
The system SHALL render each ranked asset card as a compact decision summary rather than a long explanatory article.

#### Scenario: Ranked card is rendered
- **WHEN** a ranked asset appears in the list
- **THEN** the card shows rank, asset name, code, asset type, score, observation label, key return and risk metrics, and one short reason

#### Scenario: Details are available without bloating the list
- **WHEN** the user selects a ranked asset
- **THEN** longer explanations, charts, risk flags, investment direction, data quality notes, and opposing-view text appear in the selected asset detail area rather than expanding every list card

### Requirement: Asset Detail Separates Evidence From Actions
The system SHALL organize selected asset detail into clear evidence sections and SHALL distinguish observation labels from tracked-position handling signals.

#### Scenario: User selects an asset
- **WHEN** the user selects an ETF or fund from the ranked list
- **THEN** the detail area shows current conclusion, reason, key charts, recent return windows, drawdown evidence, risk explanation, and data limitations in Chinese

#### Scenario: Observation score is explained
- **WHEN** the selected asset has a high score or a high-watch label
- **THEN** the UI explains that score and observation labels mean “worth observing” and do not decide whether an already-held position must continue to be held

### Requirement: Tracked Holdings Are Presented As Position Management
The system SHALL present user-marked bought assets as a separate holding observation workflow focused on current P/L, holding status, latest alert, and email delivery status.

#### Scenario: User has tracked holdings
- **WHEN** active tracked positions exist
- **THEN** the page shows each tracked holding with asset name, code, order date, entry price date, current price, estimated P/L, holding days, position handling status, latest alert reason, and whether an email was sent or skipped

#### Scenario: Data-only warning exists
- **WHEN** a tracked holding has a data quality warning such as stale quote or missing IOPV but no actionable exit signal
- **THEN** the UI shows it as a web-only data提示 and does not style it as a sell or reduce-position reminder

#### Scenario: Actionable exit signal exists
- **WHEN** a tracked holding has hard stop, trailing take-profit, trend weakening, or exit-watch signal
- **THEN** the UI highlights it as a sell/reduce-position or stop-loss reminder and shows the specific reason and email status

### Requirement: Advanced And Operational Controls Are De-Emphasized
The system SHALL keep data update and advanced strategy operations available without making them dominate the beginner short-term research workflow.

#### Scenario: User needs to refresh data
- **WHEN** the user needs to update data or regenerate rankings
- **THEN** the page provides clear web buttons and visible task status while keeping the ranked list and selected detail as the primary visual focus

#### Scenario: User needs advanced strategy features
- **WHEN** the user wants backtests, simulation, reliability evaluation, or strategy configuration
- **THEN** the UI points to advanced strategy areas without embedding those controls as primary content in the short-term research workbench

### Requirement: Mobile Short-Term Research Uses Tabbed Workbench
The system SHALL render `/short-term` as a concise mobile tabbed workbench on narrow screens, while preserving the existing desktop short-term research layout on wide screens.

#### Scenario: User opens short-term page on mobile
- **WHEN** the user opens `/short-term` on a mobile-width viewport
- **THEN** the page shows a compact top summary and a tab control with `榜单`, `详情`, `追踪`, and `说明`

#### Scenario: Mobile page defaults to ranked list
- **WHEN** mobile `/short-term` finishes loading
- **THEN** the `榜单` tab is selected by default and shows the ranked asset list without requiring the user to scroll through detail, chart, tracking, or explanation sections first

#### Scenario: Selecting ranked asset opens detail
- **WHEN** the user taps an asset in the mobile `榜单` tab
- **THEN** the selected asset is stored and the mobile view switches to the `详情` tab

#### Scenario: Tracking is reachable without long scroll
- **WHEN** the user opens the mobile `追踪` tab
- **THEN** the page shows active tracked positions, current estimated profit/loss, holding status, latest alert status, and close/stop tracking controls without requiring the user to scroll through the full ranking or chart sections

#### Scenario: Explanations are separated from primary workflow
- **WHEN** the user opens the mobile `说明` tab
- **THEN** the page shows lower-frequency content such as charts, AI/rule explanations, data limitations, observation portfolio, and data issue notes

#### Scenario: Desktop layout remains unchanged
- **WHEN** the user opens `/short-term` on a desktop-width viewport
- **THEN** the page keeps the existing two-column workbench behavior, including the left ranked list and right detail/explanation column

### Requirement: Observation Labels And Holding Actions Are Visually Separate
The system SHALL distinguish short-term observation labels from tracked-position handling signals in API responses and the `/short-term` UI.

#### Scenario: Ranked asset has high score
- **WHEN** a ranked fund or ETF has a high score and an observation label such as `短线观察`, `重点观察`, or `高位观察`
- **THEN** the UI explains that the label means the asset is worth observing and does not decide whether an already-held position must continue to be held

#### Scenario: Tracked position has exit signal
- **WHEN** a tracked position for the same asset has `hard_stop`, `trailing_take_profit`, `trend_weakening`, or `exit_watch`
- **THEN** the UI shows the holding action separately from the ranked asset label with the specific holding reason

#### Scenario: High-position warning is not an email signal
- **WHEN** a tracked position is profitable and its ranked asset label is `高位观察` but no exit threshold is breached
- **THEN** the UI shows a web-only profit-watch or high-position note and does not present it as a sent sell email

### Requirement: Asset Detail Explains Trend Position Risk And Drawdown Together
The system SHALL show trend strength, recent position risk, volatility, drawdown, and tracked-position exit state together for the selected asset.

#### Scenario: Strong trend with large historical drawdown
- **WHEN** an asset has positive 5-day, 20-day, and 60-day or longer returns and also has a notable recent maximum drawdown
- **THEN** the detail view explains that trend is strong but historical pullback risk exists, and indicates whether the user's own position has breached any exit threshold

#### Scenario: User has no tracked position
- **WHEN** the selected asset has no active tracked position
- **THEN** the detail view shows observation evidence and risk only, without showing sell-or-reduce wording

#### Scenario: User has active tracked position
- **WHEN** the selected asset has an active tracked position
- **THEN** the detail view includes entry price or confirmed NAV, current estimated profit/loss, maximum profit, giveback, active threshold values, and latest holding action

### Requirement: ETF Ranking Uses The Unified Short-Term Research Source
The system SHALL use `/api/short-research` as the canonical source for ETF ranking and explanation on the short-term page.

#### Scenario: Short-term ETF list is loaded
- **WHEN** the `/short-term` page loads ETF mode
- **THEN** ranked ETF cards, detail explanations, and observation portfolio are based on the latest `/api/short-research` signal run

#### Scenario: Legacy short ETF endpoint is called
- **WHEN** a client calls the legacy `/api/short-etf` endpoints
- **THEN** the system preserves compatibility while avoiding a conflicting scoring explanation from being shown as the primary short-term workbench result

