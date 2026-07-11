## MODIFIED Requirements

### Requirement: Short-Term Scores Are Deterministic And Explainable
The system SHALL rank assets using a versioned deterministic component manifest, SHALL expose the exact score-bearing inputs and limitations for each rank, SHALL calculate the final score once after all declared inputs are available, and SHALL apply stale, unavailable, and risk hard limits after every soft component.

#### Scenario: Ranking uses short-term windows
- **WHEN** the system generates short-term signals
- **THEN** it uses recent windows such as 5, 10, 20, and 60 trading days rather than long-term-only metrics

#### Scenario: Risk reduces conclusion strength
- **WHEN** an asset has chase risk, high volatility, large drawdown, stale data, low liquidity where applicable, or insufficient sample history
- **THEN** the conclusion is downgraded, the risk flags are returned, adding a risk cannot increase the score, stale data cannot exceed its configured cap, and unavailable data cannot exceed its configured cap

#### Scenario: Ranking does not require an API key
- **WHEN** no LLM or third-party AI API key is configured
- **THEN** the system still generates deterministic rankings, charts, labels, and rule-based explanations

#### Scenario: Validation evidence exists
- **WHEN** label, Top-N, replay, backtest, or comparison evidence is available
- **THEN** the evidence is exposed as research context and MUST NOT alter the current score, label, rank, allocation, tracked position, alert, or notification

#### Scenario: Declared score input is unavailable
- **WHEN** a score-bearing component lacks finite decision-eligible inputs
- **THEN** the current final score is unavailable and the system does not replace the component with zero, a neutral value, stale data, or silently renormalized weights

### Requirement: Short-Term Research Reads Cached Results Efficiently
The system SHALL serve `/short-term` ranking data from the latest compatible published full-scope signal snapshot without recomputing all assets during page load and without falling back to partial, legacy, stale, or incompatible runs.

#### Scenario: First page ranking uses cached signal items
- **WHEN** the frontend requests the first page of short-term ETF rankings
- **THEN** the backend returns paginated items from the compatible published full snapshot and does not scan all ETF price history to recompute every asset

#### Scenario: Observation portfolio avoids unnecessary full recomputation
- **WHEN** the frontend requests the ETF observation portfolio
- **THEN** the backend uses the same compatible snapshot and only loads additional history for shortlisted portfolio candidates

#### Scenario: Only an incompatible cache exists
- **WHEN** available cached runs are partial, legacy, stale, or use a different ranking contract
- **THEN** the API returns a readable waiting, stale, or unavailable state and does not present that cache as current comprehensive ranking

### Requirement: Short-Term Workbench Supports Label Filtering
The short-term research workbench SHALL allow users to filter ranked ETF or fund cards by observation labels, entry timing labels, and current-user tracked-position status without changing the stored global ranking identity.

#### Scenario: User filters by observation label
- **WHEN** the user selects one or more buy-observation labels such as `短线观察` or `高位观察`
- **THEN** the ranked list shows only assets matching at least one selected buy-observation label while preserving each asset's global rank and selected sort mode

#### Scenario: User filters by entry timing
- **WHEN** the user selects one or more entry timing labels such as `健康回踩`, `趋势延续`, or `冲高别追`
- **THEN** the ranked list shows only assets matching at least one selected entry timing label while exposing its filtered position separately

#### Scenario: User filters by holding status
- **WHEN** the user selects holding filters such as `我已持仓`, `触发提醒`, or `仅网页提示`
- **THEN** both static and live ranking paths return only assets matching the selected tracked-position state for the authenticated user

#### Scenario: Tracking filter has no authenticated user
- **WHEN** a tracking-state filter is requested without an identifiable current user
- **THEN** the system returns an authentication or empty scoped result and MUST NOT silently return the unfiltered ranking or another user's positions

## ADDED Requirements

### Requirement: Comprehensive Ranking Uses A Versioned Component Manifest
The system SHALL publish a component DAG and manifest with every comprehensive score version that identifies score-bearing and explanatory components, primitive-factor lineage, fixed weights, input eligibility, asset-bucket scope, missing-data behavior, and the final score field.

#### Scenario: Auxiliary factor has no production source
- **WHEN** a theme, flow, fundamental, valuation, macro, sector, or premium component lacks a point-in-time production source required by the manifest
- **THEN** it is shown as explanatory unavailable data and is not advertised or used as a score-bearing component

#### Scenario: Component behavior changes
- **WHEN** a weight, required input, eligibility rule, or price basis changes
- **THEN** the system creates a new score version and ranking contract instead of reinterpreting historical scores

#### Scenario: Composite component uses primitive factors
- **WHEN** a score-bearing composite already contains one or more primitive factors
- **THEN** the manifest prevents those same primitives from contributing again through another weighted path

### Requirement: Ranking Sorts Preserve Missingness And Stable Ties
The system SHALL treat finite zero values as real values, place missing or non-finite sort metrics after all valid values, and use one stable tie-break across static and live rankings.

#### Scenario: Return is exactly zero
- **WHEN** an ETF has a valid zero return and another ETF has a negative return
- **THEN** descending return sort places the zero return ahead of the negative return rather than treating zero as missing

#### Scenario: Drawdown is missing
- **WHEN** an ETF has no valid drawdown metric
- **THEN** drawdown-low sort places it after every ETF with a valid drawdown instead of treating it as zero drawdown

