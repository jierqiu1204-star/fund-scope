## ADDED Requirements

### Requirement: ETF Exit Thresholds Expose Rule Version
The tracked-position exit strategy SHALL expose the rule version and threshold inputs used for ETF hard stop, take-profit watch, trailing take-profit, and trend weakening.

#### Scenario: Tracked ETF is evaluated
- **WHEN** an active ETF tracked position is evaluated
- **THEN** the result includes rule version, volatility unit, hard-stop threshold, take-profit-watch threshold, trailing-giveback threshold, trend inputs, and current distance to each threshold

#### Scenario: Threshold uses fallback defaults
- **WHEN** asset-specific volatility thresholds cannot be calculated from eligible data
- **THEN** the result marks the threshold mode as conservative default and explains why it is not asset-specific

### Requirement: ETF Exit Signals Are Replayable
The tracked-position exit strategy SHALL persist enough threshold context to replay why an alert was or was not sent.

#### Scenario: Email alert is sent
- **WHEN** an ETF tracked-position email is sent
- **THEN** the alert audit stores entry price, current price, quote time, quote reliability, highest profit, current profit, giveback, threshold values, and trigger reason

#### Scenario: Email alert is not sent
- **WHEN** an ETF tracked position does not send an email because thresholds are not crossed or data is ineligible
- **THEN** the latest position snapshot exposes the main no-email reason for the UI
