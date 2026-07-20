## ADDED Requirements

### Requirement: Approved Calibrated ETF Parameters Require Intraday Evidence
The tracked-position exit strategy SHALL use calibrated ETF exit parameters only when those parameters were approved from intraday-alert evidence that matches the current live rule contract.

#### Scenario: Approved intraday parameters are available
- **WHEN** an active tracked ETF is evaluated and a matching approved calibrated parameter set exists for its bucket
- **THEN** the exit strategy uses those parameters and returns the calibration run id, candidate id, bucket key, execution model, and contract hash in the threshold context

#### Scenario: Candidate is daily-close only
- **WHEN** the best available calibrated parameter was produced by `daily_close` evidence
- **THEN** the tracked-position exit strategy MUST NOT use it for live intraday email decisions

#### Scenario: Candidate is not approved
- **WHEN** a calibrated parameter set is `candidate`, `rejected`, `expired`, or `evidence_insufficient`
- **THEN** the tracked-position exit strategy keeps the existing dynamic default thresholds

#### Scenario: Contract does not match
- **WHEN** the approved parameter contract hash does not match the current live exit rule contract
- **THEN** the tracked-position exit strategy ignores the parameter and explains that the evidence is version-mismatched

### Requirement: Live Email Eligibility Remains Data-Gated
The tracked-position exit strategy SHALL keep live email eligibility gated by current fresh intraday data even when calibrated parameters exist.

#### Scenario: Fresh intraday quote crosses calibrated threshold
- **WHEN** a tracked ETF crosses an approved calibrated threshold using a fresh decision-eligible intraday quote
- **THEN** the signal may be email-eligible subject to cooldown and duplicate-suppression rules

#### Scenario: Stale or display-only quote crosses calibrated threshold
- **WHEN** a tracked ETF appears to cross an approved calibrated threshold using stale, estimated, display-only, or daily-close data
- **THEN** the system may show the context on the page but MUST NOT send an actionable email
