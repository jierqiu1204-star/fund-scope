## ADDED Requirements

### Requirement: Tracked ETF exits can use approved calibrated parameters
The tracked-position exit strategy SHALL use approved ETF exit calibration parameters when a matching, current, evidence-sufficient parameter set exists.

#### Scenario: Approved bucket parameter is available
- **WHEN** a tracked ETF belongs to a bucket with an approved parameter set matching the current exit-rule contract
- **THEN** the exit strategy uses the approved hard-stop, take-profit-watch, trailing-start, trailing-giveback, and trend-confirmation parameters for that ETF

#### Scenario: No approved parameter exists
- **WHEN** no approved, current, evidence-sufficient parameter set matches the tracked ETF
- **THEN** the exit strategy continues using the existing conservative dynamic threshold rules

#### Scenario: Candidate is not approved
- **WHEN** a calibration parameter set has status candidate, rejected, expired, or evidence-insufficient
- **THEN** the exit strategy MUST NOT use it for live email-triggering decisions

### Requirement: Tracked ETF exit output identifies parameter source
The tracked-position exit strategy SHALL expose whether each ETF exit threshold came from calibrated parameters or the default dynamic rule.

#### Scenario: Calibrated parameter used
- **WHEN** a tracked ETF is evaluated with an approved calibrated parameter set
- **THEN** the result includes calibration run id, candidate id, bucket key, calibration version, and threshold source `approved_calibration`

#### Scenario: Default dynamic rule used
- **WHEN** the exit strategy uses the current default dynamic thresholds
- **THEN** the result includes threshold source `default_dynamic_rule` and explains why no approved calibration was used

### Requirement: Approved calibration does not bypass data eligibility
The tracked-position exit strategy SHALL enforce existing data reliability and email eligibility rules even when approved calibrated parameters exist.

#### Scenario: Stale quote with approved parameter
- **WHEN** a tracked ETF has approved calibrated thresholds but only stale or display-only price data
- **THEN** the strategy may show display context but MUST NOT send an actionable email

#### Scenario: Fresh intraday quote with approved parameter
- **WHEN** a tracked ETF has approved calibrated thresholds and a fresh decision-eligible intraday quote
- **THEN** the strategy may trigger an eligible exit email if the approved thresholds are crossed

### Requirement: Live exit rules remain deterministic
The tracked-position exit strategy SHALL calculate live exit signals deterministically from approved parameters, tracked position state, and eligible market data.

#### Scenario: AI explanation exists
- **WHEN** an AI or rule explanation is available for an exit decision
- **THEN** it may explain the deterministic decision but MUST NOT change thresholds, trigger state, email eligibility, or trade sizing

#### Scenario: Same inputs are evaluated twice
- **WHEN** the same tracked position, approved parameters, and market data are evaluated repeatedly
- **THEN** the strategy returns the same exit signal and threshold context except for idempotent audit timestamps
