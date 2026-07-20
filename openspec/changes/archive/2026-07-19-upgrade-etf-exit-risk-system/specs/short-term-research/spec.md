## ADDED Requirements

### Requirement: Short-term workbench separates exit risk classes
The short-term research workbench SHALL distinguish ETF observation labels, actionable holding signals, guard-only risk states, and research-only exit evidence.

#### Scenario: Ranked ETF has observation label
- **WHEN** an ETF appears in the ranking with `短线观察`, `高位观察`, or another observation label
- **THEN** the UI presents it as observation evidence and MUST NOT imply that the label decides how an existing holding should be sold

#### Scenario: Holding has actionable exit signal
- **WHEN** a tracked ETF has a proposed action from `hard_stop`, `trailing_take_profit`, `confirmed_trend_weakening`, or eligible `exit_watch`
- **THEN** the UI shows it in the holding action area with immutable-baseline absolute target, action status, execution provenance, threshold context, data source, email eligibility, and notification status

#### Scenario: Holding has take-profit watch
- **WHEN** a tracked ETF only has eligible `take_profit_watch`
- **THEN** the UI shows `hold/仅观察/未生成减仓动作` in the soft-watch area and MUST NOT style it as an actionable reduce or exit

#### Scenario: Holding has guard-only risk state
- **WHEN** a tracked ETF only has unconfirmed trend weakening, cooldown, repeated-stop guard, portfolio drawdown guard, or market-regime guard
- **THEN** the UI displays it as `风险警戒` or `暂不加仓` style context and MUST NOT style it as a sell email trigger

### Requirement: Short-term workbench displays exit evidence confidence honestly
The short-term research workbench SHALL display ETF exit risk validation confidence, sample limitations, and research-only status without fallback wording.

#### Scenario: Exit evidence is available
- **WHEN** current ETF exit risk validation evidence exists
- **THEN** the page shows evidence status, policy version, universe scope, source signal run, sample count, baseline comparison, confidence level, and whether the policy is approved for live use

#### Scenario: Exit evidence is weak
- **WHEN** a rule has low confidence, high false-exit rate, high missed-upside rate, or insufficient samples
- **THEN** the page labels it as `样本不足`, `仅供观察`, or `证据偏弱` and MUST NOT call it reliable

#### Scenario: Candidate parameters are shown
- **WHEN** candidate exit parameters exist but are not approved
- **THEN** the page labels them as research candidates and states that they do not change live alerts

### Requirement: Short-term workbench explains protection guard effects
The short-term research workbench SHALL explain when protection guards suppress, downgrade, or block ETF holding messages.

#### Scenario: Cooldown suppresses duplicate email
- **WHEN** a tracked ETF alert is suppressed by cooldown
- **THEN** the holding card identifies it as notification suppression, explains the reason and next eligible notification slot, and does not imply that action eligibility, execution state, or reentry cooldown changed

#### Scenario: Portfolio guard is active
- **WHEN** portfolio drawdown or repeated stop-loss guard is active
- **THEN** the workbench displays the guard state separately from individual ETF rankings and explains that new add reminders may be blocked

#### Scenario: Guard is not active
- **WHEN** no guard suppresses a tracked ETF signal
- **THEN** the holding context avoids showing stale or inherited guard warnings
