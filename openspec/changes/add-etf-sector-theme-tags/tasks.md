## 1. Coordinator Setup And Parallel Agent Briefing

- [x] 1.1 Coordinator reads proposal, design, specs, and current AGENTS.md rules before assigning work.
- [x] 1.2 Coordinator defines success criteria: full ETF taxonomy coverage, working theme filters, theme heat statistics, portfolio concentration limits, tests passing, and no trading-instruction wording.
- [x] 1.3 Assign Research Agent using 5.5high or 5.4xhigh to study mature ETF/research tool patterns and return a taxonomy report with recommended theme groups, ambiguous ETF cases, and risks.
- [x] 1.4 Assign Backend Agent using 5.3codex to inspect existing ETF metadata, short research services, API serializers, and tests, then return an implementation impact report before coding.
- [x] 1.5 Assign Frontend Agent using 5.3codex to inspect `/short-term` filters, cards, detail layout, and mobile tabs, then return a UI impact report before coding.
- [x] 1.6 Assign Portfolio Agent using 5.3codex to inspect observation portfolio concentration logic and return a theme-cap integration report before coding.
- [x] 1.7 Coordinator reviews agent reports, resolves conflicts, and writes final implementation notes into the change context or task comments before code edits.

## 2. Backend Taxonomy Data Model

- [x] 2.1 Add an additive ETF theme profile model or equivalent additive schema with ETF code, asset bucket, theme group, primary theme, secondary themes, classification source, confidence, reason, and timestamps.
- [x] 2.2 Add Alembic migration for the theme profile storage without modifying existing ETF, signal, tracking, or portfolio columns destructively.
- [x] 2.3 Add repository/query helpers for upserting and reading theme profiles by ETF code and in bulk.
- [x] 2.4 Add tests proving taxonomy refresh is idempotent and duplicate profile rows are not created.

## 3. Backend Taxonomy Classification

- [x] 3.1 Implement deterministic ETF theme classification using manual overrides, index name, fund name, existing category, and keyword rules.
- [x] 3.2 Classify unknown or ambiguous ETFs as `未分类` with low or unknown confidence instead of guessing.
- [x] 3.3 Seed core theme groups and labels including 半导体、人工智能、生物医药、证券、银行、红利、黄金、债券、纳指、恒生科技、宽基、跨境、未分类.
- [x] 3.4 Add a taxonomy refresh job/service that reports total, classified, unknown, low-confidence, inserted, updated, and failed counts.
- [x] 3.5 Add unit tests for clear classification, ambiguous classification, unknown handling, and manual override priority.

## 4. Short Research API Integration

- [x] 4.1 Extend ETF signal item serialization with normalized theme fields while preserving existing response fields.
- [x] 4.2 Add theme filter support to ETF ranking/list endpoints using cached signal items and theme profiles, with pagination preserved.
- [x] 4.3 Add theme heat summary generation from latest ETF signal cache and latest quote freshness without full metric recomputation.
- [x] 4.4 Extend ETF detail response with theme classification evidence and confidence.
- [x] 4.5 Extend short-term status/admin data status with taxonomy coverage counts and latest taxonomy refresh time.
- [x] 4.6 Add API tests for theme-filtered ranking, no-match empty state, theme heat summary, and unknown classification response.

## 5. Observation Portfolio Theme Constraints

- [x] 5.1 Update portfolio allocation to use normalized theme groups instead of raw name matching for concentration control.
- [x] 5.2 Preserve single ETF max weight at 30% while adding deterministic theme group concentration limits.
- [x] 5.3 Treat unknown or low-confidence themes conservatively and record weight or exclusion reasons.
- [x] 5.4 Add included, watch-only, and excluded ETF explanations that mention theme cap or overlap when relevant.
- [x] 5.5 Add tests where multiple high-scoring technology ETFs are capped or de-duplicated by theme concentration.
- [x] 5.6 Add tests proving theme heat does not override stale data, weak entry timing, high premium, low liquidity, or correlation exclusions.

## 6. Frontend ETF Theme Filters And Heat

- [x] 6.1 Add ETF theme filter controls under `全部方向` on `/short-term` with compact desktop and mobile rendering.
- [x] 6.2 Reset pagination to page one when the user changes theme, search, universe, sort, or asset type.
- [x] 6.3 Add a theme heat panel or compact summary showing theme count, average score, top ETF, latest change summary, and freshness.
- [x] 6.4 Show theme tags and classification confidence on ETF cards without bloating the left list.
- [x] 6.5 Show primary theme, secondary themes, theme group, confidence, and classification reason in selected ETF detail.
- [x] 6.6 Show `未分类` and conservative explanation when classification is unknown.
- [x] 6.7 Ensure theme labels remain research metadata and no UI copy says a theme is directly buyable.

## 7. Admin And Job Visibility

- [x] 7.1 Add web-runnable admin job entry for taxonomy refresh if no suitable existing job hook exists.
- [x] 7.2 Show taxonomy refresh result counts in `/admin/jobs` task output.
- [x] 7.3 Show taxonomy coverage summary in `/short-term` status or data-quality area without implying market signal quality.
- [x] 7.4 Ensure taxonomy job failure for one ETF does not block other ETFs.

## 8. Agent Review Loop

- [x] 8.1 Research Agent submits final taxonomy report with mature-tool references, grouping rationale, and ambiguous ETF examples.
- [x] 8.2 Backend Agent submits code report with files changed, tests added, and unresolved backend risks.
- [x] 8.3 Frontend Agent submits code report with UI states, mobile behavior, and screenshots or screenshot instructions.
- [x] 8.4 Portfolio Agent submits concentration report with examples of included, watch-only, and excluded ETFs.
- [x] 8.5 Coordinator reviews all reports, requests follow-up fixes for inconsistencies, and confirms there is no redundant code or unsupported fallback classification.

## 9. Validation

- [x] 9.1 Run backend tests covering taxonomy, short research API, observation portfolio, and existing scheduler behavior.
- [x] 9.2 Run `uv run ruff check .`.
- [x] 9.3 Run `corepack pnpm exec tsc --noEmit`.
- [x] 9.4 Run `corepack pnpm build:static`.
- [x] 9.5 Manually verify `/short-term` desktop and mobile: filters, theme heat, ETF detail, portfolio explanations, and no horizontal overflow.
- [x] 9.6 Verify no existing tracking, email alert, market data, or ranking endpoint behavior is broken by the new fields.

## 10. Deployment Readiness

- [x] 10.1 Coordinator confirms all OpenSpec tasks are complete and specs match implementation.
- [x] 10.2 Commit with a concise Chinese message after tests pass.
- [x] 10.3 Push to Gitee and GitHub if requested.
- [x] 10.4 Deploy to `110.42.222.9` if requested.
- [x] 10.5 After deployment, run taxonomy refresh, ETF signal generation, and observation portfolio generation.
- [x] 10.6 Verify production `/short-term` shows theme filters, theme heat, and theme concentration explanations using current ETF data.
