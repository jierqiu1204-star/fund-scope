## Context

The FundScope MVP is mostly implemented and the asset recommendation change has been archived. The remaining `add-fundscope-mvp` tasks are not one more feature vertical; they are a mix of test coverage, spec drift, deployment readiness, and manual verification evidence.

The highest-risk mismatch is in `valuation-monitoring`: the spec says index watchlist entries can be added and removed and that valuation fetch has a fallback path, while the current implementation only has seeded watchlist indices and an AKShare primary fetch. The release-readiness pass should either implement those small missing behaviors or explicitly narrow the spec before archive. Since the MVP already has user-facing valuation pages and scheduled valuation jobs, the better choice is to implement the missing backend support and tests.

## Goals / Non-Goals

**Goals:**

- Close the remaining MVP verification gaps with fresh automated tests and documented manual checks.
- Add deterministic tests for data ingestion fallback behavior without relying on live external network calls.
- Bring valuation-monitoring behavior into alignment with its spec by adding index watchlist management and fallback handling.
- Verify or document the Docker Compose local stack path.
- Provide a concrete secret/deployment checklist for external VPS work without storing secrets in the repo.
- Leave `add-fundscope-mvp` ready to archive after validation.

**Non-Goals:**

- No new investing features.
- No real broker or trading integration.
- No production secret creation from this repository.
- No requirement to deploy to a real VPS during local-only development.
- No broad frontend redesign.

## Decisions

### D1. Treat This as Release Hardening, Not Product Expansion

**Decision:** Keep this change limited to tests, small missing API/data-source behaviors, deployment verification, and documentation.

**Rationale:** The product already has portfolio tracking, valuation, news, reminders, onboarding, deployment files, and recommendations. More feature work would delay the more important milestone: a cleanly verifiable MVP.

**Alternatives considered:**

- Start a new feature such as richer stock data ingestion: useful later, but premature while MVP tasks remain open.
- Archive the MVP with warnings: faster, but leaves unresolved spec drift and weakens the project as a portfolio artifact.

### D2. Implement Minimal Index Watchlist Management

**Decision:** Add small backend support for adding/removing index watchlist entries if tests reveal it is missing, with no complex frontend editor unless required for verification.

**Rationale:** The valuation spec already requires add/remove behavior. A minimal API keeps the implementation aligned while avoiding a larger settings UI.

**Alternatives considered:**

- Remove those scenarios from the spec: acceptable only if the user wants a strictly fixed index set, but less flexible and contradicts the current spec.
- Build a full index management UI: unnecessary for MVP closure.

### D3. Add a Valuation Fallback Boundary

**Decision:** Make `index_data.py` mirror `fund_data.py`: primary fetch through AKShare, fallback through a separate provider function that can be tested with stubs.

**Rationale:** The spec requires fallback behavior and job logging. A provider boundary lets tests cover retries and fallback without real network calls.

**Alternatives considered:**

- Use live CSIndex website requests in tests: unstable and slow.
- Leave fallback undocumented: conflicts with existing spec and task list.

### D4. Convert External Environment Work into Checklists and Evidence

**Decision:** For GitHub/Gitee secrets and VPS deployment, document required values and verification commands instead of attempting to create secrets.

**Rationale:** Secrets and a real VPS are outside the local workspace and should not be embedded in source control. The repo can still be release-ready by making the steps exact and auditable.

**Alternatives considered:**

- Skip external tasks: leaves the MVP change incomplete.
- Store sample private values: unacceptable security practice.

## Risks / Trade-offs

- **[External data providers are unstable]** -> Test provider parsing and fallback with local stubs; keep live checks manual.
- **[Docker Compose may fail on a local machine without Docker running]** -> Record the exact blocker and provide commands; do not fake success.
- **[VPS deployment cannot be completed locally]** -> Document secret names, expected deployment command, and post-deploy checks.
- **[Scope creep from index management UI]** -> Implement API-level support first; add UI only if needed to satisfy verification.
- **[MVP tasks and this new change overlap]** -> Treat this change as the mechanism for closing `add-fundscope-mvp`; update both task lists during implementation.

## Migration Plan

1. Add tests around current behavior and watch them fail where gaps exist.
2. Implement only the missing behavior needed for tests and spec alignment.
3. Run full backend/frontend verification and OpenSpec validation.
4. Mark corresponding `add-fundscope-mvp` tasks complete when evidence exists.
5. Archive `finish-fundscope-mvp-verification`, then archive `add-fundscope-mvp` once all 81 tasks are complete.

No database migration is expected unless index watchlist management needs additional metadata; current `indices.is_watchlist` should be sufficient.

## Open Questions

1. Should the final VPS deployment be executed now if credentials are available, or should local-only verification remain the acceptance bar?
2. Should screenshots be committed under docs/assets, or generated locally and referenced in README without storing binary files?
