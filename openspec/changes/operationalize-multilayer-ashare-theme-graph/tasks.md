## 1. Contracts and storage

- [x] 1.1 Add additive Alembic tables and indexes for industry paths, capture runs, and daily theme states, and extend fine-theme relations with source-code/reason/weight/snapshot fields.
- [x] 1.2 Add immutable dataclasses, canonical hash validation, bounded persistence helpers, and source registry contracts for the new facts.
- [x] 1.3 Add migration and contract tests covering multi-label uniqueness, source snapshot sealing, non-finite rejection, and rollback safety.

## 2. Reliable hierarchy and theme capture

- [x] 2.1 Parse the TickFlow universe catalog into aligned SW1/SW2/SW3 paths and fetch only bounded SW3 member batches.
- [x] 2.2 Persist complete industry-path snapshots with deterministic checkpoints, expected counts, content hashes, and explicit broad fallback coverage.
- [x] 2.3 Extend the canonical theme registry with provider-concept and industry-union relation kinds without claiming proxy membership as factual provider data.
- [x] 2.4 Materialize curated multi-label theme relations from disclosed SW3 unions and retain the bounded Eastmoney current-concept path as an optional independent source.
- [x] 2.5 Wire one-worker scheduler continuations with hard request, response-size, memory, and 55-second invocation bounds.

## 3. Theme state and deterministic resolution

- [x] 3.1 Load one industry path and all compatible theme memberships per A-share input without collapsing relations during ingestion.
- [x] 3.2 Implement the frozen context resolver with complete-snapshot, cutoff, freshness, five-peer, relation-kind, confidence, registry-priority, and hierarchy-depth gates.
- [x] 3.3 Materialize immutable daily theme-state facts from compact staged features one context group at a time.
- [x] 3.4 Make A-share hot-theme gates consume persisted compatible theme state and preserve broad fallback with exact reasons.
- [x] 3.5 Include selected, alternative, and rejected contexts plus theme-state identity in staged hashes, candidate facts, and manifests.

## 4. Read surfaces and isolation

- [x] 4.1 Extend existing read-only candidate and summary responses with hierarchy paths, multi-theme relations, theme states, coverage, snapshot age, and provider health.
- [x] 4.2 Add stable unavailable reasons for partial capture, stale snapshot, insufficient peers, missing state, and incompatible taxonomy.
- [x] 4.3 Add boundary tests proving A-share capture/materialization cannot mutate ETF comprehensive ranking, ETF theme taxonomy, positions, alerts, notifications, or execution state.

## 5. Verification and production activation

- [x] 5.1 Run focused migration, capture, resolver, formula, API, scheduler, and low-resource continuation tests with per-command bounds no greater than 60 seconds.
- [x] 5.2 Run backend domain-boundary tests, targeted Ruff, and strict OpenSpec validation with per-command bounds no greater than 60 seconds.
- [ ] 5.3 Commit and push the change, verify automatic deployment and migration health, and record the rollback switch.
- [ ] 5.4 Complete one bounded production hierarchy capture and theme-state materialization, then verify real row counts, coverage, provider health, selected fine contexts, candidate provenance, and unchanged ETF protected identities.
