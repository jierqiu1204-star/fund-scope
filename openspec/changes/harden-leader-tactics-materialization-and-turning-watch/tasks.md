## 1. Contracts and storage

- [x] 1.1 Add an additive migration for compact V2 feature facts, stage checkpoints, theme hierarchy metadata, and required uniqueness/index constraints.
- [x] 1.2 Add immutable compact-feature, stage-progress, theme-resolution, and turning-watch domain contracts with canonical hashes and finite-value validation.
- [x] 1.3 Add storage helpers for idempotent feature-page persistence, terminal-count checks, bounded feature reads, atomic finalization, and abandoned-run visibility.

## 2. PIT theme resolution

- [x] 2.1 Extend theme ingestion to preserve fine-theme identifiers, hierarchy level, aliases, source fact hashes, effective time, and receipt time without historical inference.
- [x] 2.2 Implement the cutoff-visible fine-theme-first resolver with explicit SW1 fallback and stable unavailable/fallback reasons.
- [x] 2.3 Add fixtures and tests for rare-earth alias normalization, factual persistence, partial-alias provider failure, fallback provenance, and chunk-invariant peer groups.

## 3. Two-stage low-memory materialization

- [x] 3.1 Extract shared per-asset scalar feature derivation and cross-sectional finalization from the pure V2 engine.
- [x] 3.2 Implement deterministic 5–20 asset Stage A pages with one lease, a <=55-second budget, compact persistence, memory checks, and durable resume.
- [x] 3.3 Implement Stage B compact cross-sectional finalization that starts only at complete terminal coverage and publishes exactly one compatible manifest.
- [x] 3.4 Update the scheduler job to resume compatible stages after resource recovery without duplicate provider work, concurrent runs, or partial publication.
- [x] 3.5 Add interruption, retry, batch-size invariance, manifest idempotency, bounded-query, and low-memory tests.

## 4. Formula and turning-watch behavior

- [x] 4.1 Version the formula/source registry and keep the 120-session peak-volume gate only on breakout.
- [x] 4.2 Add the frozen base-launch relative-volume or own-prior-20-session amount-percentile confirmation and verify old evidence remains immutable.
- [x] 4.3 Implement deterministic non-actionable `turning_watch` classification, normalized threshold distances, and lifecycle/notification isolation.
- [x] 4.4 Extend candidate storage, summary, API schemas, pagination/filter validation, and unavailable-reason vocabulary for turning watches and theme provenance.
- [x] 4.5 Add formula, boundary, lifecycle, and API contract tests including the rare-earth early-rotation fixture.

## 5. Independent ETF materialization and UI

- [x] 5.1 Enable the dedicated ETF V2 materialization flag in deployment configuration while retaining separate resource, readiness, checkpoint, and manifest controls.
- [x] 5.2 Add protected-state identity assertions proving ETF V2 cannot mutate comprehensive ranking, allocation, positions, alerts, notifications, or executions.
- [x] 5.3 Update the V2 panel with turning-watch filtering, passed/failed gates, theme-resolution provenance, A-share stage progress, and isolated ETF/A-share selection.
- [x] 5.4 Add frontend interaction and contract tests for non-actionable wording, empty states, resource waiting, and universe isolation.

## 6. Acceptance

- [x] 6.1 Run migration, focused backend, performance-boundary, frontend interaction, Ruff, domain-boundary, and strict OpenSpec validation in individually bounded commands.
- [x] 6.2 Verify on a production-shaped fixture that staged and pure outputs match, page/group memory is bounded by explicit gates, ETF comprehensive-ranking identities remain unchanged, and rollback flags are documented.
