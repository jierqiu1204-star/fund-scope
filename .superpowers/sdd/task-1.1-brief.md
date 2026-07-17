# Task 1.1: Record active-change overlap

Create `openspec/changes/harden-etf-comprehensive-ranking/overlap-matrix.md`.

Compare this change against these active changes:

- `make-opportunity-final-decision-ranking`
- `improve-etf-cross-sectional-ranking`
- `add-etf-sector-trend-scoring`
- `expand-etf-factor-library`
- `add-etf-theme-catalyst-scoring`

For each relevant pending requirement/task, record one of:

- `retained`: compatible implementation work that v3 should reuse;
- `superseded`: conflicts with the immutable v3 contract or one-way evidence boundary;
- `blocked`: must not deploy, recompute, validate, promote evidence, or feed downstream consumers before a compatible published v3 snapshot exists.

The matrix must include at least: score version/ordering, validation-to-score feedback, sector inputs, factor producers/degraded profiles, theme/catalyst freshness, deployment/recompute/Top-N validation, and downstream allocation/evidence promotion.

Constraints:

- Do not change business code or existing active-change artifacts in this task.
- Do not mark Task 1.2 or 1.3 complete; this task only records the comparison.
- Preserve the modular-monolith dependency direction.
- Validation/backtest evidence is read-only and must never change current ranking, allocation, tracking, alerts, or notifications.
- The corrected score is `final_score_v3`; historical `final_score_v2` remains old-contract evidence.
- Keep the document concise and actionable, with source artifact paths and task identifiers where available.

Verification:

- `openspec validate harden-etf-comprehensive-ranking --strict`
- Confirm the matrix covers all five active changes and each required overlap category.

After verification, update only task 1.1 in `tasks.md` from `[ ]` to `[x]`, commit the task, and write the report to `.superpowers/sdd/task-1.1-report.md`.
