## 1. Data Model And Defaults

- [x] 1.1 Add short-term research schemas for normalized asset summaries, detail responses, status responses, signal runs, signal items, chart series, and data health.
- [x] 1.2 Add or extend default seed data to cover about 200 eligible funds and ETFs with asset type, theme tags, investment direction, and trading rule labels.
- [x] 1.3 Add idempotent migration or startup seeding so the expanded short-term universe can be created without duplicating existing fund and ETF records.
- [x] 1.4 Add eligibility filters for one-year holding period, closed-period, fixed-open, persistently missing-data, and off-scope products.

## 2. Backend Services

- [x] 2.1 Create a `short_research` service boundary that unifies fund NAV and ETF price data into one short-term asset model.
- [x] 2.2 Implement data status aggregation for latest date, usable history length, stale data, failed assets, and provider/source notes.
- [x] 2.3 Implement short-term metric generation for 5, 10, 20, and 60 trading-day returns, drawdown, volatility, data freshness, and ETF liquidity where available.
- [x] 2.4 Implement deterministic scoring and allowed observation labels: `短线观察`, `高位观察`, `谨慎观察`, `不适合短线`, and `数据不足`.
- [x] 2.5 Implement beginner-readable rationale including trend evidence, risk evidence, investment direction, opposing view, and no-trade-instruction marker.
- [x] 2.6 Implement short-term sample gates at 20, 60, 120, and 250 usable trading days.

## 3. APIs And Jobs

- [x] 3.1 Add `GET /api/short-research/status`.
- [x] 3.2 Add `GET /api/short-research/assets` with asset type, theme, sort mode, and query filters.
- [x] 3.3 Add `GET /api/short-research/assets/{asset_type}/{code}` for chart data and detail explanation.
- [x] 3.4 Add `POST /api/short-research/data/sync` for web-triggered public data preparation.
- [x] 3.5 Add `POST /api/short-research/signals/run` and `GET /api/short-research/signals/latest`.
- [x] 3.6 Wire daily jobs so public data sync and signal generation can run automatically without command-line use.

## 4. Frontend

- [x] 4.1 Add `/short-term` page as the beginner-facing main short-term research experience.
- [x] 4.2 Update navigation so `短线研究` is prominent and `策略实验室` is treated as advanced.
- [x] 4.3 Build top status cards for latest data date, research pool size, observation count, high-risk count, and data exceptions.
- [x] 4.4 Build ranked list with score, observation label, asset type, theme tags, key reason, and risk badges.
- [x] 4.5 Build filters and sort modes for asset type, theme, comprehensive score, recent return, drawdown, liquidity, and risk.
- [x] 4.6 Build asset detail charts for trend, drawdown, recent return windows, and ETF turnover where available.
- [x] 4.7 Build explanation sections for investment direction, why ranked, risk explanation, opposing view, data source, and sample sufficiency.
- [x] 4.8 Add Chinese empty, loading, stale-data, sync-failed, and insufficient-sample states.

## 5. Safety And Compatibility

- [x] 5.1 Preserve existing `/api/short-etf/*`, `/api/strategy-lab/*`, and recommendation compatibility behavior.
- [x] 5.2 Add checks proving short-term outputs do not include buy, sell, stop-loss, take-profit, target price, expected return, or guaranteed-profit fields.
- [x] 5.3 Ensure core ranking and explanation work without any LLM or AI API key.
- [x] 5.4 Clean mojibake in touched short-term-facing Chinese copy without translating API fields, database enum values, or URLs.

## 6. Tests And Verification

- [x] 6.1 Add backend tests for universe seeding, eligibility filters, fund and ETF metric generation, sample gates, scoring labels, and rationale payloads.
- [x] 6.2 Add API tests for status, asset list, asset detail, data sync, signal run, and latest signal endpoints.
- [x] 6.3 Add frontend/type tests or build checks covering `/short-term` rendering, filters, detail selection, empty states, and chart data handling.
- [x] 6.4 Run `uv run pytest`.
- [x] 6.5 Run `uv run ruff check .`.
- [x] 6.6 Run `uv run mypy app`.
- [x] 6.7 Run `corepack pnpm lint`.
- [x] 6.8 Run `corepack pnpm exec tsc --noEmit`.
- [x] 6.9 Run `corepack pnpm build:static`.
