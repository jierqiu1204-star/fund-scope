## ADDED Requirements

### Requirement: ETF Holding Thresholds Use Dynamic Context
Tracked ETF positions SHALL use the dynamic ETF threshold context when calculating hard stop, take-profit watch, trailing take-profit, and trend weakening.

#### Scenario: High-volatility held ETF is evaluated
- **WHEN** a tracked ETF has high recent volatility and decision-eligible fresh price data
- **THEN** the hard-stop and trailing-giveback thresholds are wider within configured safety bounds and the reason explains volatility input

#### Scenario: Low-volatility held ETF is evaluated
- **WHEN** a tracked ETF has low recent volatility and decision-eligible fresh price data
- **THEN** the hard-stop and trailing-giveback thresholds are tighter within configured safety bounds and the reason explains volatility input

### Requirement: Holding State Adjusts Profit Protection
Tracked ETF profit-protection thresholds SHALL consider the user's own entry price, holding days, current profit, highest profit, and giveback state.

#### Scenario: Position has high-water profit
- **WHEN** a tracked ETF reached a meaningful maximum profit and then gives back more than its dynamic giveback threshold
- **THEN** the system may trigger `trailing_take_profit` and explains highest profit, current profit, giveback, threshold, and price source

#### Scenario: Position has no meaningful profit
- **WHEN** a tracked ETF has not reached the dynamic profit-protection start line
- **THEN** the system does not trigger trailing take-profit solely because the ranked asset label changes

### Requirement: Tracked ETF Premium Risk Is Web-Only Unless Paired With Actionable Signal
Tracked ETF premium/discount warnings SHALL be shown as risk context and SHALL NOT send email by themselves.

#### Scenario: Held ETF has high premium warning only
- **WHEN** a tracked ETF has high premium or missing IOPV but no hard stop, trailing take-profit, trend weakening, take-profit watch, or exit-watch signal
- **THEN** the system shows a web-only structure risk warning and does not send an email

#### Scenario: Premium risk combines with actionable signal
- **WHEN** a tracked ETF has an actionable holding signal and high premium risk context
- **THEN** the email and UI include premium context as supporting evidence without making premium alone the trigger

### Requirement: ETF Threshold Context Is Persisted In Alert Audit
Tracked ETF alert audit records SHALL persist dynamic threshold inputs and threshold values used for the decision.

#### Scenario: Alert is sent
- **WHEN** a tracked ETF sends `hard_stop`, `trailing_take_profit`, `trend_weakening`, `take_profit_watch`, or `exit_watch`
- **THEN** the alert audit includes volatility unit, threshold mode, threshold values, rule version, current price, quote time, current profit, highest profit, and giveback

#### Scenario: No alert is sent
- **WHEN** a tracked ETF does not send an email because thresholds are not crossed
- **THEN** the latest tracked position response exposes the closest relevant threshold and distance where available