#### Scenario: Two scores are equal
- **WHEN** static and live items have equal scores and no live adjustment
- **THEN** both paths use the same deterministic base-rank and code tie-break and do not manufacture a rank change

### Requirement: Workbench Shows Snapshot And Ranking Scope
The short-term workbench SHALL display the ranking snapshot's trade date, generation time, score version, freshness, coverage, and explicit global versus filtered rank context.

#### Scenario: Filter is active
- **WHEN** an ETF is global rank 20 and first in the current filtered result
- **THEN** the UI presents the equivalent of `全榜 #20 · 筛选结果第 1` and does not label it simply as rank one

#### Scenario: Ranking is stale or unavailable
- **WHEN** no compatible fresh snapshot exists
- **THEN** the workbench shows the exact stale/waiting/unavailable state and does not combine a newer global data date with an older ranking date

### Requirement: Workbench View States Are Distinct And Recoverable
The short-term workbench SHALL distinguish loading, ready, filtered-empty, no-snapshot, stale, unavailable, and request-error states and SHALL keep pagination recoverable as totals change.

#### Scenario: Ranking request fails
- **WHEN** the ranking API returns an error
- **THEN** the UI shows a request-error state and does not simultaneously claim that no assets match the filters

#### Scenario: Active filters return zero items
- **WHEN** a successful response has zero filtered items
- **THEN** the UI shows a filter-specific empty state with a clear-filter action

#### Scenario: Total shrinks below the current page
- **WHEN** refreshed total count makes the current offset invalid
- **THEN** the workbench moves to the last valid page and retains a previous-page action whenever the offset is greater than zero

### Requirement: User-Scoped Research Queries Are Cache-Isolated
The frontend SHALL key private ranking, tracking, and position queries by stable authenticated user identity and SHALL cancel and remove those queries when identity changes or authentication ends.

#### Scenario: User changes in one browser tab
- **WHEN** user A logs out and user B logs in
- **THEN** user B never sees user A's tracked positions, tracking-filtered ranking, or private source tags from cache

#### Scenario: Old request completes after logout
- **WHEN** an in-flight private request for user A completes after authentication changed
- **THEN** request cancellation or identity-key isolation prevents it from updating user B's visible state

### Requirement: Manual Research Operations Respect Workflow Dependencies
The workbench SHALL prevent overlapping manual data synchronization, signal generation, and advisor generation for the same research scope and SHALL bind downstream output to an explicit completed signal snapshot.

#### Scenario: Data synchronization is running
- **WHEN** a user starts the synchronization workflow
- **THEN** signal and advisor actions for that scope remain disabled until synchronization and publication gates complete

#### Scenario: Advisor generation starts
- **WHEN** the user requests an advisor explanation
- **THEN** the request identifies the completed source snapshot and cannot silently explain an older run

### Requirement: Short-Term Business Dates Use Asia Shanghai Calendar Semantics
The workbench SHALL create and format exchange business dates using Asia/Shanghai calendar fields rather than UTC date slicing or browser-dependent parsing.

#### Scenario: User opens the page during Shanghai early morning
- **WHEN** local Shanghai time is between 00:00 and 07:59
- **THEN** the default business date is the current Shanghai date and not the previous UTC date

#### Scenario: Browser uses a negative UTC offset
- **WHEN** the UI formats a stored `YYYY-MM-DD` exchange date
- **THEN** it displays the same calendar date without shifting to the previous day

### Requirement: Comprehensive Ranking Terminology Is Unambiguous
The API and workbench SHALL distinguish the comprehensive final score from theme/catalyst opportunity evidence while preserving documented legacy sort compatibility.

#### Scenario: Legacy opportunity sort is requested for ETFs
- **WHEN** an existing client requests the ETF `opportunity` sort mode
- **THEN** the backend orders by the declared comprehensive final score and identifies the compatibility alias in ranking metadata

#### Scenario: Theme opportunity is displayed
- **WHEN** an ETF has a high theme/catalyst opportunity value but a lower comprehensive final score
- **THEN** the theme value remains visible as auxiliary explanation and does not determine the comprehensive order

### Requirement: Workbench Uses One Effective Live Status
The workbench SHALL derive quote status, exchange session, refresh time, watched count, and polling behavior from the applicable live-list or selected-ETF live response rather than from an unrelated static response.

#### Scenario: Default comprehensive page has selected live data
- **WHEN** the static ETF ranking is displayed and the selected ETF live response reports an open market
- **THEN** the workbench reports the open state and selected quote context instead of `休市`, waiting, or zero watched items caused by absent static live metadata

#### Scenario: No live response is available
- **WHEN** neither the live list nor selected-ETF live query has returned usable status
- **THEN** the workbench shows an explicit waiting/unavailable state and does not infer closed market

### Requirement: Workbench Counts Declare Their Scope
Every observation, risk, watch, and issue count shown by the workbench SHALL identify whether it represents the full snapshot, filtered result, current page, authenticated user, or live watch scope.

#### Scenario: Summary is calculated from one page
- **WHEN** a count is derived only from the current paginated items
- **THEN** the UI labels it as a current-page count and does not present it as a full-universe total

#### Scenario: API supplies full filtered totals
- **WHEN** the backend returns a count before pagination
- **THEN** the UI may present it as the filtered total with its filter scope
