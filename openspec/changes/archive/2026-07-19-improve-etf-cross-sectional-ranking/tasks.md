## 1. Scoring Contract

- [x] 1.1 Audit current ETF scoring fields in short research API responses and identify all places that read `total_score`, `score_breakdown_json`, `metrics_json`, and ranking order.
- [x] 1.2 Define `final_score_v2` score breakdown structure with score version, component scores, confidence, and readable reasons.
- [x] 1.3 Keep existing `total_score` and conclusion labels backward compatible while marking old cached runs as old scoring口径.
- [x] 1.4 Add unit tests for response compatibility when old and new score breakdowns coexist.

## 2. Cross-Sectional Ranking

- [x] 2.1 Implement pure percentile helpers for numeric ETF metrics with null-safe and tie-stable behavior.
- [x] 2.2 Compute market-wide percentiles for trend, drawdown, volatility, liquidity, moving-average position, and overextension inputs.
- [x] 2.3 Compute comparable-bucket percentiles by ETF bucket/theme when enough peers exist, and fall back to market-wide percentiles when peer samples are insufficient.
- [x] 2.4 Combine global and comparable-bucket percentiles into `cross_sectional_percentile_score`.
- [x] 2.5 Add tests proving similar fixed raw scores receive deterministic separated ranking when percentile inputs differ.

## 3. Dynamic Threshold Integration

- [x] 3.1 Reuse existing dynamic threshold context without importing tracking, alerts, or notification services.
- [x] 3.2 Convert dynamic threshold outputs into bounded `dynamic_threshold_score` adjustments and explicit reasons.
- [x] 3.3 Ensure high-volatility, bond-like, cross-border, and theme-specific ETFs use their own threshold context.
- [x] 3.4 Add tests for high-volatility ETF, low-volatility ETF, and cross-border ETF threshold differences.

## 4. Label Evidence Integration (Superseded By One-Way Evidence Boundary)

- [x] 4.1 Preserve current ETF label validation evidence as a display-only query; the former score-input behavior is superseded.
- [x] 4.2 Supersede the former positive adjustment: sufficient evidence leaves all current decision outputs unchanged.
- [x] 4.3 Supersede the former penalty: weakening evidence is displayed and leaves all current decision outputs unchanged.
- [x] 4.4 Record evidence sample count, horizon, confidence, and limitation reason in score breakdown.
- [x] 4.5 Treat former boost/penalty tests as legacy v2 coverage; corrected v3 behavior requires evidence changes to be no-op for all current decision outputs.

## 5. Data Reliability And Liquidity/Premium Penalties

- [x] 5.1 Gate ranking inputs through market data reliability vocabulary: `verified`, `alternate_provider`, `estimated`, `stale`, and `unavailable`.
- [x] 5.2 Ensure non-decision-eligible data cannot improve final score, label confidence, portfolio eligibility, or realtime buy-point status.
- [x] 5.3 Add liquidity quality score using recent average turnover, turnover stability, and low-liquidity flags.
- [x] 5.4 Add premium/discount penalty with explicit handling for abnormal premium, missing premium data, and provider disagreement.
- [x] 5.5 Add tests proving stale quote, inferred quote time, missing premium, and low liquidity are penalized or marked display-only.

## 6. Ranking Pipeline Integration

- [x] 6.1 Integrate `final_score_v2` into ETF daily signal generation without changing API URLs or task names.
- [x] 6.2 Integrate compatible scoring into ETF realtime ranking while preserving daily versus intraday score source separation.
- [x] 6.3 Ensure page requests still read cached signal items and do not recompute the full universe on every request.
- [x] 6.4 Ensure observation portfolio uses the new ranking score only after a new score version cache has been generated.
- [x] 6.5 Add performance checks or tests to confirm full ETF ranking remains batch-oriented.

## 7. Frontend Display

- [x] 7.1 Extend frontend types for score version, score source, component scores, confidence, evidence summary, and limitation reasons.
- [x] 7.2 Update `/short-term` ranked cards to show concise final score source and key penalty reason without adding long text to the list.
- [x] 7.3 Update selected ETF detail to show the five score dimensions: 横截面分位、动态阈值、历史有效性、数据可信度、流动性/折溢价.
- [x] 7.4 Show old cached rankings as `旧口径结果` until a new score version is generated.
- [x] 7.5 Ensure UI states do not imply buy/sell instructions and do not hide data reliability limitations.

## 8. Validation

- [x] 8.1 Run backend ranking and reliability tests covering the new score components.
- [x] 8.2 Run `uv run pytest tests/test_backend_domain_boundaries.py`.
- [x] 8.3 Run `uv run ruff check .`.
- [x] 8.4 Run frontend type check with `corepack pnpm exec tsc --noEmit`.
- [x] 8.5 Do not run local static build as the primary validation path; use server Docker build for static build verification according to local deployment troubleshooting docs.

## 9. Deployment And Data Refresh

- [ ] 9.1 Deploy backend and frontend after tests pass. **PAUSED / BLOCKED:** resume only when a pinned, published, full-scope, fresh `final_score_v3` snapshot exists with exact contract, universe, and input identity plus a compatible price basis, and deployed consumers preserve that identity without fallback.
- [ ] 9.2 Run ETF data refresh if needed. Retained only to prepare traceable inputs under the v3 price-basis, trade-date, universe, and coverage contract; it MUST NOT publish, refresh, or promote a `final_score_v2` or legacy canonical ranking.
- [ ] 9.3 Run ETF signal generation to create `final_score_v2` cached results. **PAUSED / BLOCKED:** this v2 production generation MUST NOT run or feed current consumers; resume only as v3 generation from a pinned, published, full-scope, fresh `final_score_v3` contract with exact contract, universe, and input identity plus a compatible price basis, consumed without fallback.
- [ ] 9.4 Run ETF observation portfolio generation after the new ranking cache exists. **PAUSED / BLOCKED:** resume only when allocation consumes and preserves a pinned, published, full-scope, fresh `final_score_v3` snapshot with exact contract, universe, and input identity plus a compatible price basis, without fallback.
- [ ] 9.5 Verify `/short-term` shows new score breakdown, score version, data reliability limits, and non-misleading old-cache handling. **PAUSED / BLOCKED:** production verification resumes only when `/short-term` consumes and preserves a pinned, published, full-scope, fresh `final_score_v3` snapshot with exact contract, universe, and input identity plus a compatible price basis, without v2/legacy fallback.
