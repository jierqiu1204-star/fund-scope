## ADDED Requirements

### Requirement: Low-base catch-up remains an isolated A-share shadow

The system SHALL expose `low_base_catchup_proxy_v1` only for A-share leader-tactics research and SHALL NOT mutate ETF comprehensive ranking, recommendations, positions, alerts, or notifications.

#### Scenario: ETF materialization runs

- **WHEN** the ETF leader-tactics universe is materialized
- **THEN** the low-base catch-up formula is not an ETF candidate and ETF comprehensive-ranking identities remain unchanged

### Requirement: Low-base catch-up uses frozen causal setup and launch gates

The system SHALL require 129 qualified adjusted sessions so that every date in a ten-session setup window has a complete 120-session history, a cutoff-visible hot peer group with at least five comparable assets, low-range and drawdown evidence plus causal repeated-volume evidence observed within that setup window, and a current rising-MA5, prior-five-high breakout and positive close launch trigger. Every remembered fact SHALL preserve its evidence date and value and SHALL be no later than the decision cutoff.

#### Scenario: One isolated volume spike occurs

- **WHEN** only one of the latest five sessions has relative volume at or above 1.20 or the five-session mean volume is below 1.30 times the disjoint prior-twenty mean
- **THEN** the repeated-volume family fails and the observation cannot become actionable
- **AND WHEN** the latest session itself reaches 2.00 times its prior-twenty mean and supplies the launch trigger
- **THEN** the system may expose `single_day_volume_watch=true` with `entry_status=watch`, but SHALL NOT confirm the candidate until the frozen repeated-volume gate passes

#### Scenario: Setup precedes the launch trigger

- **WHEN** cutoff-visible low-base and repeated-volume evidence occurred within the previous ten qualified sessions and the current session supplies the rising-MA5 and prior-five-high launch trigger
- **THEN** the observation may emit `launch_signal=true` without requiring the current session to remain inside the original low-base range

#### Scenario: Setup evidence is stale

- **WHEN** the last qualifying low-base or repeated-volume evidence is older than the ten-session setup window
- **THEN** a current price breakout cannot emit a low-base catch-up launch signal

#### Scenario: Candidate has an extended but valid launch signal

- **WHEN** five-session return is at most 25 percent and symmetric MA20 distance is greater than 1.50 but no more than 2.00 ATR20
- **THEN** the system preserves `launch_signal=true`, returns `entry_status=watch`, and exposes `extension_band=extended_watch`

#### Scenario: Candidate is already overextended

- **WHEN** five-session return exceeds 25 percent or symmetric MA20 distance exceeds 2.00 ATR20
- **THEN** `entry_status=overextended` and the observation is not actionable

### Requirement: Lifecycle and entry suitability are separate

The system SHALL preserve `turning_watch`, `preparing`, `confirmed`, and `invalidated`, SHALL use a five-session watch timeout and a three-session preparing confirmation timeout, and SHALL expose `entry_status=watch|actionable|overextended|invalidated` independently.

#### Scenario: Only the initial-turning family is missing

- **WHEN** the hot-theme family passes and cutoff-visible low-base and repeated-volume evidence exists within the ten-session setup window but the initial-turning family does not
- **THEN** the observation may enter `turning_watch` and may advance to `preparing` only after a later qualified close breaks the signal high while holding adjusted MA5

#### Scenario: A structural family is missing

- **WHEN** the hot-theme family fails or low-base or repeated-volume setup evidence is unavailable or stale
- **THEN** a later price rise alone cannot advance the observation into `preparing`

#### Scenario: A setup does not confirm within its bounded window

- **WHEN** a `turning_watch` observation remains unconfirmed for five later sessions or a `preparing` observation remains unconfirmed for three later sessions
- **THEN** its lifecycle becomes `invalidated` and its entry status is `invalidated`

### Requirement: Historical exemplars are diagnostic, not optimized labels

The system SHALL support causal historical diagnostics for Fenghua High-Tech (`000636`), Leo Group (`002131`), and Yuheng Pharmaceutical (`002437`) while keeping thresholds frozen independently of their known future returns.

#### Scenario: A known exemplar is replayed

- **WHEN** an exemplar is evaluated at a historical cutoff
- **THEN** only bars and theme facts visible by that cutoff may determine setup and launch state, and forward returns may be reported only as outcomes rather than formula inputs

### Requirement: Single-day-volume watches use bounded next-morning confirmation

The system SHALL evaluate only prior-session A-share low-base observations carrying `single_day_volume_watch=true`, SHALL read at most twenty declared assets, and SHALL use only decision-eligible closed ten-minute facts received no later than a 10:40–11:30 Asia/Shanghai decision cutoff.

#### Scenario: Next morning confirms while extension remains safe

- **WHEN** at least seven continuous closed ten-minute bars are visible, cumulative observed volume is at least 1.20 times the prior-twenty full-day mean, normalized current price is above the signal close and at or above signal MA5, and symmetric distance from signal MA20 is at most 1.50 signal ATR20
- **THEN** an immutable research transition may expose `state=confirmed` and `entry_status=actionable`

#### Scenario: Morning evidence is missing, late, or unsafe

- **WHEN** any required bar was received after the decision cutoff, bars are incomplete, normalization identities conflict, volume remains below the frozen threshold, or ATR extension exceeds 1.50
- **THEN** the observation remains `watch` and the system SHALL NOT infer an actionable intraday state

#### Scenario: Historical daily bars exist without historical intraday receipt evidence

- **WHEN** a historical exemplar has adjusted daily bars but lacks contemporaneously received ten-minute facts
- **THEN** the system SHALL report intraday confirmation as unavailable and SHALL NOT backfill a morning entry from the later daily close
