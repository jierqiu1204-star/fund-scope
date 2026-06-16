## ADDED Requirements

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
