## MODIFIED Requirements

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

## ADDED Requirements

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
