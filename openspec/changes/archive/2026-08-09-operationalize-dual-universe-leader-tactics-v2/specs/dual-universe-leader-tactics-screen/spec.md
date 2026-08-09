## Purpose

定义一套同时适用于 A 股和 ETF、公式透明、参数冻结且可按信号生命周期查询的龙头战术研究筛选面，明确区分文章披露条件、工程代理和未公开的原作者信号。

## ADDED Requirements

### Requirement: Source claims and proxy formulas remain explicitly separate
The system SHALL bind V2 screening to an immutable source registry containing article identity, publication time, captured-content hash, concise disclosed-rule paraphrases, unavailable proprietary elements, formula version, universe adaptation, and an explicit non-equivalence statement.

#### Scenario: The source mentions a takeoff signal without code
- **WHEN** an article uses the proprietary `起飞信号` but does not publish a complete reproducible formula
- **THEN** the system marks it `unavailable_proprietary`, labels all computed outputs as transparent proxies, and does not claim exact reproduction or author endorsement

#### Scenario: A source interpretation changes
- **WHEN** a source mapping, threshold, window, candidate rule, or missing-value policy changes
- **THEN** the system requires a new formula registry and manifest identity rather than rewriting V2 evidence

### Requirement: Common disclosed takeoff gates are deterministic
For signal session T, the takeoff candidates SHALL require finite decision-eligible adjusted data and SHALL define:

- `hot_score = mean(theme_rank(theme_return_1d), theme_rank(theme_return_5d), theme_up_breadth_1d)` and `hot_score >= 2/3`;
- `core_score = mean(peer_rank(return_20d), peer_rank(return_5d), peer_rank(avg_amount_20d))` and `core_score >= 0.80`;
- adjusted `MA5 > MA10 > MA20`;
- `volume_T >= max(volume[T-119:T])` over 120 eligible sessions including T; and
- batch breadth of at least three qualifying non-clone assets and at least 20 percent of decision-eligible same-theme peers.

The system SHALL record every component, peer count, cutoff, gate result, and exclusion without substitution.

#### Scenario: Every common gate passes
- **WHEN** all common inputs are finite, PIT eligible, and satisfy the frozen thresholds
- **THEN** the row may proceed to a registered takeoff candidate and exposes the exact gate facts

#### Scenario: The peer group or batch signal is incomplete
- **WHEN** theme membership, at least five eligible peers, three qualifying assets, or 20 percent breadth is unavailable or fails
- **THEN** the candidate is not emitted and the exact failed gate is recorded

### Requirement: Exactly three V2 transparent candidates are frozen
The V2 registry SHALL contain exactly `leader_breakout_proxy_v2`, `base_launch_proxy_v2`, and `former_leader_repair_proxy_v2`, and SHALL reject runtime changes to thresholds, windows, candidate count, score weights, or missing-value behavior under the same registry.

#### Scenario: Breakout proxy is evaluated
- **WHEN** the common disclosed gates pass and adjusted close at T is greater than the maximum adjusted high from T-20 through T-1
- **THEN** `leader_breakout_proxy_v2` emits a finite equal-weight score from `hot_score`, `core_score`, and the cross-sectional percentile of breakout magnitude

#### Scenario: Base-launch proxy is evaluated
- **WHEN** the common disclosed gates pass, adjusted MA5 crossed above adjusted MA10 during T-2 through T, adjusted close is above adjusted MA20, adjusted MA20 at T is no lower than at T-5, adjusted ATR5 divided by adjusted ATR20 is at most 0.90, and `abs(adjusted_close - adjusted_MA20) / adjusted_ATR20` is at most 1.50
- **THEN** `base_launch_proxy_v2` emits a finite equal-weight score from `hot_score`, `core_score`, reverse ATR compression percentile, and reverse symmetric-overextension percentile

#### Scenario: Former-leader repair proxy is evaluated
- **WHEN** the existing V1 prior-leadership, 30-to-50-percent drawdown, positive-stabilization, adjusted ATR5/ATR20 at most 0.75, and symmetric adjusted-MA20 overextension at most 1.0 gates pass under V2 PIT data and the current theme hot gate passes
- **THEN** `former_leader_repair_proxy_v2` emits its frozen V1-derived repair score and is labeled an exploratory former-leader comparator rather than a disclosed takeoff formula

