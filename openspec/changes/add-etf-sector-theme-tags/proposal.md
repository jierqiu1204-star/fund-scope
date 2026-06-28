## Why

FundScope already ranks a broad ETF universe, but users can only filter with coarse directions. For short-term ETF research, industry/theme grouping such as semiconductor, AI, healthcare, securities, gold, bond, dividend, and cross-border is a standard workflow because it helps compare similar products, avoid repeated exposure, and understand which parts of the market are active.

This change adds a deterministic ETF sector/theme taxonomy, frontend filters, theme heat statistics, and portfolio concentration constraints so ETF ranking and allocation are easier to inspect without turning labels into buy instructions.

## What Changes

- Add a normalized ETF industry/theme tag taxonomy for the full tradable ETF universe.
- Enrich ETF metadata with primary theme, secondary themes, theme group, broad asset bucket, and matching evidence.
- Add `/short-term` ETF filters under `全部方向` for common themes such as 半导体、人工智能、生物医药、证券、银行、红利、黄金、债券、纳指、恒生科技等.
- Add theme heat statistics for ETF mode: count, average intraday/daily change, top score, average score, active candidates, and data freshness.
- Update ETF ranking/detail UI to show theme tags as research dimensions, not trading instructions.
- Extend observation portfolio logic to enforce theme concentration limits using the normalized theme group rather than only raw text labels.
- Add admin/data-task visibility for taxonomy refresh and theme coverage quality.
- Add a coordinator-led implementation workflow with parallel agents for research, backend, frontend, and validation, with explicit review checkpoints.
- No breaking API changes. Existing fields remain compatible; new fields are additive.

## Capabilities

### New Capabilities
- `etf-sector-theme-taxonomy`: Defines ETF industry/theme classification, coverage quality, theme heat statistics, and refresh behavior.

### Modified Capabilities
- `short-term-research`: Adds ETF theme filters, theme heat display, and selected-asset theme evidence on `/short-term`.
- `short-etf-research`: Requires ETF ranking metadata to include normalized theme fields and theme coverage status.
- `etf-observation-portfolio-optimization`: Uses normalized theme groups for concentration limits, exclusion reasons, and weight explanations.

## Impact

- Backend:
  - ETF metadata refresh and classification services.
  - Short research API serialization for ETF list/detail/status.
  - Observation portfolio concentration logic and explanations.
  - Admin jobs/data status summaries.
- Frontend:
  - `/short-term` ETF filter controls, theme heat panel, ETF cards, detail summary, and portfolio explanation.
  - No new dependency required.
- Database:
  - May add additive columns or a small mapping table for normalized ETF themes and classification evidence.
  - Existing ETF, signal, portfolio, and tracking tables remain compatible.
- Operations:
  - Theme taxonomy refresh must be idempotent and runnable from the web task page.
  - Existing ranking, tracking, email reminders, and real trading boundaries remain unchanged.
