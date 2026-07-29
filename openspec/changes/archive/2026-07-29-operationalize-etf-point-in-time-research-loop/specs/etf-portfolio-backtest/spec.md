## ADDED Requirements

### Requirement: ETF portfolio backtest accepts complete research ranking cohorts
ETF portfolio backtest SHALL accept only complete immutable point-in-time ranking cohorts for `policy_mode=policy_shadow` and SHALL bind every action result to the ranking and action-policy contract identities.

#### Scenario: Ranking cohort is complete
- **WHEN** a replay date has a complete eligible Top20 research cohort
- **THEN** the portfolio and pure `etf_exit_action_v3` lifecycle may produce research-only positions, actions, simulated fills, and shadow notification facts

#### Scenario: Ranking cohort is incomplete
- **WHEN** coverage, provenance, adjusted price, or score eligibility is incomplete
- **THEN** policy shadow records an exclusion and does not construct a fallback portfolio

### Requirement: ETF policy shadow has a fixed action endpoint
ETF policy-shadow evaluation SHALL use the existing Top20 ten-session tax-and-fee-adjusted mean action-cycle benefit relative to continuing to hold under the declared action execution model.

#### Scenario: Exit action completes
- **WHEN** an action and its comparison hold window complete
- **THEN** the system records action-cycle benefit, costs, execution provenance, and directional stop or profit diagnostics separately

#### Scenario: Directional accuracy improves but benefit does not
- **WHEN** stop or profit directional accuracy improves while cost-adjusted action-cycle benefit does not
- **THEN** the policy is not promoted based on accuracy alone

### Requirement: ETF policy shadow has no production side effects
ETF policy shadow MUST NOT create or update production portfolio, tracked-position, risk-alert, notification, or SMTP records.

#### Scenario: Policy replay completes
- **WHEN** policy-shadow artifacts are committed
- **THEN** only research evidence and replay artifact stores change

### Requirement: ETF policy evidence distinguishes delivery and execution
ETF portfolio backtest SHALL report full policy-shadow simulation, live-notification-linked sensitivity, provider-delivery evidence, and user-confirmed execution as separate result groups with independent sample gates.

#### Scenario: Only simulated evidence exists
- **WHEN** policy shadow has complete simulated actions but no live delivery or user-confirmed execution sample
- **THEN** simulated benefit is reported while live notification and confirmed execution results remain unavailable
