## ADDED Requirements

### Requirement: ETF Signal Items Include Normalized Theme Metadata
The ETF short-term research service SHALL include normalized theme metadata in ETF signal items and detail responses.

#### Scenario: ETF signal item is serialized
- **WHEN** an ETF appears in a signal run or ranking API response
- **THEN** the response includes normalized theme group, primary theme, secondary themes, classification confidence, and classification source where available

#### Scenario: Theme metadata is missing
- **WHEN** an ETF has no valid theme profile
- **THEN** the response marks the theme as unknown and does not fall back to an unsupported guessed label

### Requirement: ETF Theme Filtering Uses Cached Signals
The ETF short-term research API SHALL apply theme filtering to cached signal items rather than recomputing all ETF metrics during page load.

#### Scenario: Client requests a theme-filtered list
- **WHEN** the client requests ETF rankings with a theme filter
- **THEN** the API filters the latest cached ETF signal items by normalized theme fields and returns paginated results

#### Scenario: Theme filter has no matches
- **WHEN** a theme filter has no matching ETFs in the latest signal cache
- **THEN** the API returns an empty result with a readable state and does not trigger full-universe recomputation

### Requirement: ETF Taxonomy Coverage Is Refreshed From Public Metadata
The ETF research service SHALL classify ETF themes from available public metadata and deterministic overrides.

#### Scenario: ETF universe refresh completes
- **WHEN** ETF universe metadata is refreshed
- **THEN** the taxonomy refresh can classify new or updated ETF records and report classified, unknown, and low-confidence counts

#### Scenario: Classification rule changes
- **WHEN** taxonomy keyword rules or manual overrides are updated
- **THEN** rerunning the taxonomy refresh updates matching ETF profiles without changing historical signal rows unexpectedly

### Requirement: Theme Search Does Not Replace Risk Labels
ETF theme labels SHALL supplement, not replace, existing observation labels, entry-timing labels, data reliability, and risk flags.

#### Scenario: ETF belongs to a hot theme but has risk flags
- **WHEN** an ETF belongs to a high-heat theme but has chase risk, stale data, low liquidity, high premium, or weak entry timing
- **THEN** the ranking/detail response keeps the risk labels and does not promote the ETF solely because of its theme
