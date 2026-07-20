## ADDED Requirements

### Requirement: ETF exit risk validation compares policy paths
The system SHALL validate ETF exit policies by replaying full position paths and comparing hold baseline, current default rules, candidate parameters, and protection-guard policies. Current-policy candidates SHALL use immutable exposure baselines, unique absolute-target action cycles, and execution-origin cooldown; legacy relative-action behavior may appear only as a diagnostic old-contract baseline.

#### Scenario: Policy path validation is generated
- **WHEN** ETF exit risk validation runs for a selected ETF universe
- **THEN** the result includes metrics for `hold_baseline`, `current_default`, `candidate_params`, and `guard_enabled_policy`, and separately records unique actions, simulated executions, and notification events

#### Scenario: Hold baseline is always present
- **WHEN** a validation sample has enough forward price data
- **THEN** the system records the result of holding without exit triggers as the baseline for opportunity-cost comparison

#### Scenario: Candidate policy is research-only
- **WHEN** a candidate policy outperforms the current default in historical validation
- **THEN** the system marks it as `candidate` and MUST NOT automatically apply it to live tracked-position alerts

### Requirement: ETF exit risk validation uses realistic entry and holding state
The system SHALL validate trailing and profit-protection rules using entry price, immutable exposure baseline/version, high-water profit, current profit, giveback, holding age, action/execution provenance, and decision-eligible price data.

#### Scenario: Tracked position history is available
- **WHEN** validation can use a real tracked ETF position with entry price and historical prices
- **THEN** the replay initializes the path from that tracked-position entry context

#### Scenario: Synthetic research entry is used
- **WHEN** validation uses signal-run TopN membership as a synthetic entry assumption
- **THEN** the result labels the sample as research simulation and records the signal run, rank basis, entry date, and entry price source

#### Scenario: Missing holding state blocks trailing validation
- **WHEN** a sample lacks enough price path data to compute high-water profit and giveback
- **THEN** the system excludes it from trailing-policy validation and records the exclusion reason

### Requirement: ETF exit validation confidence is multi-metric
The system SHALL grade ETF exit validation confidence using sample count, rolling stability, drawdown protection, missed upside, false exit rate, alert count, data coverage, and comparison against baseline policies.

#### Scenario: High confidence requires stability
- **WHEN** a candidate policy has enough trades, enough rolling windows, stable rolling performance, and is not worse than the current default on return or drawdown
- **THEN** the system may mark the evidence level as `high`

#### Scenario: Low confidence is explicit
- **WHEN** a policy lacks samples, has unstable rolling windows, or materially underperforms baseline
- **THEN** the system marks the evidence as `low` or `样本不足` and MUST NOT display it as reliable

#### Scenario: False exits are counted
- **WHEN** an exit trigger is followed by material missed upside within the configured horizon
- **THEN** the system records the event as a false exit or missed-upside event in the validation metrics

### Requirement: ETF exit protection guards are validated separately
The system SHALL validate protection guards separately from per-position exit thresholds.

#### Scenario: Cooldown guard is tested
- **WHEN** a simulated owner-equivalent partial/full execution starts reentry cooldown and the same ETF generates another entry or add signal within that execution-origin window
- **THEN** the validation records whether the reentry guard would suppress the new add/reentry action and its outcome without using notification history as execution proof

#### Scenario: Notification cooldown is tested separately
- **WHEN** the same alert episode produces repeated notification opportunities
- **THEN** validation records notification suppression/repetition separately, does not create another trade, and does not change action eligibility or reentry cooldown

#### Scenario: Repeated stop-loss guard is tested
- **WHEN** multiple stop-loss events occur within the configured lookback window
- **THEN** the validation records whether a repeated stop-loss guard would pause new add reminders or downgrade alerts

#### Scenario: Portfolio drawdown guard is tested
- **WHEN** a simulated observation portfolio breaches the configured drawdown guard
- **THEN** the validation records whether portfolio-level protection would switch the system into reduced-risk mode

### Requirement: ETF exit risk validation is auditable research evidence
The system SHALL store ETF exit validation metadata, selected universe, data window, rule versions, source signal run, selected codes, exclusions, and research-only status.

#### Scenario: Validation run is stored
- **WHEN** an exit risk validation run completes
- **THEN** the run summary includes rule version, policy validation version, universe scope, selected codes, source signal run id, data window, sample counts, exclusions, and `research_only=true`

#### Scenario: Validation is unavailable
- **WHEN** no suitable signal run, tracked-position sample, or price history exists
- **THEN** the system records a failed or waiting evidence state and MUST NOT generate fallback confidence scores
