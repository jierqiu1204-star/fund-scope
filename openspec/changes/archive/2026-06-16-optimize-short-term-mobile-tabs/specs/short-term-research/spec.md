## ADDED Requirements

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
