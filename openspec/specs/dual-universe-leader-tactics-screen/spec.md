# dual-universe-leader-tactics-screen Specification

## Purpose
定义一套同时适用于 A 股和 ETF、公式透明、参数冻结且可按信号生命周期查询的龙头战术研究筛选面，明确区分文章披露条件、工程代理和未公开的原作者信号。
## Requirements

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

- `hot_score = mean(theme_rank(theme_return_1d), theme_rank(theme_return_5d), theme_up_breadth_1d)` and `hot_score >= 2/3` within the resolved PIT peer group;
- `core_score = mean(peer_rank(return_20d), peer_rank(return_5d), peer_rank(avg_amount_20d))` and `core_score >= 0.80`;
- adjusted `MA5 > MA10 > MA20`; and
- batch breadth of at least three qualifying non-clone assets and at least 20 percent of decision-eligible same-theme peers.

The breakout candidate SHALL additionally require `volume_T >= max(volume[T-119:T])` over 120 eligible sessions including T. The base-launch candidate SHALL instead use its own registered relative-volume or amount-percentile confirmation and MUST NOT inherit the breakout peak-volume gate. The system SHALL record every component, peer count, cutoff, gate result, theme-resolution provenance, and exclusion without substitution.

#### Scenario: Every common gate passes
- **WHEN** all common inputs are finite, PIT eligible, and satisfy the frozen thresholds
- **THEN** the row may proceed to a registered takeoff candidate and exposes the exact gate facts

#### Scenario: Every common and candidate-specific gate passes
- **WHEN** all common inputs and the selected candidate's registered volume confirmation are finite, PIT eligible, and satisfy the frozen thresholds
- **THEN** the row may proceed to that registered takeoff candidate and exposes the exact gate facts

#### Scenario: The peer group or batch signal is incomplete
- **WHEN** resolved PIT theme membership, at least five eligible peers, three qualifying assets, or 20 percent breadth is unavailable or fails
- **THEN** the candidate is not emitted and the exact failed gate and theme-resolution source are recorded

#### Scenario: Base launch lacks a 120-day peak volume
- **WHEN** the base-launch relative-volume confirmation passes but current volume is below the 120-session maximum
- **THEN** the base-launch row is not excluded for `volume_peak_gate_failed`

### Requirement: Exactly three V2 transparent candidates are frozen
The active formula registry SHALL contain exactly one versioned breakout proxy, one versioned base-launch proxy, and one versioned former-leader repair proxy, and SHALL reject runtime changes to thresholds, windows, candidate count, score weights, or missing-value behavior under the same registry identity. Evidence produced by an older registry remains immutable and queryable under its original identity.

#### Scenario: Breakout proxy is evaluated
- **WHEN** the common gates, 120-session peak-volume gate, and adjusted-close breakout over T-20 through T-1 pass
- **THEN** the registered breakout proxy emits a finite equal-weight score from `hot_score`, `core_score`, and the cross-sectional percentile of breakout magnitude

#### Scenario: Base-launch proxy is evaluated
- **WHEN** the common gates pass, adjusted MA5 crossed above adjusted MA10 during T-2 through T, adjusted close is above adjusted MA20, adjusted MA20 at T is no lower than at T-5, adjusted ATR5 divided by adjusted ATR20 is at most 0.90, `abs(adjusted_close - adjusted_MA20) / adjusted_ATR20` is at most 1.50, and either current volume is at least 1.20 times the prior-20-session mean volume or current amount ranks at or above its own empirical 70th percentile against the prior 20 eligible sessions
- **THEN** the registered base-launch proxy emits a finite equal-weight score from `hot_score`, `core_score`, reverse ATR compression percentile, and reverse symmetric-overextension percentile

#### Scenario: Former-leader repair proxy is evaluated
- **WHEN** the existing prior-leadership, 30-to-50-percent drawdown, positive-stabilization, adjusted ATR5/ATR20 at most 0.75, symmetric adjusted-MA20 overextension at most 1.0, and current hot-theme gates pass under compatible PIT data
- **THEN** the registered former-leader repair proxy emits its frozen repair score and is labeled an exploratory former-leader comparator rather than a disclosed takeoff formula

