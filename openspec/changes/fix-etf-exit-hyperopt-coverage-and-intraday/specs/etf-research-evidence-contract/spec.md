## ADDED Requirements

### Requirement: Exit Optimization Evidence Records Execution And Coverage
The ETF research evidence contract SHALL record execution model, ETF coverage, data coverage, baseline comparison, and live-rule applicability for exit-rule optimization runs.

#### Scenario: Intraday optimization evidence is produced
- **WHEN** an ETF exit Hyperopt run completes with `execution_model=intraday_alert`
- **THEN** the evidence contract records data window, coverage funnel, final optimized ETF count, baseline metrics, candidate metrics, contract hash, and research-only status

#### Scenario: Partial coverage evidence is produced
- **WHEN** an ETF exit Hyperopt run is sampled or excludes a material share of eligible ETFs
- **THEN** the evidence contract marks the run as partial coverage and MUST NOT describe it as full-universe evidence

#### Scenario: Daily-close evidence is produced
- **WHEN** an ETF exit optimization result uses `execution_model=daily_close`
- **THEN** the evidence contract labels it as daily-close evidence and MUST NOT mark it as validating intraday email execution

### Requirement: Exit Optimization Evidence Separates Current Rule From Candidate Rule
The ETF research evidence contract SHALL distinguish live default exit rules, approved calibrated rules, and research-only candidate rules.

#### Scenario: Candidate exists but is not approved
- **WHEN** an optimization run produces a candidate parameter set that is not approved
- **THEN** the evidence contract exposes it as research-only and states that current live email rules are unchanged

#### Scenario: Approved calibrated rule exists
- **WHEN** an approved calibrated rule matches the current contract and bucket
- **THEN** the evidence contract identifies it as eligible for live rule lookup and records its run id, candidate id, bucket key, and approval timestamp

#### Scenario: Candidate is rejected
- **WHEN** an optimization candidate fails out-of-sample, rolling, coverage, or baseline comparison rules
- **THEN** the evidence contract records the rejection reason for UI and audit display
