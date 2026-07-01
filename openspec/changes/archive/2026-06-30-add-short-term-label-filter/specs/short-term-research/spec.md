## ADDED Requirements

### Requirement: Short-Term Workbench Supports Label Filtering
The short-term research workbench SHALL allow users to filter ranked ETF or fund cards by observation labels, entry timing labels, and tracked-position status.

#### Scenario: User filters by observation label
- **WHEN** the user selects one or more buy-observation labels such as `短线观察` or `高位观察`
- **THEN** the ranked list shows only assets matching at least one selected buy-observation label while preserving the selected sort mode

#### Scenario: User filters by entry timing
- **WHEN** the user selects one or more entry timing labels such as `健康回踩`, `趋势延续`, or `冲高别追`
- **THEN** the ranked list shows only assets matching at least one selected entry timing label

#### Scenario: User filters by holding status
- **WHEN** the user selects holding filters such as `我已持仓`, `触发提醒`, or `仅网页提示`
- **THEN** the ranked list shows only assets matching the selected tracked-position state for the current user

### Requirement: Label Filtering Is Separate From Sorting
The short-term research workbench SHALL distinguish label filtering from ranking sort order.

#### Scenario: User applies labels and sort
- **WHEN** the user selects label filters and then chooses a sort mode such as real-time score or drawdown
- **THEN** the system first applies the label filters and then orders the remaining assets by the selected sort mode

#### Scenario: Real-time score sort remains available
- **WHEN** label filtering is added to the workbench
- **THEN** the existing real-time score sort remains available as a sort option and is not removed

### Requirement: Label Filter Controls Follow Compact Geist Style
The short-term research workbench SHALL render label filters as compact controls consistent with the existing Geist-style workbench UI.

#### Scenario: User opens label filter
- **WHEN** the user clicks the label filter control
- **THEN** the UI shows grouped checkbox options for buy-observation labels, entry timing labels, and tracked-position labels without large decorative cards

#### Scenario: User has active filters
- **WHEN** one or more label filters are active
- **THEN** the UI shows compact chips for the selected filters and allows each selected filter to be removed

#### Scenario: User uses quick filter
- **WHEN** the user clicks a quick filter such as `稳妥观察`, `高位谨慎`, or `只看持仓`
- **THEN** the UI applies the corresponding label combination and shows the selected filter chips

### Requirement: Label Filter State Is Clear And Reversible
The short-term research workbench SHALL make active label filters visible and easy to clear.

#### Scenario: User clears filters
- **WHEN** the user clicks `清空`
- **THEN** all label filters are removed and the ranked list returns to the current mode, direction, search, range, and sort settings

#### Scenario: Filter returns no results
- **WHEN** active label filters produce no visible assets
- **THEN** the UI shows a Chinese empty state explaining that no assets match the selected labels and offers a clear-filter action

#### Scenario: User changes high-level filters
- **WHEN** the user changes asset mode, direction, search keyword, ETF range, or label filters
- **THEN** the ranked list resets to the first page to avoid empty pages caused by stale pagination

### Requirement: Label Filtering Avoids Trading Instructions
The label filter UI SHALL describe filters as observation states and MUST NOT present any label combination as a direct buy instruction.

#### Scenario: Conservative filter is selected
- **WHEN** the user selects `稳妥观察` or equivalent filters
- **THEN** the UI labels the result as observation candidates and does not use wording such as `推荐买入`, `可以买`, `必涨`, or `稳赚`

#### Scenario: High-position filter is selected
- **WHEN** the user selects high-position or chase-risk labels
- **THEN** the UI keeps risk wording visible and does not hide that these assets may be unsuitable for immediate chasing
