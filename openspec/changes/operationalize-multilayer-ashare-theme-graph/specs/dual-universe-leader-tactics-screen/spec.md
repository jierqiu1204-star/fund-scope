## ADDED Requirements

### Requirement: A-share peer context is selected from compatible classifications
For each A-share observation the screen SHALL evaluate cutoff-visible theme and industry contexts, reject stale or incomplete source snapshots, require at least five decision-eligible peers, and select one context by a frozen deterministic policy that prefers factual provider themes, then declared industry-union proxies, then the finest complete primary-industry level, and finally an explicit broad fallback. The policy SHALL NOT select a context solely because it produces the highest candidate score.

#### Scenario: Multiple themes are compatible
- **WHEN** more than one theme has a complete cutoff-visible state and sufficient peers
- **THEN** the frozen source, confidence, relation-kind, and registry-priority policy chooses the context and exposes all alternatives

#### Scenario: Fine context is too small
- **WHEN** a fine theme or level-three industry has fewer than five decision-eligible peers
- **THEN** it is recorded as rejected context and resolution proceeds to the next factual ancestor without inflating the peer set

#### Scenario: No compatible context exists
- **WHEN** all theme and industry contexts are missing, stale, partial, or below the peer minimum
- **THEN** the observation fails closed with `missing_compatible_peer_context`

### Requirement: Theme heat uses independent daily state
The A-share `hot_score` SHALL use the selected peer context's immutable daily theme-state facts and SHALL expose the state hash, member count, breadth, returns, participation, leader count, and unavailable fields. Membership alone SHALL NOT imply that a theme is hot.

#### Scenario: Theme membership is current but theme state is unavailable
- **WHEN** the candidate belongs to a theme whose compatible daily state is unavailable
- **THEN** the hot-theme gate fails with `theme_state_unavailable` and no neutral or broad-industry theme score is silently substituted

### Requirement: ETF ranking and A-share classification graph remain isolated
The A-share classification graph, capture jobs, theme-state materialization, and resolver SHALL remain inside the strategy-lab research boundary and SHALL NOT write or alter ETF comprehensive-ranking inputs, taxonomy, scores, ordering, publication gates, alerts, or notifications.

#### Scenario: A-share theme materialization completes
- **WHEN** a new complete industry or theme snapshot becomes available
- **THEN** only A-share research manifests are invalidated or regenerated and protected ETF identities remain unchanged