#### Scenario: Caller attempts parameter search
- **WHEN** a caller supplies another threshold, window, weight, Top N, or candidate under the V2 identity
- **THEN** the request is rejected before any future outcome is read

### Requirement: Signal lifecycle is causal and deterministic
The system SHALL create `preparing` when a registered candidate first passes, transition to `confirmed` only on a later eligible session whose adjusted close exceeds the signal-session adjusted high and remains above same-session adjusted MA5, and transition an active signal to `invalidated` after an eligible adjusted close falls below same-session adjusted MA5.

#### Scenario: Candidate appears but has not strengthened
- **WHEN** the formula passes on T but no later eligible close has exceeded the signal-day high above MA5
- **THEN** the signal remains `preparing` and MUST NOT be described as confirmed or bought

#### Scenario: Candidate strengthens
- **WHEN** a later eligible adjusted close exceeds the immutable signal-day high and remains above adjusted MA5
- **THEN** the signal becomes `confirmed` at that close with the transition cutoff and evidence hash recorded

#### Scenario: Confirmed signal breaks MA5
- **WHEN** an active signal closes below same-session adjusted MA5
- **THEN** it becomes `invalidated`, and any simulated exit occurs only at the next eligible adjusted close with declared costs

#### Scenario: Intraday execution is requested from daily bars
- **WHEN** no factual minute-level PIT quote and executable spread are available
- **THEN** the system reports intraday entry or exit as unavailable and does not infer a fill from daily high, low, or close

### Requirement: Universe adapters preserve comparable semantics
The A-share adapter SHALL calculate peer ranks and batch breadth inside the factual PIT theme group. The ETF adapter SHALL calculate them inside the factual PIT theme or tracked-index group and SHALL apply the frozen clone policy before breadth, ranking, and cohort formation.

#### Scenario: Equivalent ETFs qualify together
- **WHEN** multiple eligible ETFs belong to the same clone group
- **THEN** only the declared most-liquid representative contributes to breadth and the remaining clones carry explicit exclusions

#### Scenario: A stock lacks PIT theme membership
- **WHEN** an otherwise eligible A share has no factual theme membership by the cutoff
- **THEN** it cannot borrow a current taxonomy or join an unrelated peer group

### Requirement: Candidate screen is filterable and evidence rich
The read-only screen API SHALL accept `universe=etf|ashare`, `formula=all|breakout|base_launch|former_leader_repair`, `state=preparing|confirmed|invalidated`, `as_of`, and bounded pagination, default to ETF, and return code, name, theme, formula identity, state, score, gate facts, signal and transition dates, data cutoff, source provenance, exclusion reasons, and manifest hash.

#### Scenario: User requests A-share confirmed candidates
- **WHEN** the query uses `universe=ashare` and `state=confirmed`
- **THEN** only compatible confirmed A-share rows at or before `as_of` are returned in deterministic order

#### Scenario: No eligible result exists
- **WHEN** filters are valid but data, coverage, formula, or state conditions produce no candidates
- **THEN** the response returns an empty result with counts and a stable reason rather than silently falling back to another universe or formula

### Requirement: Post-close watchlists preserve causal time boundaries

The system MAY screen the latest complete adjusted trading session after that session has
closed, but SHALL identify the result as `post_close_watchlist`, use only memberships visible
at the actual decision cutoff, record the next eligible exchange session, and exclude the run
from historical PIT replay and promotion evidence.

#### Scenario: Weekend screen uses Friday prices

- **WHEN** a Saturday decision uses Friday total-return-adjusted bars and a membership first
  visible by Saturday
- **THEN** the system records Friday as the feature trade date and Saturday as the membership
  evaluation date
- **AND** it labels the result `post_close_watchlist` with Monday as the next eligible session
- **AND** no Saturday or earlier bar can confirm or invalidate the candidate after creation
- **AND** the run is rejected by historical PIT replay and promotion paths
