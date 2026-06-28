## ADDED Requirements

### Requirement: ETF label dashboard shows historical replay evidence
The ETF label validation dashboard SHALL show historical replay evidence for ETF observation labels and entry timing labels when replay results are available.

#### Scenario: Historical replay evidence is available
- **WHEN** the user views an ETF label combination with completed historical replay results
- **THEN** the UI shows replay date range, rule version, sample count, horizon, median forward return, win rate, worst forward drawdown, confidence state, and data coverage

#### Scenario: Historical replay evidence is insufficient
- **WHEN** historical replay evidence has too few samples, stale data, or low coverage
- **THEN** the UI labels it as `样本不足` or `覆盖不足` and MUST NOT describe the label as historically reliable

#### Scenario: Historical replay evidence is missing
- **WHEN** no historical replay validation run exists
- **THEN** the UI shows a Chinese empty state that asks the user to run historical replay validation before trusting label evidence

### Requirement: ETF label dashboard separates replay and forward evidence
The ETF label validation dashboard SHALL visually and textually separate historical replay evidence from real forward validation evidence.

#### Scenario: Both evidence tracks exist
- **WHEN** both historical replay and real forward validation results exist for a label combination
- **THEN** the UI shows them in separate sections named `历史回放` and `真实前瞻`

#### Scenario: Forward evidence is still pending
- **WHEN** historical replay evidence exists but real forward validation has too few completed samples
- **THEN** the UI may show historical replay statistics while clearly stating that real forward validation is still accumulating

#### Scenario: Evidence tracks disagree
- **WHEN** historical replay evidence is favorable but real forward evidence is weak or insufficient
- **THEN** the UI shows a cautious conclusion and MUST NOT present the label as fully validated

### Requirement: ETF label evidence explains limitations
The ETF label validation dashboard SHALL explain the limitations of historical replay evidence in beginner-readable Chinese.

#### Scenario: User views replay evidence
- **WHEN** historical replay evidence is displayed
- **THEN** the UI explains that replay uses current rules on past data, may have universe and rule-version bias, and does not guarantee future performance

#### Scenario: User views a live ETF label
- **WHEN** a ranked ETF displays label validation evidence on `/short-term`
- **THEN** the page states whether the evidence source is historical replay, real forward validation, or both
