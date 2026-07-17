# Task 1.2 brief

## Task

Remove or supersede every active-change requirement that lets label or validation evidence contribute to a current score, label, rank, allocation, tracked position, alert, or notification.

## Scope

- Documentation/OpenSpec artifacts only; do not modify production code or tests.
- Inspect every active OpenSpec change, with special attention to `improve-etf-cross-sectional-ranking`.
- Replace validation-to-live-score feedback with a strict one-way boundary: validation/backtest/healthcheck consumes published ranking evidence and may motivate a future human-reviewed contract version, but never mutates the current contract or its live outputs.
- Preserve historical truth: `final_score_v2` remains a legacy contract; corrected behavior belongs to `final_score_v3`.
- Keep display-only evidence summaries if useful, but they must not affect score, label, rank, allocation, tracking, alert, or notification.
- Do not perform task 1.3 operational pause annotations.
- Mark only task 1.2 complete in `harden-etf-comprehensive-ranking/tasks.md`.

## Known conflict locations

- `openspec/changes/improve-etf-cross-sectional-ranking/design.md`
- `openspec/changes/improve-etf-cross-sectional-ranking/proposal.md`
- `openspec/changes/improve-etf-cross-sectional-ranking/specs/etf-signal-validation/spec.md`
- `openspec/changes/improve-etf-cross-sectional-ranking/specs/short-term-research/spec.md`

Search all other active changes for equivalent normative wording before concluding.

## Validation

- Strict-validate every changed OpenSpec change plus `harden-etf-comprehensive-ranking`.
- Search active change artifacts and demonstrate no remaining normative wording permits validation/label evidence to alter current live outputs.
- Commit the task changes in one focused commit and report commit hash, changed files, validation results, and any concerns.
