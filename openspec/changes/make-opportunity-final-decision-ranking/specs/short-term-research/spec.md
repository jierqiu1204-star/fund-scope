## ADDED Requirements

### Requirement: Comprehensive Focus Ranking Uses Final Decision Score
The short-term research workbench SHALL treat `综合关注` as the final decision-support ranking for ETF research, using the current final score that combines cross-sectional strength, dynamic thresholds, label evidence, data reliability, liquidity, premium/discount risk, and theme or sector context.

#### Scenario: User sorts ETFs by comprehensive focus
- **WHEN** the user selects `综合关注` on `/short-term`
- **THEN** ETFs are ordered by the final decision score rather than by theme catalyst or sector heat alone

#### Scenario: Theme heat is strong but entry timing is risky
- **WHEN** an ETF has high sector or theme heat but its entry timing is `冲高别追`, `跌破等待`, `放量转弱`, or data is not decision-eligible
- **THEN** the risky entry timing and data status MUST reduce or cap its comprehensive focus ranking

#### Scenario: Theme score remains explanatory
- **WHEN** an ETF has theme catalyst or sector trend data
- **THEN** the system MAY show those values as supporting explanation but MUST NOT label them as the comprehensive ranking itself

#### Scenario: Historical evidence is generated
- **WHEN** the system validates Top 5, Top 10, Top 20, or Top 50 historical outcomes for the short-term workbench
- **THEN** the validation MUST use the same comprehensive focus ranking used by the current `/short-term` ETF list
