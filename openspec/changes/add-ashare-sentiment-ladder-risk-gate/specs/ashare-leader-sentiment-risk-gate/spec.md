## Purpose

为 A 股龙头战术提供独立于选股 alpha 的情绪梯队风险覆盖层，用可复现的中位梯队走弱代理表达“龙头仍强、跟随梯队先转弱”的风险，并在事实不足时拒绝给出可操作结论。

## ADDED Requirements

### Requirement: Middle-echelon risk proxy is deterministic and PIT safe
The system SHALL form the proxy only from decision-eligible A-share adjusted bars and factual PIT theme memberships visible by the signal cutoff. Inside hot themes, `core_score >= 0.80` SHALL define the leader tier and `0.50 <= core_score < 0.80` SHALL define the middle tier. The frozen proxy SHALL evaluate middle-tier median one-session return, positive one-session breadth, below-adjusted-MA5 ratio, and the positive leader-minus-middle median one-session-return spread.

#### Scenario: All proxy inputs are available
- **WHEN** at least two hot themes, three leader-tier assets, and twelve middle-tier assets have finite visible inputs
- **THEN** the system returns every metric, cohort count, threshold, cutoff, contract hash, and triggered component in deterministic order

#### Scenario: Cohort support is insufficient
- **WHEN** any minimum theme, leader, middle-tier, finite-value, or PIT requirement is not met
- **THEN** the risk state is `unavailable`, the shadow action is `observe_only`, and the exact unavailable reason is recorded without borrowing current taxonomy or future data

### Requirement: Risk states use frozen breadth and divergence thresholds
The proxy SHALL trigger one component for each of: middle-tier median one-session return at or below zero, middle-tier positive breadth below 0.40, middle-tier below-adjusted-MA5 ratio above 0.60, and leader-minus-middle median one-session-return spread at least 0.02 while the leader median return is positive. Fewer than two components SHALL be `healthy`, exactly two SHALL be `warning`, and at least three SHALL be `risk_off`.

#### Scenario: Middle echelon weakens while leaders remain strong
- **WHEN** at least three frozen components trigger on complete inputs
- **THEN** the risk state is `risk_off` with all triggered and non-triggered facts retained

#### Scenario: One noisy component triggers
- **WHEN** complete inputs trigger fewer than two frozen components
- **THEN** the risk state remains `healthy` and no threshold is changed at runtime

### Requirement: The proxy does not impersonate factual limit-board data
The system SHALL label this contract as an adjusted-bar breadth proxy and SHALL NOT infer consecutive limit-up height, promotion rate, sealed-board status, failed-board rate, or exchange limit state from adjusted OHLC bars.

#### Scenario: Factual limit-board inputs are absent
- **WHEN** no independently timestamped limit-board facts exist by the cutoff
- **THEN** those fields remain explicitly unavailable and do not contribute substitute values to the risk state

### Requirement: Risk overlay changes shadow actionability, not alpha
For A-share `leader_breakout_proxy_v2`, `healthy` SHALL map to `shadow_entry_allowed`; `warning`, `risk_off`, or `unavailable` SHALL map to `observe_only`. Other formulas SHALL expose `not_applicable`. The overlay SHALL NOT change raw score, qualification, rank, or lifecycle state.

#### Scenario: Qualified breakout encounters warning
- **WHEN** a breakout candidate passes its frozen formula while the risk state is `warning`
- **THEN** it remains a qualified research candidate with the same score and lifecycle but reports `observe_only` and `new_entry_allowed=false`

#### Scenario: ETF candidate is evaluated
- **WHEN** the universe is ETF
- **THEN** no A-share sentiment-ladder action override is applied and ETF ranking or leader-tactics identities remain unchanged
