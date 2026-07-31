## Purpose

Defines transparent, reproducible ETF research proxies for source-described leader tactics while preserving point-in-time integrity, explicit non-equivalence, bounded execution, and complete isolation from production decisions.

## ADDED Requirements

### Requirement: Leader-tactics hypotheses are transparent and source-bound
The system SHALL bind every leader-tactics experiment to an immutable hypothesis registry that records source article identity, source publication time, captured-content hash, disclosed rule statements, unavailable or subjective rule statements, ETF adaptation notes, exact proxy formulas, and an explicit statement that the proxies are not the source author's proprietary signal.

#### Scenario: Proprietary signal is unavailable
- **WHEN** a source describes an entry signal without disclosing a reproducible formula
- **THEN** the registry marks that source rule `unavailable_proprietary`, uses only a separately named transparent proxy, and MUST NOT label any result as the original signal

#### Scenario: Subjective source judgment is adapted
- **WHEN** a phrase such as hot sector, core leader, stabilization, strengthening, or do-not-chase is mapped to a deterministic ETF rule
- **THEN** the registry records the source phrase and the exact non-equivalent proxy side by side

#### Scenario: Source or formula changes
- **WHEN** source identity, interpretation, formula, threshold, window, candidate order, or missing-value rule changes
- **THEN** the system creates a new hypothesis registry and experiment identity rather than merging outcomes with prior evidence

### Requirement: Leader-tactics candidates are exactly pre-registered
The leader-tactics shadow SHALL contain exactly `leader_breakout_proxy_v1`, `former_leader_repair_proxy_v1`, and `cycle_routed_leader_proxy_v1`, with no runtime threshold, weight, window, Top N, or candidate search.

#### Scenario: Frozen registry is accepted
- **WHEN** the exact three candidate identities and canonical formulas are registered before any outcome is read
- **THEN** the experiment may construct point-in-time features and outcomes

#### Scenario: Caller supplies an alternative parameter
- **WHEN** a caller changes a drawdown band, percentile gate, moving-average window, volume window, route, or score weight under the same registry
- **THEN** the experiment rejects the request before reading future returns

### Requirement: Leader breakout proxy uses factual adjusted daily inputs
For signal session T, `leader_breakout_proxy_v1` SHALL require at least 120 decision-eligible total-return-adjusted OHLCV sessions, factual historical ETF membership and peer mapping as of T, at least five eligible ETFs in the peer bucket, adjusted `MA5 > MA10 > MA20`, adjusted close above the maximum adjusted high of the preceding 20 sessions, T volume at the maximum of the latest 120 sessions, sector-trend percentile of at least two thirds, peer 20-session total-return percentile of at least 0.80, and peer 20-session average-turnover percentile of at least 0.50.

Its finite score SHALL be the equal-weight mean of sector-trend percentile, peer 20-session total-return percentile, and peer 20-session average-turnover percentile among rows that pass every gate.

#### Scenario: Breakout row passes
- **WHEN** every input and gate is available as of T and all values are finite
- **THEN** the system emits the three component values, gate facts, score, source cutoff, peer count, and feature hash

#### Scenario: Any breakout gate is unavailable or fails
- **WHEN** history, adjusted provenance, membership, peer mapping, peer count, moving-average alignment, price breakout, volume breakout, sector heat, leadership, liquidity, or finite-value eligibility fails
- **THEN** the system emits no candidate score for that ETF and records the exact exclusion without substitution

### Requirement: Former-leader repair proxy is reproducible
For signal session T, `former_leader_repair_proxy_v1` SHALL require at least 180 decision-eligible total-return-adjusted OHLCV sessions, factual historical membership and peer mapping as of T, at least five eligible peer ETFs, a peer 20-session total-return percentile of at least 0.80 on at least one session from T-119 through T-20, a current adjusted-close drawdown between 30 and 50 percent from the highest adjusted close in the latest 120 sessions, adjusted close above both adjusted open and prior adjusted close, adjusted ATR5 no greater than 0.75 times adjusted ATR20, and `abs(adjusted_close - adjusted_MA20) / adjusted_ATR20` no greater than 1.0.

