## 1. Data Model And Storage

- [x] 1.1 Inspect existing short research signal run/item models and current ETF validation tables before adding storage.
- [x] 1.2 Add or extend label outcome storage for signal item id, asset code, label, entry timing label, rule version, horizon, forward return, adverse drawdown, favorable excursion, status, and exclusion reason.
- [x] 1.3 Add uniqueness constraints so the same signal item and horizon cannot create duplicate outcome rows.
- [x] 1.4 Add migration tests or model tests for the new/extended storage.

## 2. Validation Service

- [x] 2.1 Implement an idempotent ETF label outcome review service that reads stored signal items rather than recomputing historical labels.
- [x] 2.2 Calculate 1/3/5/10 trading-day outcomes using only verified or alternate-provider future daily close data.
- [x] 2.3 Keep incomplete future windows pending and exclude unreliable future data with explicit reasons.
- [x] 2.4 Generate aggregate summaries by label + entry timing label + asset type + optional theme group.
- [x] 2.5 Classify confidence as `样本充足`, `样本有限`, `近期走弱`, or `样本不足`.

## 3. Jobs And APIs

- [x] 3.1 Add a scheduled post-close job that runs ETF label outcome review after ETF daily data and signal generation.
- [x] 3.2 Add admin manual job entry for running label outcome review from the web task page.
- [x] 3.3 Extend short-term detail API with selected ETF label validation summary and recent examples.
- [x] 3.4 Extend ranked list API with compact label-confidence status when available.

## 4. Frontend

- [x] 4.1 Add a compact label-confidence badge to ETF ranked cards without using buy-instruction wording.
- [x] 4.2 Add a detail section explaining historical label outcomes with sample count, 1/3/5/10 day summaries, win rate, median return, worst drawdown, and confidence state.
- [x] 4.3 Add recent examples for the same label combination, including signal date and future outcome.
- [x] 4.4 Add empty and pending states for insufficient samples or incomplete future windows.
- [x] 4.5 Ensure UI text says historical evidence does not guarantee future returns.

## 5. Tests

- [x] 5.1 Test that historical label review uses stored signal context and does not recompute labels from future data.
- [x] 5.2 Test completed horizons produce forward return, drawdown, favorable excursion, and status.
- [x] 5.3 Test incomplete horizons remain pending and do not enter aggregate summaries.
- [x] 5.4 Test fallback, stale, estimated, or display-only price data is excluded from validation.
- [x] 5.5 Test confidence classification for insufficient, limited, sufficient, and recent degradation cases.
- [x] 5.6 Test API response includes validation summary without breaking existing clients.

## 6. Verification

- [x] 6.1 Run backend pytest for short research signal validation and jobs.
- [x] 6.2 Run `uv run ruff check .` in backend.
- [x] 6.3 Run `corepack pnpm exec tsc --noEmit`.
- [x] 6.4 Run `corepack pnpm build:static`.
- [x] 6.5 Manually verify `/short-term` shows historical label evidence and does not present it as a guaranteed buy signal.
