## 1. Provider And Normalization

- [x] 1.1 Refactor ETF spot fetching into provider adapters that return the existing normalized quote shape plus provider id and provider error metadata.
- [x] 1.2 Keep current AKShare ETF provider as one adapter with existing backoff behavior.
- [x] 1.3 Add one direct public backup provider for ETF spot quotes, preferring Eastmoney direct API; if required fields are insufficient, use Sina or Tencent as the backup provider.
- [x] 1.4 Add short per-provider timeout and failure isolation so one slow provider cannot block the whole intraday watch job.

## 2. Cross-Validation And Storage

- [x] 2.1 Implement quote consensus selection using quote freshness, non-fallback quote time, field completeness, and price-difference tolerance.
- [x] 2.2 Mark consensus status as `consistent`, `single_provider`, `diverged`, `stale`, or `unavailable`.
- [x] 2.3 Persist the selected primary quote through the existing `etf_intraday_quotes` row and store provider validation details in `raw_json` without adding a database migration.
- [x] 2.4 Ensure diverged, stale, fallback-time, estimated, or unavailable quotes are display-only and not decision-eligible.

## 3. API And Decision Gates

- [x] 3.1 Extend ETF quote, live ranking, watch status, and tracked ETF snapshot responses with selected source, consensus status, provider count, price-difference summary, decision eligibility, and readable limitation reason.
- [x] 3.2 Update realtime ranking and intraday buy-point calculation to use only decision-eligible cross-validated quotes.
- [x] 3.3 Update tracked ETF alert evaluation so actionable emails require fresh decision-eligible cross-validated quotes.
- [x] 3.4 Add provider health and consensus summary counts to intraday watch job details.

## 4. Frontend Display

- [x] 4.1 Add a compact `/short-term` quote reliability line showing `行情来源`, `校验状态`, and limitation reason when present.
- [x] 4.2 Keep existing price,涨跌,行情时间,追踪盈亏 layout unchanged except for the small reliability note.
- [x] 4.3 Ensure source disagreement or display-only quotes are shown as网页参考, not realtime decision-ready data.

## 5. Tests And Verification

- [x] 5.1 Add backend tests for consistent providers, single eligible provider, diverged providers, missing quote time, and all providers failing.
- [x] 5.2 Add tests proving diverged/display-only quotes cannot drive live ranking adjustments or tracked ETF emails.
- [x] 5.3 Run `uv run pytest tests/test_intraday_etf_watch.py tests/test_tracked_positions.py`.
- [x] 5.4 Run `uv run ruff check .`.
- [x] 5.5 Run `corepack pnpm exec tsc --noEmit` and `corepack pnpm build:static`.

## 6. Server Acceptance

- [x] 6.1 Deploy to `110.42.222.9` after tests pass.
- [ ] 6.2 During trading hours, confirm intraday watch job records provider health and consensus counts.
- [x] 6.3 Compare one tracked ETF and one top-ranked ETF against the API response to verify UI price source and consensus status are visible.
- [x] 6.4 Confirm provider disagreement or stale quote status does not send emails and is visible only as a data-quality note.
