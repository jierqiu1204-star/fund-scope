## ADDED Requirements

### Requirement: Dynamic Exit Lines Explain Threshold Source And Distance
The system SHALL show how each tracked-position dynamic exit line was calculated and how far the current position is from triggering it.

#### Scenario: Dynamic lines are returned
- **WHEN** a tracked position snapshot is returned
- **THEN** the response includes hard-stop threshold, take-profit-watch threshold, trailing-giveback threshold, trend-weakening state, threshold source, rule version, and current distance to each applicable line

#### Scenario: Threshold lacks enough data
- **WHEN** the system cannot compute an asset-specific dynamic threshold due to insufficient history or ineligible price data
- **THEN** it uses a conservative default or waiting status and explains that the threshold is not asset-specific yet

### Requirement: Profit Protection Adapts To Volatility And Existing Profit
The system SHALL make take-profit-watch and trailing-giveback thresholds responsive to recent volatility, drawdown behavior, and current maximum profit.

#### Scenario: High volatility receives wider giveback
- **WHEN** a tracked ETF has higher recent volatility or larger normal pullbacks
- **THEN** the trailing-giveback threshold is wider within configured safety bounds and the UI explains the volatility reason

#### Scenario: Larger profit receives stronger protection
- **WHEN** a tracked position has materially higher maximum profit after entry
- **THEN** the profit-protection explanation highlights highest profit, current profit, giveback, and whether the soft watch or trailing condition is close to triggering

### Requirement: Holding Signals Remain Separate From Buy Observation Labels
The system SHALL keep ranked asset observation labels separate from tracked-position handling signals.

#### Scenario: High ranked ETF triggers exit signal
- **WHEN** an ETF remains high ranked but the user tracked position breaches an exit threshold
- **THEN** the UI shows the ranked observation state separately from the holding signal and explains why both can be true

#### Scenario: High watch without threshold breach
- **WHEN** a tracked position is profitable and the asset is 高位观察 but no threshold is breached
- **THEN** the system shows a web-only caution and MUST NOT send a sell or reduce-position email
