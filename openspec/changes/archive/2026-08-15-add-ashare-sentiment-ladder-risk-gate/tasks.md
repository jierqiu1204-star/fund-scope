## 1. Risk contract and calculation

- [x] 1.1 Add the frozen A-share middle-echelon proxy contract, stable hash, scalar input/result types, finite-value validation and deterministic threshold evaluation.
- [x] 1.2 Add unit tests for healthy, warning, risk-off, unavailable, deterministic ordering, boundary thresholds and unsupported limit-board facts.

## 2. Screen integration and isolation

- [x] 2.1 Compute the A-share risk snapshot once per screen from existing hot/core features and adjusted bars, then attach compact risk facts without changing raw scores, qualification or lifecycle.
- [x] 2.2 Apply the shadow action mapping only to A-share breakout observations and prove base-launch, former-leader-repair and every ETF path remain non-applicable and unchanged.

## 3. Persisted evidence and API

- [x] 3.1 Persist risk facts through existing observation gate facts and project validated top-level risk state, action mode and entry permission from read-only candidate responses, including stable legacy/unavailable handling.
- [x] 3.2 Add summary risk-state/action counts from persisted evidence only, with no provider call, database migration or production mutation.
- [x] 3.3 Add API/storage tests for manifest immutability, pagination compatibility, old-row handling, provenance and ETF isolation.

## 4. Research UI

- [x] 4.1 Extend the frontend contract validator and A-share leader panel with concise healthy/warning/risk-off/unavailable and shadow-action badges while keeping ETF rendering unchanged.
- [x] 4.2 Add frontend interaction/contract tests for risk labels, observe-only semantics and research-only wording.

## 5. Validation and handoff

- [x] 5.1 Add a frozen policy-shadow comparison helper or diagnostics contract that preserves the existing five-session theme-relative cost-adjusted primary and rejects runtime threshold changes.
- [x] 5.2 Run focused backend and frontend tests, backend domain-boundary tests, Ruff on changed Python files, and strict OpenSpec validation with bounded commands.
- [x] 5.3 Review performance and safety: O(n) cross-section work, no per-row bar copies, no extra SQL/provider work, no ETF comprehensive-ranking imports, and no production side effects.
