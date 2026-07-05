## 1. Backend Tests First

- [x] 1.1 Add unit tests for sector trend scoring with broad peer participation, weak breadth, insufficient peers, stale peers, and unclassified ETFs.
- [x] 1.2 Add tests for opportunity score weighting: full evidence, technical plus sector trend, technical plus catalyst, and no non-technical evidence.
- [x] 1.3 Add tests proving strong sector trend does not remove `冲高别追`, stale-data, insufficient-history, or liquidity risk labels.
- [x] 1.4 Add tests for robotics and innovative drug theme classification from ETF name, theme tags, investment direction, and metadata.
- [x] 1.5 Add API tests proving ETF detail scores equal the latest cached signal item and missing scores serialize as `null`.
- [x] 1.6 Add API tests proving `sort=opportunity` sorts numeric comprehensive scores before unavailable scores.

## 2. Sector Trend Scoring

- [x] 2.1 Create `app.services.short_research.sector_trends` with pure functions for peer grouping, coverage checks, trend components, labels, summaries, and unavailable reasons.
- [x] 2.2 Compute theme-level sector trend from existing signal-run metrics: 5-day and 20-day returns, MA5/MA20 position, turnover expansion, peer technical score, and data reliability.
- [x] 2.3 Return `sector_trend_score = null` when peer coverage or data quality is insufficient; do not expose neutral fallback scores.
- [x] 2.4 Add score breakdown fields for sector trend components and score version.

## 3. Theme Coverage

- [x] 3.1 Split robotics classification from broad artificial-intelligence matching so `机器人` can be a primary/searchable theme.
- [x] 3.2 Split innovative drug classification from broad healthcare matching so `创新药` can be a primary/searchable theme.
- [x] 3.3 Add or refresh default ETF candidates by matching stored ETF metadata for robotics and innovative-drug keywords, including server-known robotics ETFs when present.
- [x] 3.4 Keep innovative drug catalyst fields unavailable until verified catalyst events exist; do not seed fake catalyst or event heat.

## 4. Signal Integration

- [x] 4.1 Insert sector trend enrichment after ETF technical/final-score computation and before opportunity-score synthesis.
- [x] 4.2 Update opportunity score builder to accept sector trend evidence and select the correct weighting version.
- [x] 4.3 Persist `sector_trend_score`, `sector_trend_label`, `sector_trend_summary`, `sector_peer_count`, `sector_trend_status`, `opportunity_score_version`, and `opportunity_breakdown` into signal item JSON.
- [x] 4.4 Ensure ETF detail continues to read scores from the latest cached signal run instead of recomputing singleton normalized scores.
- [x] 4.5 Update `sort=opportunity` so unavailable comprehensive scores remain behind numeric scores.

## 5. API And Frontend

- [x] 5.1 Extend backend schema and `_asset_out` output for sector trend fields and opportunity score version.
- [x] 5.2 Ensure unavailable sector trend, catalyst, heat, and comprehensive scores serialize as `null` with readable Chinese labels or reasons.
- [x] 5.3 Update `/short-term` list cards to show board trend score, peer count, and status only when real data exists.
- [x] 5.4 Update ETF detail panel to show board trend summary, score breakdown, weighting version, and unavailable states.
- [x] 5.5 Update theme filter options so `机器人` and `创新药` are visible when matching ETFs exist.

## 6. Verification

- [x] 6.1 Run focused backend tests for short research API, theme taxonomy, sector trend scoring, and opportunity sorting.
- [x] 6.2 Run `uv run pytest tests/test_backend_domain_boundaries.py`.
- [x] 6.3 Run `uv run ruff check .`.
- [x] 6.4 Run frontend typecheck for `/short-term` changes.
- [ ] 6.5 Manually verify ETF detail and ranking consistency for robotics, innovative drug, semiconductor, optical-module proxy, and no-theme ETFs.
- [ ] 6.6 After deployment, run the ETF signal generation job and confirm production API shows no fallback `50/60` comprehensive scores.
