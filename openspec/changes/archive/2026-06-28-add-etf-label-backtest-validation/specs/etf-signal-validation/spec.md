## ADDED Requirements

### Requirement: ETF signal validation supports historical replay
The system SHALL support historical replay validation for ETF observation labels and entry timing labels, using only data available on or before each replay date.

#### Scenario: Historical replay reconstructs labels
- **WHEN** a historical replay validation run evaluates an ETF on a replay date
- **THEN** it reconstructs the observation label, entry timing label, score inputs, and data reliability using only prices and metrics dated on or before that replay date

#### Scenario: Future data is unavailable during reconstruction
- **WHEN** a metric or source value would require data after the replay date
- **THEN** the validator excludes that value from label reconstruction and records a no-lookahead exclusion reason

#### Scenario: Historical replay records rule version
- **WHEN** a historical replay validation run is created
- **THEN** it records the signal rule version, replay date range, universe source, horizons, and data coverage summary

### Requirement: ETF historical replay records forward outcomes
The system SHALL calculate forward ETF outcomes after each replayed signal for configured 1, 3, 5, and 10 trading-day horizons.

#### Scenario: Forward outcome is completed
- **WHEN** the replayed signal has enough future decision-eligible ETF daily prices for a horizon
- **THEN** the system records forward return, maximum adverse excursion, maximum favorable excursion, horizon end date, and price source

#### Scenario: Forward outcome is not completed
- **WHEN** the replayed signal lacks enough future decision-eligible ETF daily prices for a horizon
- **THEN** the system excludes that sample for the horizon and records the missing-data reason

#### Scenario: Replay uses daily close only
- **WHEN** historical replay calculates entry and future prices
- **THEN** it uses verified ETF daily close data and MUST NOT use stale intraday quotes, estimated prices, or AI-generated values

### Requirement: ETF historical replay aggregates label evidence
The system SHALL aggregate historical replay outcomes by observation label, entry timing label, horizon, asset type, and rule version.

#### Scenario: Label combination is summarized
- **WHEN** replay samples exist for a label combination and horizon
- **THEN** the system records sample count, average return, median return, win rate, worst forward drawdown, median maximum favorable excursion, data coverage, and confidence state

#### Scenario: Label combination has insufficient samples
- **WHEN** a label combination has fewer than the configured minimum completed samples
- **THEN** the system marks the evidence as `insufficient` and MUST NOT expose it as reliable evidence

#### Scenario: Recent evidence weakens
- **WHEN** recent replay outcomes are materially worse than the longer replay summary for the same label combination
- **THEN** the system marks the label evidence as recently weakened and includes the affected horizon

### Requirement: Historical replay evidence remains research-only
The system SHALL keep historical replay validation separate from live ranking, portfolio allocation, tracked-position alerts, and notification sending.

#### Scenario: Historical replay finds weak evidence
- **WHEN** a label combination has weak historical replay outcomes
- **THEN** the system displays the evidence but does not automatically rewrite current labels, scores, portfolio weights, or email alert decisions

#### Scenario: Historical replay run completes
- **WHEN** a historical replay validation run finishes successfully
- **THEN** it updates validation evidence only and MUST NOT create tracked-position alerts or notification logs
