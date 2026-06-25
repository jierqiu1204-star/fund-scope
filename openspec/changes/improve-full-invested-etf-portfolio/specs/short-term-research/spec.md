## ADDED Requirements

### Requirement: Short-Term Workbench Shows Full-Invested ETF Portfolio Reference
The short-term research workbench SHALL display the ETF observation portfolio as a full-invested reference for the user's securities-account ETF capital when full-invested weights are available.

#### Scenario: Full-invested ETF portfolio exists
- **WHEN** the latest ETF observation portfolio has valid target weights
- **THEN** `/short-term` shows the weights as `全仓 ETF 观察组合参考` and shows that the weights sum to 100% of the ETF account capital

#### Scenario: Full-invested ETF portfolio is unavailable
- **WHEN** the backend cannot generate decision-eligible full-invested weights
- **THEN** `/short-term` shows a clear unavailable state and the reason rather than showing stale or partial weights as current guidance

### Requirement: Short-Term Workbench Explains Portfolio Construction
The short-term research workbench SHALL explain why each ETF receives its portfolio weight or why it is excluded from target weights.

#### Scenario: ETF has target weight
- **WHEN** a selected ETF is included in the latest full-invested observation portfolio
- **THEN** the detail panel shows target weight, primary reason, risk adjustment, correlation or theme constraint, and data reliability

#### Scenario: ETF is watch-only or excluded
- **WHEN** a selected ETF is ranked but not included in the portfolio weights
- **THEN** the detail panel shows the exclusion or watch-only reason without implying that the ETF is invalid for all future observation

### Requirement: Short-Term Workbench Avoids Whole-Asset Misinterpretation
The short-term research workbench SHALL distinguish securities-account ETF capital from the user's total assets.

#### Scenario: User views full-invested reference
- **WHEN** the UI uses terms such as full-invested or 100%
- **THEN** the UI states that this refers only to the money the user has chosen to allocate to securities-account ETF research
