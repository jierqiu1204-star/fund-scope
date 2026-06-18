## ADDED Requirements

### Requirement: Short-Term UI Explains AI And Rule-Based Dynamic Lines
The system SHALL explain on `/short-term` that AI reports are model-assisted explanations while ranking labels, dynamic exit thresholds, and email-triggering holding signals are rule-based.

#### Scenario: User views selected asset detail
- **WHEN** an asset detail includes AI or rule explanation and dynamic line values
- **THEN** the UI shows whether the explanation source is `AI生成` or `规则兜底`, and states that dynamic lines are calculated from market data and rules

#### Scenario: User views tracked holding
- **WHEN** a tracked holding card is displayed
- **THEN** the UI shows dynamic hard-stop line, take-profit-watch line, trailing-giveback line where available, current holding signal, and whether the latest alert sent email or was web-only

#### Scenario: Take-profit watch is displayed
- **WHEN** a tracked holding has `take_profit_watch`
- **THEN** the UI labels it as `止盈观察提醒`, explains why it triggered, and avoids wording that implies automatic or guaranteed selling
