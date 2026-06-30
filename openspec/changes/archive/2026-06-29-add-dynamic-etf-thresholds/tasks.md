## 1. Baseline Review And Success Criteria

- [x] 1.1 Review current ETF threshold constants and call sites in short research, tracked positions, and observation portfolio code.
- [x] 1.2 Define success criteria: dynamic thresholds are visible, testable, rule-versioned, and do not use stale or estimated data for email triggers.
- [x] 1.3 Capture representative fixtures for high-volatility ETF, low-volatility ETF, cross-border high-premium ETF, insufficient-history ETF, and active tracked ETF.
- [x] 1.4 Confirm no scope creep into broker connection, automatic trading, or AI-driven threshold decisions.

## 2. Dynamic Threshold Engine

- [x] 2.1 Add a focused dynamic ETF threshold module with no dependency on frontend, notifier, or scheduler code.
- [x] 2.2 Implement volatility unit calculation from ATR-style range, 20-day realized volatility, 60-day median absolute return, and recent drawdown context.
- [x] 2.3 Implement asset-bucket safety bounds so bond, broad-base, industry, cross-border, commodity, and unknown ETFs do not share one unlimited threshold.
- [x] 2.4 Implement own-history return percentile calculation for 1-day, 5-day, 20-day, and 60-day return windows.
- [x] 2.5 Implement optional theme/sector peer percentile context when normalized theme data or existing theme labels are available.
- [x] 2.6 Implement premium/discount state classification with unavailable, mild, high, and extreme premium states.
- [x] 2.7 Return a structured threshold context with rule version, inputs, thresholds, decision eligibility, and human-readable reason.

## 3. Dynamic Threshold Tests

- [x] 3.1 Add tests proving a high-volatility ETF can have a larger normal daily move without automatically becoming `冲高别追`.
- [x] 3.2 Add tests proving a low-volatility ETF can be marked abnormal with a smaller absolute move when its own percentile is extreme.
- [x] 3.3 Add tests proving insufficient history returns conservative default or data-insufficient context.
- [x] 3.4 Add tests proving high and extreme premium downgrade entry timing or block portfolio eligibility.
- [x] 3.5 Add tests proving AI output is not required and cannot alter threshold values.

## 4. Short-Term ETF Label Integration

- [x] 4.1 Replace fixed-only ETF entry-timing thresholds with dynamic threshold context while preserving existing label names.
- [x] 4.2 Make `健康回踩`, `趋势延续`, `冲高别追`, `跌破等待`, and `放量转弱` explanations include dynamic threshold evidence.
- [x] 4.3 Ensure high-premium or unavailable-premium ETFs cannot receive misleading favorable entry timing.
- [x] 4.4 Persist dynamic threshold context into existing signal item metrics/rationale JSON without breaking old response fields.
- [x] 4.5 Add API tests for ETF ranking/detail responses containing dynamic threshold context.

## 5. Tracked Position Exit Integration

- [x] 5.1 Wire tracked ETF hard-stop, take-profit-watch, trailing-take-profit, and trend-weakening calculations to the dynamic threshold engine.
- [x] 5.2 Use holding state inputs: entry price, shares, holding days, current profit, high-water profit, and giveback.
- [x] 5.3 Keep data eligibility strict: stale, display-only, estimated, daily fallback during intraday watch, or missing price cannot trigger email.
- [x] 5.4 Persist threshold mode, rule version, threshold values, current profit, high-water profit, giveback, and price source in alert audit context.
- [x] 5.5 Add tests for high-volatility held ETF, low-volatility held ETF, no-profit state, trailing-giveback trigger, and web-only premium warning.

## 6. Observation Portfolio Integration

- [x] 6.1 Use dynamic overextension and premium context when deciding whether an ETF can receive target weight.
- [x] 6.2 Exclude or mark watch-only ETFs whose move is extreme versus own-history or theme context.
- [x] 6.3 Exclude trustworthy high-premium or extreme-premium ETFs from target weight with readable reason.
- [x] 6.4 Include dynamic threshold summary in weight and exclusion explanations.
- [x] 6.5 Add tests proving dynamic context affects eligibility without overriding data reliability, liquidity, correlation, and single-ETF cap rules.

## 7. Frontend Display

- [x] 7.1 Show dynamic threshold summary in selected ETF detail: volatility unit, current move versus normal band, percentile context, premium state, and rule version.
- [x] 7.2 Keep ETF cards compact; show only one short dynamic reason on cards and put full evidence in detail.
- [x] 7.3 Show tracked-position dynamic lines and distance to thresholds using beginner-readable Chinese.
- [x] 7.4 Show premium risk as structure risk, not as an automatic sell instruction.
- [x] 7.5 Ensure mobile `/short-term` does not overflow and keeps dynamic context readable.

## 8. Label Validation And Replay

- [x] 8.1 Update historical label replay to record dynamic threshold rule version and threshold mode.
- [x] 8.2 Add or update validation output so fixed-rule and dynamic-rule results can be compared where possible.
- [x] 8.3 Ensure insufficient dynamic samples display `样本不足` rather than claiming improved reliability.

## 9. Verification

- [x] 9.1 Run targeted backend tests for dynamic thresholds, short research, tracked positions, and observation portfolio.
- [x] 9.2 Run `uv run ruff check .`.
- [x] 9.3 Run `corepack pnpm exec tsc --noEmit`.
- [x] 9.4 Run `corepack pnpm build:static`.
- [x] 9.5 Manually inspect `/short-term` desktop and mobile for high-volatility, low-volatility, high-premium, and tracked ETF cases.
- [x] 9.6 Verify email reminders still only send for actionable tracked-position signals with eligible data.

## 10. Deployment Readiness

- [x] 10.1 Confirm OpenSpec requirements match implementation and no stale fixed-threshold explanations remain.
- [x] 10.2 Commit with concise Chinese message after verification passes.
- [x] 10.3 Push to Gitee and GitHub if requested.
- [x] 10.4 Deploy to `110.42.222.9` if requested.
- [x] 10.5 After deployment, regenerate ETF signals and inspect several ETFs including volatile theme ETF, low-volatility ETF, and high-premium cross-border ETF.
