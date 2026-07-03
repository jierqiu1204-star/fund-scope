## ADDED Requirements

### Requirement: Short-Term Research Separates Technical Score From Opportunity Score
The short-term research workbench SHALL expose the existing short-term `total_score` separately from the new `opportunity_score` so users can distinguish technical entry quality from theme attention.

#### Scenario: Ranked ETF has catalyst data
- **WHEN** an ETF ranking item includes catalyst scoring data
- **THEN** the API returns both `total_score` and `opportunity_score` with a breakdown showing technical, catalyst, and sentiment/heat components

#### Scenario: Ranked ETF has no catalyst data
- **WHEN** an ETF ranking item has no active catalyst snapshot
- **THEN** the API still returns the existing deterministic ranking fields and marks catalyst evidence as unavailable or neutral

#### Scenario: User views scores in UI
- **WHEN** `/short-term` renders an ETF card or detail panel
- **THEN** the UI labels the existing score as technical or short-term score and labels the new score as comprehensive attention or opportunity score

### Requirement: Short-Term Research Shows Catalyst Summary With Risk Context
The short-term research workbench SHALL display theme catalyst summaries together with entry timing and risk labels.

#### Scenario: Strong catalyst with chase risk
- **WHEN** a selected ETF has strong theme catalyst evidence and `entry_timing_label` is `冲高别追`
- **THEN** the UI states that the theme is strong but the short-term entry is overheated or needs waiting, without hiding the chase-risk label

#### Scenario: Catalyst supports attention
- **WHEN** a selected ETF has active high-confidence theme catalysts and normal data reliability
- **THEN** the UI shows the catalyst summary, event count, source type, and effective date range as research evidence

#### Scenario: Catalyst evidence is limited
- **WHEN** catalyst evidence is stale, manual-only, proxy-based, or unavailable
- **THEN** the UI shows the limitation near the catalyst summary and avoids confident wording

### Requirement: Short-Term Research Sorts By Opportunity Without Replacing Risk Filters
The short-term research workbench SHALL allow opportunity-aware sorting or highlighting while preserving existing label filters, risk labels, and entry timing filters.

#### Scenario: User sorts by opportunity score
- **WHEN** the user selects an opportunity score sort mode
- **THEN** the ranked list orders visible ETFs by `opportunity_score` while still showing the original technical score, observation label, and entry timing label

#### Scenario: User filters by entry timing
- **WHEN** the user filters for entry timing labels such as `健康回踩` or `冲高别追`
- **THEN** the filter applies to the original entry timing label and is not bypassed by catalyst strength

#### Scenario: High opportunity asset is data-limited
- **WHEN** an ETF has high theme catalyst score but data or liquidity limitations
- **THEN** the UI keeps the data or liquidity limitation visible and does not style the ETF as decision-ready

### Requirement: Short-Term Research Avoids Catalyst-Based Trading Instructions
The short-term research workbench SHALL describe catalyst-enhanced results as research attention and MUST NOT present catalyst scores as direct buy, sell, target price, or guaranteed-profit instructions.

#### Scenario: Catalyst score is displayed
- **WHEN** `/short-term` displays `opportunity_score` or catalyst evidence
- **THEN** the surrounding text uses observation language such as `重点观察`, `主题催化`, or `等待买点` and avoids direct trade instructions

#### Scenario: AI-assisted catalyst explanation is shown
- **WHEN** a catalyst explanation includes AI-assisted text
- **THEN** the UI states that AI summarizes structured evidence and does not alter rankings, labels, portfolio weights, or alerts

#### Scenario: Strong catalyst is shown in mobile view
- **WHEN** a mobile user opens the ranked list or detail tab for an ETF with strong catalysts
- **THEN** the mobile UI keeps the catalyst summary concise and still shows the risk or entry timing label without overlap
