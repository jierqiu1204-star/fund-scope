## 1. Data Model And Configuration

- [x] 1.1 Add database models and Alembic migration for LLM research report records and generation attempts.
- [x] 1.2 Add settings for enabling the advisor, model base URL, model name, API key, max analyzed assets, timeout, and prompt version.
- [x] 1.3 Add server `.env` documentation for ECNU-compatible configuration without exposing the API key to the frontend.

## 2. LLM Client And Validation

- [x] 2.1 Extend `LLMClient` with a structured short-term research report method using OpenAI-compatible chat completions.
- [x] 2.2 Add a JSON schema and prompt for conservative fund/ETF research reports.
- [x] 2.3 Implement backend validation for required fields, allowed action labels, conservative action downgrades, and prohibited trading language.
- [x] 2.4 Add deterministic fallback report generation when the model is disabled, unavailable, quota-limited, or returns invalid output.

## 3. Advisor Service And Jobs

- [x] 3.1 Implement a service that selects latest short-term signal candidates for LLM analysis, including Top N, watched assets, held assets, and changed-action assets.
- [x] 3.2 Persist valid advisor reports idempotently per signal run and asset.
- [x] 3.3 Record failed model attempts with asset code and readable error message while continuing the batch.
- [x] 3.4 Add manual admin job endpoint and scheduler registration so the advisor runs after data sync and deterministic short-term signal generation.

## 4. API And Frontend

- [x] 4.1 Add API endpoints or extend short-term responses to return advisor reports alongside ranked assets.
- [x] 4.2 Add `/short-term` UI cards for 今日研究建议, action label, reason, risks, opposing view, watch conditions, and data limitations.
- [x] 4.3 Show clear Chinese empty, disabled, failed, and stale-report states without hiding deterministic rankings.
- [x] 4.4 Add admin job controls and run result summaries for generating AI research reports from the web.

## 5. Safety And Tests

- [x] 5.1 Add backend tests proving LLM output cannot change deterministic rank, score, label, or risk flags.
- [x] 5.2 Add backend tests for invalid JSON, missing API key, provider failure, action downgrade, and prohibited language rejection.
- [x] 5.3 Add job tests for idempotent reruns and partial failure handling.
- [x] 5.4 Add frontend tests or type checks for rendering reports, fallback explanations, and disabled states.
- [x] 5.5 Run regression commands: `uv run pytest`, `uv run ruff check .`, `uv run mypy app`, `corepack pnpm exec tsc --noEmit`, and `corepack pnpm build:static`.
