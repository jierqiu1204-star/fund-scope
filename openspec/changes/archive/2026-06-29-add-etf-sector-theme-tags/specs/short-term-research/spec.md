## ADDED Requirements

### Requirement: ETF Theme Filters Are Available On Short-Term Workbench
The short-term research workbench SHALL allow users to filter ETF rankings by normalized industry/theme labels.

#### Scenario: User filters by ETF theme
- **WHEN** the user selects a theme such as `半导体`, `人工智能`, `生物医药`, `证券`, `银行`, `红利`, `黄金`, `债券`, `纳指`, or `恒生科技`
- **THEN** the ranked ETF list updates to show matching ETFs without mixing unrelated themes

#### Scenario: User clears theme filter
- **WHEN** the user selects `全部方向`
- **THEN** the ETF ranking returns to the unfiltered paginated result

### Requirement: Theme Heat Is Visible In ETF Mode
The short-term research workbench SHALL show ETF theme heat statistics when theme data and ranking cache are available.

#### Scenario: Theme heat panel is displayed
- **WHEN** the user opens ETF mode and latest ETF signals exist
- **THEN** the page shows theme heat summaries including theme name, ETF count, average score, top ETF, latest change summary, and data freshness

#### Scenario: Theme heat is unavailable
- **WHEN** theme heat data is missing or the latest signal run is unavailable
- **THEN** the page shows a Chinese waiting or unavailable state rather than calculating expensive metrics on the client

### Requirement: Selected ETF Detail Shows Theme Evidence
The selected ETF detail area SHALL show normalized theme metadata and the evidence used for classification.

#### Scenario: User opens ETF detail
- **WHEN** the user selects an ETF from the ranked list
- **THEN** the detail view shows primary theme, secondary themes where present, theme group, classification confidence, and a short classification reason

#### Scenario: ETF is unclassified
- **WHEN** the selected ETF has unknown theme classification
- **THEN** the detail view clearly shows `未分类` and explains that theme-based portfolio constraints may treat it conservatively

### Requirement: Theme Filters Preserve Existing Workbench Behavior
Theme filtering SHALL preserve existing pagination, selected-detail behavior, tracked holdings, and mobile tabs.

#### Scenario: Filter changes current page
- **WHEN** the user changes a theme filter, search term, asset type, universe, or sort option
- **THEN** the ranked list resets to the first page and the selected detail remains valid or selects the first returned item

#### Scenario: Mobile user filters ETF themes
- **WHEN** the user uses theme filters on a mobile viewport
- **THEN** the controls fit without horizontal overflow and the selected ETF detail remains reachable through the existing mobile tabs