#### Scenario: Caller attempts parameter search
- **WHEN** a caller supplies another threshold, window, weight, Top N, or candidate under an existing registry identity
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
The read-only screen API SHALL accept `universe=etf|ashare`, `formula=all|breakout|base_launch|former_leader_repair`, `state=turning_watch|preparing|confirmed|invalidated`, `as_of`, and bounded pagination, default the dedicated panel to A-share research, and return code, name, resolved theme, theme-resolution source, formula identity, state, score when eligible, gate facts, passed and failed gates, distance to candidate, signal and transition dates, data cutoff, source provenance, exclusion reasons, and manifest hash.

#### Scenario: User requests A-share confirmed candidates
- **WHEN** the query uses universe=ashare and state=confirmed
- **THEN** only compatible confirmed A-share rows at or before as_of are returned in deterministic order

#### Scenario: User requests A-share turning watches
- **WHEN** the query uses `universe=ashare` and `state=turning_watch`
- **THEN** only compatible non-candidate A-share observations that satisfy the registered turning-watch minimum are returned in deterministic order with their remaining blockers

#### Scenario: User requests confirmed candidates
- **WHEN** the query uses a candidate lifecycle state such as `confirmed`
- **THEN** turning-watch observations are excluded and only compatible lifecycle rows at or before `as_of` are returned

#### Scenario: No eligible result exists
- **WHEN** filters are valid but data, coverage, formula, or state conditions produce no observations
- **THEN** the response returns an empty result with counts and a stable reason rather than silently falling back to another universe, formula, state, or stale manifest

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

### Requirement: Turning watch is non-actionable and deterministic
The system SHALL assign `turning_watch` only when a non-qualifying breakout or base-launch observation has compatible PIT inputs, a resolved peer group, adjusted close above adjusted MA20, non-negative adjusted MA20 slope, and passes at least three of the four registered families `hot theme`, `core leader`, `MA alignment`, and candidate-specific volume confirmation. It SHALL expose the failed families and normalized distance to each threshold and SHALL NOT feed this state into confirmation, execution, notification, allocation, or production ranking.

#### Scenario: Early theme rotation is visible
- **WHEN** a security satisfies the turning-watch minimum but fails one or more formal candidate gates
- **THEN** it appears only as `转强观察`, carries no actionable score or buy wording, and lists every remaining blocker

#### Scenario: Observation does not meet watch minimum
- **WHEN** fewer than three registered gate families pass or the PIT/MA20 prerequisites fail
- **THEN** it remains an excluded observation and is not presented as a turning watch

### Requirement: ETF materialization is independently controlled
ETF V2 materialization SHALL have its own enablement, checkpoint, manifest, coverage counts, and read API and SHALL use only decision-eligible total-return-adjusted ETF inputs. Enabling or running it MUST NOT alter comprehensive-ranking inputs, scores, ordering, publication gates, snapshots, portfolio state, alerts, or notifications.

#### Scenario: ETF V2 materialization is enabled
- **WHEN** the ETF V2 readiness contract passes
- **THEN** one ETF research manifest is materialized and the A-share and comprehensive-ranking manifests remain unchanged

#### Scenario: ETF V2 readiness fails
- **WHEN** required adjusted history, PIT grouping, provider health, finite-value, or clone checks fail
- **THEN** ETF V2 returns its own waiting reason and no comprehensive-ranking fallback or mutation occurs

### Requirement: A-share candidate screen separates raw qualification from shadow actionability
The A-share screen SHALL return the immutable raw candidate result together with sentiment risk state, action mode, new-entry permission, proxy provenance, and stable reasons. The default candidate ordering SHALL remain the frozen raw V2 score ordering.

#### Scenario: Risk overlay blocks a new shadow entry
- **WHEN** a qualified A-share breakout is covered by `warning`, `risk_off`, or `unavailable`
- **THEN** the row remains visible in its original order and lifecycle state while the response clearly marks it observe-only

#### Scenario: Existing filters are used
- **WHEN** callers use the existing universe, formula, state, as-of, cursor, or pagination filters
- **THEN** the same snapshot and keyset semantics are preserved and no provider work is triggered

### Requirement: ETF and A-share screening remain isolated
The risk overlay SHALL execute only for the A-share screen and SHALL NOT import, read, score, filter, or mutate ETF comprehensive-ranking or ETF leader-tactics state.

#### Scenario: Same materialization run handles ETF rows
- **WHEN** ETF candidates are screened or read
- **THEN** their hashes, raw qualification, score, ordering, exclusions, and API output remain compatible with the pre-change ETF contract
