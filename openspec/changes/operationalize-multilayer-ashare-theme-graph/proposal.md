## Why

The production leader-tactics screen currently has no persisted fine-theme members and therefore evaluates every stock inside a broad industry fallback, while its resolver can retain only one fine theme per stock. This makes labels such as innovation drugs, rare-earth magnets, or MLCC unavailable in practice and weakens peer comparison even though the screen appears operational.

## What Changes

- Introduce a versioned A-share classification model with one point-in-time primary industry path, zero-to-many point-in-time theme memberships, and a separate daily theme-state fact.
- Preserve provider codes, canonical theme identities, membership reasons, source confidence, effective windows, receipt time, and immutable hashes instead of relying on hard-coded display labels.
- Capture bounded daily concept snapshots and materialize membership diffs without requiring a full-market in-memory join or an unbounded provider call.
- Resolve every candidate against all compatible fine themes, select a deterministic peer context only after minimum-peer and freshness gates pass, and retain the full multi-theme evidence set.
- Calculate theme breadth, relative return, volume participation, leader count, and available limit-board facts independently from membership; unavailable inputs fail closed rather than becoming neutral values.
- Keep broad-industry fallback explicit, standardize its hierarchy provenance, and prevent mixed taxonomy levels from being presented as equivalent fine themes.
- Expose classification coverage, theme capture health, selected peer context, alternative memberships, daily theme state, and stable unavailable reasons through existing read-only research APIs.
- Keep the change research-only: it does not alter ETF comprehensive ranking, ETF theme taxonomy, allocations, tracked positions, alerts, notifications, or execution state.

## Capabilities

### New Capabilities

- `ashare-multilayer-theme-graph`: Versioned primary-industry paths, multi-label PIT theme memberships, daily theme-state facts, bounded capture, and deterministic resolution.

### Modified Capabilities

- `a-share-point-in-time-research-data`: Require multi-label membership and independent theme-state data with complete PIT provenance and observable coverage.
- `dual-universe-leader-tactics-screen`: Evaluate fine-theme peer contexts without collapsing a stock to one theme and fail closed when the selected context is stale or too small.
- `dual-universe-leader-tactics-evidence`: Persist selected and alternative theme contexts, daily state facts, capture health, and fallback provenance.

## Impact

- Backend research models, SQL migrations, bounded theme collectors, materialization adapters, scheduler jobs, candidate storage, and read-only strategy-lab APIs.
- Focused backend and migration tests plus domain-boundary, Ruff, strict OpenSpec, and production smoke validation.
- Additional append-only research storage and bounded post-close work; no changes to public trading APIs or protected ETF production tables.
