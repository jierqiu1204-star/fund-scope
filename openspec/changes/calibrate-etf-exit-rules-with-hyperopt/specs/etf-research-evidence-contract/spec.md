## ADDED Requirements

### Requirement: ETF evidence contract includes exit calibration version
The ETF research evidence contract SHALL include the exit calibration version and approved parameter version when ETF exit calibration evidence is displayed or used.

#### Scenario: Calibration evidence is produced
- **WHEN** an ETF exit calibration run completes
- **THEN** the evidence contract records signal rule version, exit rule version, calibration rule version, execution model, data cutoff, data window, and contract hash

#### Scenario: Approved parameter is used by live exit strategy
- **WHEN** a live tracked ETF exit strategy uses approved calibrated parameters
- **THEN** the evidence contract identifies the approved parameter version and the calibration run that produced it

### Requirement: ETF calibration evidence status is explicit
The system SHALL expose whether exit calibration evidence is current, insufficient, stale, rejected, or waiting.

#### Scenario: Evidence matches current contract
- **WHEN** the latest calibration evidence matches the current signal, exit, and calibration rule versions with enough samples
- **THEN** the evidence status is marked as current and evidence-sufficient

#### Scenario: Evidence is from old rule version
- **WHEN** calibration evidence was generated under an older signal, exit, or calibration rule version
- **THEN** the system marks it as old evidence and MUST NOT present it as current live-rule validation

#### Scenario: Evidence sample is insufficient
- **WHEN** a calibration bucket or candidate has fewer than the minimum completed samples
- **THEN** the evidence status is marked sample-insufficient and the parameter MUST NOT be auto-approved

### Requirement: Current live rule and candidate calibration are displayed separately
The ETF research evidence contract SHALL distinguish currently active exit parameters from candidate optimization results.

#### Scenario: Candidate exists but is not approved
- **WHEN** a candidate parameter set exists without approved status
- **THEN** the evidence API and UI identify it as research-only and do not describe it as the current live email rule

#### Scenario: Approved parameter exists
- **WHEN** a parameter set is approved and matches the current contract
- **THEN** the evidence API and UI show it as the active calibrated parameter source while still displaying default-rule fallback availability

### Requirement: Calibration evidence cannot mutate live research artifacts
ETF exit calibration evidence SHALL NOT automatically rewrite short-term labels, portfolio weights, tracked positions, or notification history.

#### Scenario: Calibration finds stronger parameter
- **WHEN** a calibration run finds a better-scoring candidate
- **THEN** it updates calibration evidence only and MUST NOT change current short-term ranking labels, ETF allocation weights, tracked-position state, or notification logs

#### Scenario: Calibration fails
- **WHEN** a calibration run fails or produces no candidates
- **THEN** the system records the failure in evidence and job output without changing the current live exit rules
