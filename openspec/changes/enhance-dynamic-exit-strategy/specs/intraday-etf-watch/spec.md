## MODIFIED Requirements

### Requirement: Dynamic Intraday Exit Signals Are Explainable
The system SHALL produce dynamic ETF sell-or-reduce reminders using volatility, trend, liquidity, and structure metrics instead of fixed percentages only.

#### Scenario: Dynamic hard stop triggers
- **WHEN** an active ETF tracking record's intraday estimated loss breaches its volatility-adjusted hard stop threshold
- **THEN** the system creates an urgent hard-stop reminder with the current loss, threshold, volatility unit, price source, and quote time

#### Scenario: Dynamic trailing profit triggers
- **WHEN** an active ETF tracking record has reached the dynamic profit-protection start level and then gives back more than the dynamic trailing threshold
- **THEN** the system creates a warning-level trailing-profit reminder with highest profit, current profit, giveback, threshold, price source, and quote time

#### Scenario: Trend weakening triggers
- **WHEN** an active ETF tracking record's intraday price breaks below short moving-average or VWAP proxy conditions and recent daily momentum is negative
- **THEN** the system creates a trend-weakening reminder explaining the broken trend conditions and whether intraday or daily-close data was used

#### Scenario: Structural risk does not become a command
- **WHEN** an ETF has wide spread, abnormal premium/discount, stale IOPV, or weak turnover but no loss/profit-protection breach
- **THEN** the system records a risk warning or watch-level signal without presenting it as a direct sell instruction and without sending a sell-or-reduce email

#### Scenario: Ranked status remains independent from holding action
- **WHEN** an ETF remains highly ranked during intraday monitoring but the tracked holding breaches an exit threshold
- **THEN** the system keeps the ranked observation label and tracked-position handling signal as separate fields and explanations

### Requirement: Intraday Watch Surfaces Dynamic Threshold Context
The system SHALL expose enough dynamic-threshold context for users to understand ETF intraday holding alerts.

#### Scenario: Tracked ETF card is displayed
- **WHEN** an active tracked ETF appears in the short-term page or quote API
- **THEN** the response includes current price, price source, quote freshness, estimated profit/loss, highest profit, giveback, hard-stop threshold, trailing threshold, trend status, and latest actionable or web-only alert status

#### Scenario: Quote is stale during market hours
- **WHEN** a tracked ETF quote is older than the freshness threshold during market hours
- **THEN** the UI marks the quote as stale and MUST NOT treat stale quote structure warnings as a sell-or-reduce email trigger
