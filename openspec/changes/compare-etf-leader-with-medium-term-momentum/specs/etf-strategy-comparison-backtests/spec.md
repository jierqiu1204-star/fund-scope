## ADDED Requirements

### Requirement: A frozen medium-term momentum control is independent of promotion candidates
Cross-route research SHALL expose exactly one additional control that ranks eligible non-clone ETFs by positive adjusted return over 126 trading sessions, using 127 eligible consecutive session closes including the decision date, with descending return and ascending asset code for ties. It SHALL select at most ten ETFs, assign ten-percent target weight per selected asset and retain unfilled slots as cash. This control SHALL have a separate immutable identity and MUST NOT change the original 20-session control, the three-candidate promotion registry, V2 formulas or any existing primary endpoint.

#### Scenario: Monthly decisions are generated
- **WHEN** a frozen comparison starts and subsequently reaches the last exchange session of each calendar month
- **THEN** the control emits one initial decision at the declared start and then one decision at each month-end, coalescing a start that is itself month-end, and executes each at the next eligible exchange-session close

#### Scenario: A non-rebalance session occurs
- **WHEN** the account is between its declared decision dates
- **THEN** it continues daily valuation without resending unchanged target weights or restoring equal weights, and an absent target is not classified as a missing monthly decision

#### Scenario: Positive momentum coverage is limited
- **WHEN** the shared pool passes the preflight requirement of at least ten assets with complete eligible history but fewer than ten have strictly positive 126-session return
- **THEN** only positive-return assets can be selected and remaining slots are cash, while a failure of the preflight history requirement remains unavailable rather than becoming a cash decision; the report does not shorten the lookback or fill from another strategy

#### Scenario: A parameter search is requested
- **WHEN** a caller requests alternative lookbacks, skip periods, Top N values, thresholds or outcome-driven changes within this control
- **THEN** the frozen comparison rejects the request rather than selecting a best historical configuration

### Requirement: Cross-route comparisons preserve route cadence and common capital semantics
The cross-route report SHALL compare the ETF breakout V2 capital account, the frozen medium-term momentum control, and the existing daily_core Top10 hysteresis account under the same declared initial capital, exchange calendar, source mode, eligible universe policy, clone policy, adjusted-price provenance and cost assumptions. Each route SHALL retain its own frozen decision schedule and portfolio rules. Common support SHALL concern input eligibility and valuation, not the intersection of assets selected by different strategies.

#### Scenario: Strategies have different decision frequencies
- **WHEN** a daily hysteresis account, monthly momentum account and event-triggered breakout account run together
- **THEN** each keeps its own decision schedule while all are valued daily with the same next-session-close execution convention and cash/share accounting

#### Scenario: A route is incomplete
- **WHEN** a route lacks a required decision, transition or held-price valuation
- **THEN** completed results for other routes remain available but cross-route differences are restricted to an explicitly reported common uninterrupted interval, with omitted or blocked periods exposed and no joining of disjoint equity segments

#### Scenario: Clone metadata or membership changes
- **WHEN** only future mapping or membership can justify an otherwise historical selection
- **THEN** strict PIT comparison excludes that use without rewriting the V2 cross-sectional formula; exposure grouping, representative selection and exclusions retain their cutoff-visible provenance

### Requirement: Cross-route results expose actual costs and evidence limitations
Cross-route diagnostics SHALL show gross and net return, capital maximum drawdown, traded notional divided by initial capital, cost drag, order and rebalance counts, average exposure and cash, concentration, coverage, blocked intervals and route identities. Gross results SHALL come from a separate zero-cost account. Base costs SHALL remain five basis points fee plus five basis points slippage per side; the existing five-plus-ten basis point stress SHALL be reported without selecting parameters from its results.

#### Scenario: A rebalance changes drifted holdings
- **WHEN** target membership is unchanged at a scheduled rebalance but market prices changed the actual weights
- **THEN** trades needed to restore the targets incur their actual modeled costs, while non-rebalance days incur no invented round trip

#### Scenario: A short diagnostic appears favorable
- **WHEN** one route has higher net return in a short or current-vintage sample
- **THEN** the report may describe that interval's performance but labels the result exploratory with exact evidence gaps and does not produce a validated winner or alter production

### Requirement: Cross-route research does not consume or weaken existing validation gates
Cross-route controls and capital diagnostics SHALL remain outside formal ranking promotion and MUST NOT consume an existing holdout, change its authorization or one-time-use state, replace the frozen five-session primary endpoint, or lower existing sample and uncertainty gates. The comparison's configuration and diagnostic interval SHALL be recorded before outcome evaluation; later variants require separate identities and cannot be tuned against the same results and reported as independent validation.

#### Scenario: A diagnostic run requests locked outcomes
- **WHEN** an exploratory comparison attempts to access an existing locked holdout or source case
- **THEN** it fails closed through the existing protection and leaves authorization and consumption state unchanged

#### Scenario: Research completes without sufficient independent data
- **WHEN** implementation and replay succeed but PIT sessions, independent dates, chronological folds or required risk evidence are insufficient
- **THEN** software completion and strategy evidence status are reported separately, without waiting for future data to declare the software delivered or claiming that the strategy passed
