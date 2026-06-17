## ADDED Requirements

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