Its finite score SHALL be the equal-weight mean of the prior maximum leadership percentile, the reverse cross-sectional percentile of adjusted ATR5 divided by adjusted ATR20, and the reverse cross-sectional percentile of `abs(adjusted_close - adjusted_MA20) / adjusted_ATR20` among rows that pass every gate.

#### Scenario: Repair row passes
- **WHEN** all prior-leadership, drawdown, positive-stabilization, compression, symmetric-overextension, provenance, and peer facts pass as of T
- **THEN** the system emits the component values, gate facts, score, source cutoff, peer count, and feature hash

#### Scenario: Later data would create prior leadership
- **WHEN** a historical peer rank, membership fact, adjustment, or taxonomy mapping was first received after T
- **THEN** the system excludes it and MUST NOT infer that the ETF was a prior leader at T

### Requirement: Cycle routing uses the existing point-in-time regime
`cycle_routed_leader_proxy_v1` SHALL use the frozen market-regime fact available at T, route `risk_on` to `leader_breakout_proxy_v1`, route `neutral` to `former_leader_repair_proxy_v1`, and emit no selection for `defensive`, `cash_wait`, unavailable, stale, or incompatible regimes.

#### Scenario: Risk-on session is evaluated
- **WHEN** the point-in-time regime is `risk_on`
- **THEN** the routed candidate uses only the same-session breakout-proxy score and records the regime contract identity

#### Scenario: Neutral session is evaluated
- **WHEN** the point-in-time regime is `neutral`
- **THEN** the routed candidate uses only the same-session repair-proxy score and records the regime contract identity

#### Scenario: Weak or missing regime is evaluated
- **WHEN** the regime is `defensive`, `cash_wait`, unavailable, stale, or incompatible
- **THEN** the candidate records a no-selection reason and does not fall back to either proxy

### Requirement: Clone and concentration controls are explicit
Leader-tactics shadow ranking SHALL apply the experiment's frozen clone policy before forming Top N cohorts and SHALL report theme, sector, tracked-index, issuer, and pairwise-return concentration separately.

#### Scenario: Multiple ETFs are equivalent clones
- **WHEN** multiple eligible ETFs belong to the same frozen clone group
- **THEN** only the declared most-liquid representative remains eligible and every removed clone is recorded

#### Scenario: Candidate result is concentrated
- **WHEN** an otherwise favorable candidate breaches a declared concentration limit
- **THEN** the evidence records the breach and the candidate cannot become promotion eligible

### Requirement: MA5 lifecycle is exploratory policy evidence only
The system SHALL treat `ma5_exit_proxy_v1` only as exploratory policy evidence and, when calculated for a leader-proxy research entry, SHALL enter at the next decision-eligible adjusted close, observe only data available through each later session, trigger after adjusted close falls below same-session adjusted MA5, and simulate exit at the next decision-eligible adjusted close with the declared non-zero costs.

#### Scenario: MA5 exit is evaluated
- **WHEN** a complete proxy entry and later adjusted observations exist
- **THEN** the system reports its net result relative to frozen five-session and ten-session holds as exploratory policy-shadow evidence

#### Scenario: MA5 diagnostic appears favorable
- **WHEN** the MA5 lifecycle improves an exploratory result
- **THEN** it MUST NOT replace the five-session ranking primary endpoint, modify `etf_exit_action_v3`, generate a tracked position, or trigger a notification

#### Scenario: Intraday T-trading is requested
- **WHEN** a source-described intraday buy-low/sell-high operation lacks a pre-registered executable point-in-time model
- **THEN** the system marks it unavailable and does not fabricate fills from daily bars

### Requirement: Leader-tactics shadow is bounded and production-isolated
The leader-tactics experiment SHALL use one worker, deterministic pages of at most 20 ETFs, durable idempotent checkpoints, bounded memory, and a hard continuation limit of at most 55 seconds, and SHALL persist only research artifacts.

#### Scenario: Continuation reaches a bound
- **WHEN** time, page, or memory budget is reached
- **THEN** the system commits the last complete page, releases the lease, and returns a resumable cursor without starting another worker

#### Scenario: Shadow run completes
- **WHEN** factors, outcomes, diagnostics, or MA5 policy evidence are persisted
- **THEN** production ranking, allocation, tracked positions, risk alerts, notification logs, SMTP state, score weights, and the existing frozen ranking-candidate registry remain unchanged
