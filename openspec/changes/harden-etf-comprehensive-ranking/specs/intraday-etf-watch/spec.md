## MODIFIED Requirements

### Requirement: Intraday ETF Watchlist Is Derived From Daily Signals
The system SHALL build each intraday ETF watch scope from a compatible fresh published full-scope daily ETF ranking plus owner-scoped active tracked positions and SHALL persist a watch-scope hash before user presentation filters.

#### Scenario: Top 20 ETFs are selected
- **WHEN** a compatible current ETF ranking snapshot exists
- **THEN** the intraday watch scope includes the top 20 items from that full snapshot and records the source snapshot id and score version

#### Scenario: Held ETFs remain watched
- **WHEN** an active tracked ETF is not in the latest top 20 ranking
- **THEN** the scheduler may include that ETF for its owner while API presentation marks `tracked_position` only for the authenticated owner

#### Scenario: Daily signal is unavailable
- **WHEN** no compatible fresh full ETF snapshot exists
- **THEN** tracked quotes may remain visible with limitations but the service returns a readable missing-base state and does not calculate an intraday comprehensive rank

#### Scenario: Later partial signal run exists
- **WHEN** a theme or code-scoped signal run finishes after the full snapshot
- **THEN** the intraday watch scope continues using the compatible full snapshot

### Requirement: Intraday Ranking Splits Base Score And Live Adjustment
The system SHALL expose daily snapshot identity, daily base score, intraday adjustment, live total, score source, rank scope, and readable contribution reasons, and SHALL calculate a live total only when both base and quote contracts are eligible.

#### Scenario: Fresh intraday quote and base exist
- **WHEN** an ETF has a fresh eligible quote and a compatible fresh daily snapshot during an open exchange session
- **THEN** the API returns `score_source=intraday`, snapshot identity, daily base score, intraday adjustment, live total, quote time, component eligibility, and contribution reasons

#### Scenario: Market is closed or quote is stale
- **WHEN** the market is closed or the ETF quote is stale, estimated, diverged, or missing quote time
- **THEN** the API returns `score_source=daily` or unavailable and MUST NOT present the score as real-time

#### Scenario: Daily base is stale or incompatible
- **WHEN** the quote is fresh but the source daily snapshot is stale, partial, legacy, or contract-incompatible
- **THEN** the quote remains displayable but live total and live rank are unavailable

### Requirement: Intraday Ranking Uses Cross-Validated Quote Eligibility
The system SHALL apply intraday ranking adjustments only when the selected quote is fresh, finite, cross-validation eligible, and compatible with the declared live component inputs.

#### Scenario: Quote is cross-validation eligible
- **WHEN** the primary quote is fresh and has `consensus_status=consistent` or eligible `single_provider` with required price, time, turnover, spread, and premium fields
- **THEN** the live ranking may use only the eligible declared fields and records their source and freshness

#### Scenario: Quote is not cross-validation eligible
- **WHEN** the primary quote is diverged, stale, server-time fallback, estimated, non-finite, or unavailable
- **THEN** the live ranking returns daily score or unavailable realtime status and MUST NOT present the quote as realtime decision evidence

#### Scenario: Optional structure field is missing
- **WHEN** premium, spread, or same-time activity evidence is unavailable
- **THEN** that adjustment is unavailable and its weight is not silently transferred to another live component

## ADDED Requirements

### Requirement: Live Rank Identity Precedes User Filters
The system SHALL calculate daily rank within the full snapshot and live rank within one frozen watch scope before applying search, theme, label, tracking, pagination, or page-size filters.

#### Scenario: User searches for one ETF
- **WHEN** the ETF is the only filtered result but was live-scope rank 20
- **THEN** the response retains live-scope rank 20, returns filtered position 1, and does not claim a nineteen-place rise

#### Scenario: Live rank change is calculated
- **WHEN** daily base order and live order are compared
- **THEN** both ranks use the same frozen live-scope hash and score version; otherwise rank change is null

#### Scenario: Static and live scores tie
- **WHEN** multiple ETFs have equal live totals
- **THEN** the live service preserves compatible base order and uses the same deterministic code tie-break as the daily service

### Requirement: Intraday Adjustments Are Time And Volatility Normalized
The system SHALL normalize price movement by the ETF's point-in-time volatility or ATR and normalize cumulative turnover against historical observations at the same exchange minute and asset bucket.

#### Scenario: Same relative activity occurs in morning and afternoon
- **WHEN** two observations have equivalent same-time relative turnover in their respective sessions
- **THEN** they receive equivalent activity treatment rather than penalizing the earlier observation for having less full-day turnover

#### Scenario: Same percentage move occurs in different volatility buckets
- **WHEN** a low-volatility ETF and a high-volatility ETF move by the same percentage
- **THEN** their live price adjustments reflect the versioned volatility-normalized thresholds rather than one universal percentage threshold

#### Scenario: Same-time history is insufficient
- **WHEN** an ETF lacks the minimum same-minute historical activity sample
- **THEN** activity adjustment is unavailable and is not automatically set to a penalty or bonus

### Requirement: Quote Collection And User Presentation Scopes Are Separate
The system SHALL separate the global scheduler quote-collection scope from the authenticated user's tracking-filter and watch-source presentation scope.

#### Scenario: Another user holds an ETF
- **WHEN** user A holds an ETF and user B does not
- **THEN** the scheduler may collect its quote, but user B does not receive `tracked_position` as a source and cannot match it through `我已持仓`

#### Scenario: Watched count is displayed
- **WHEN** a user requests live rankings
- **THEN** the response count is defined for that response's declared public and authenticated presentation scope and does not leak another user's private holdings

### Requirement: Tracking Alert Filters Use Current Relevant State
The system SHALL match `触发提醒` and `仅网页提示` filters from the authenticated user's current non-expired alert state rather than any historical latest row without a relevance check.

#### Scenario: Old alert no longer applies
- **WHEN** a tracked ETF had an alert on an earlier date but the condition is no longer current
- **THEN** it does not continue matching the active-alert filter solely because that historical row exists

### Requirement: Live Quote Refresh Crosses Session Boundaries
The workbench and API SHALL refresh session state at Asia/Shanghai open, lunch, afternoon reopen, close, and exchange-calendar day transitions independently of the previous response state.

#### Scenario: Page was opened before market open
- **WHEN** time reaches the next exchange opening boundary
- **THEN** live quote queries refresh once even if the previous response said closed

#### Scenario: Session is open
- **WHEN** an eligible open-session response is received
- **THEN** subsequent polling uses the server-provided interval until the next boundary or response-state change
