## ADDED Requirements

### Requirement: ETF evidence surfaces remain visually separate
The ETF workbench SHALL render production ranking, research replay, policy shadow, live notification, provider delivery, and user-confirmed execution as distinct evidence surfaces.

#### Scenario: Research replay exists without live evidence
- **WHEN** a research replay or policy shadow is complete but no live notification or confirmed execution sample exists
- **THEN** the UI labels the research result as simulated and shows live evidence as unavailable

#### Scenario: Legacy evidence is returned
- **WHEN** evidence lacks a compatible manifest or provenance contract
- **THEN** the UI labels it as legacy or incompatible and does not merge it with current evidence

### Requirement: ETF evidence API and UI expose stable availability states
The ETF evidence API and workbench SHALL expose stable reasons for absent production snapshots, absent replay materialization, incompatible manifests, point-in-time universe gaps, adjusted-price gaps, insufficient score coverage, insufficient independent dates, pending future windows, missing entry or exit prices, missing live notifications, and missing confirmed execution.

#### Scenario: Promotion sample is short
- **WHEN** ranking evidence has fewer than 252 eligible point-in-time sessions or 40 independent primary dates
- **THEN** the UI shows `样本不足` with exact counts and MUST NOT describe the candidate as validated

#### Scenario: Coverage gate fails
- **WHEN** either production decision-data or score coverage is below 95 percent
- **THEN** the UI shows `覆盖不足`, preserves the current production ranking state, and does not imply that fallback data can repair the result

### Requirement: ETF evidence presentation identifies primary and exploratory results
The ETF workbench SHALL label Top10 five-session paired net excess as the ranking primary endpoint and Top20 ten-session action-cycle benefit as the policy primary endpoint.

#### Scenario: User views other horizons
- **WHEN** Top5/20 or 1/3/10-session ranking results are displayed
- **THEN** they are labeled exploratory and cannot be presented as the promotion decision
