# strategy-aware-tracking-alert-routing Specification

## Purpose
为用户主动追踪的持仓提供可选择、可审计且与研究榜单隔离的邮件策略，使不同入场逻辑能够使用匹配的退出生命周期而不伪造自动交易。

## Requirements

### Requirement: Tracking alert policy is explicit and versioned
The system SHALL persist an alert-policy identity and rule version on every tracked position, and SHALL use the current dynamic holding policy as the default when the client omits a selection.

#### Scenario: Existing or ordinary position uses the default
- **WHEN** a tracked position predates this capability or a new request omits an alert policy
- **THEN** the position uses the versioned current dynamic holding policy without changing its prior behavior

#### Scenario: User selects the late-day policy
- **WHEN** a user creates or updates an ETF tracked position with `late_day_turnaround_t1_v1`
- **THEN** the system persists that exact policy and routes later evaluations only through its T+1 lifecycle

#### Scenario: Unsupported asset selects the late-day policy
- **WHEN** a fund or unsupported asset type selects `late_day_turnaround_t1_v1`
- **THEN** the system rejects the request with a stable validation reason and does not silently fall back

#### Scenario: User selects the leader sell-only policy
- **WHEN** a user creates or updates an ETF or stock tracked position with `leader_tactics_exit_v1`
- **THEN** the system persists that policy, evaluates only completed eligible adjusted daily bars, and sends no candidate or entry email

#### Scenario: Unsupported asset selects the leader policy
- **WHEN** a fund selects `leader_tactics_exit_v1` or a stock selects a non-leader policy
- **THEN** the system rejects the request and does not silently substitute another policy

### Requirement: Policy provenance remains truthful
The system SHALL distinguish candidate-backed policy selection from manual policy selection and SHALL expose any source strategy, manifest hash, and decision cutoff supplied and validated at creation time.

#### Scenario: Compatible candidate evidence is supplied
- **WHEN** the selected ETF and source manifest identify a persisted late-day observation available at or before the source decision cutoff
- **THEN** the position records candidate-backed provenance without copying research evidence into ranking or execution stores

#### Scenario: User selects the policy manually
- **WHEN** a user selects the late-day policy without a candidate manifest
- **THEN** the position records `manual_selection` provenance and the UI MUST NOT claim that the system generated the entry signal

#### Scenario: Supplied evidence does not match the ETF
- **WHEN** a supplied manifest or decision cutoff does not identify compatible evidence for the tracked ETF
- **THEN** the system rejects the provenance instead of labeling the position candidate-backed

#### Scenario: Compatible leader evidence is supplied
- **WHEN** the source manifest is complete and identifies a matching qualifying leader candidate whose visible lifecycle is confirmed by the supplied cutoff
- **THEN** the position records candidate-backed leader provenance without creating or altering research evidence

#### Scenario: Leader policy is selected without research evidence
- **WHEN** the user manually selects `leader_tactics_exit_v1` without a source manifest
- **THEN** the position records manual provenance and the UI does not claim the system generated the entry

### Requirement: Late-day entry evidence is causally executable
The late-day execution contract SHALL use the first fresh executable quote received after a valid 14:30–14:50 signal, no more than two minutes after the decision time and no later than 14:55 Asia/Shanghai.

#### Scenario: First quote arrives within the entry bound
- **WHEN** an eligible quote is received after the decision time and within both entry limits
- **THEN** research execution records that quote as the entry and records its quote time and source

#### Scenario: No eligible quote arrives within the entry bound
- **WHEN** no fresh executable quote is received before the two-minute latency limit or 14:55 deadline
- **THEN** the trade is excluded with a stable entry-unavailable reason and no later quote is backfilled as the entry

### Requirement: Late-day exit evaluation is causal and T+1 bounded
The `late_day_turnaround_t1_v1` policy SHALL remain non-actionable on the signal session and evaluate a full exit on the next eligible trading morning using only evidence received by each evaluation time.

#### Scenario: Same-session evaluation occurs
- **WHEN** the late-day tracked position is evaluated on its entry session
- **THEN** the system returns a holding status and MUST NOT generate an exit email from the T+1 rule

#### Scenario: Hard stop is breached on T+1
- **WHEN** a fresh executable T+1 quote breaches the volatility-adaptive hard stop
- **THEN** the system selects hard stop as the highest-priority full-exit reason

#### Scenario: Activated morning high gives back
- **WHEN** the causal morning high has activated profit protection and the fresh current quote gives back the volatility-adaptive allowance
- **THEN** the system selects morning-high giveback as the full-exit reason unless hard stop has priority

#### Scenario: Closed 10-minute trend fails
- **WHEN** sufficient closed T+1 10-minute bars exist and the latest closed price crosses below its 10-minute MA5 without a higher-priority trigger
- **THEN** the system selects MA5 failure as the full-exit reason

#### Scenario: No earlier trigger by 10:30
- **WHEN** no higher-priority exit has triggered by 10:30 Asia/Shanghai and a fresh executable quote is available
- **THEN** the system selects mandatory timed exit as a full-exit reason

#### Scenario: Actionable quote is unavailable
- **WHEN** a trigger can only be inferred from stale, display-only, missing, or non-executable data
- **THEN** the system exposes a stable data-waiting or exit-blocked reason and MUST NOT send an actionable email

### Requirement: Strategy policy evidence is visible and auditable
The system SHALL return policy identity, rule version, provenance, evaluation session, trigger priority, thresholds, quote freshness, and the main no-action reason in tracked-position snapshots and alert audits.

#### Scenario: Late-day exit email is sent
- **WHEN** an eligible late-day T+1 full-exit trigger sends an email
- **THEN** the audit identifies the selected policy, trigger reason, data cutoff, current quote, morning high, relevant threshold, and SMTP outcome

#### Scenario: No exit is sent
- **WHEN** the policy is waiting for T+1, has not crossed a threshold, or lacks eligible data
- **THEN** the UI receives a beginner-facing no-action reason and the raw evidence remains inspectable

### Requirement: Policy routing is isolated from rankings and research materialization
Selecting or evaluating a tracked-position alert policy SHALL NOT alter ETF comprehensive-ranking scores, leader-tactics scores, late-day candidate manifests, or automatically create broker execution state.

#### Scenario: Research candidate is materialized
- **WHEN** a late-day research run produces a candidate
- **THEN** no tracked position or email is created until a user explicitly starts tracking

#### Scenario: Tracked position is evaluated
- **WHEN** either alert policy evaluates a tracked ETF
- **THEN** only tracked-position state and its alert audit may change, while ranking and research candidate stores remain unchanged

#### Scenario: Leader candidate is displayed
- **WHEN** a leader-tactics candidate or lifecycle transition is materialized or viewed
- **THEN** no tracked position, candidate-summary email, entry email, or SMTP state is created automatically
