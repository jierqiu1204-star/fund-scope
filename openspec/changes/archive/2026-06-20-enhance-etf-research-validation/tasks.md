## 1. Execution Boundaries And Baseline

- [x] 1.1 Confirm current branch is clean before implementation and record the starting commit.
- [x] 1.2 Main agent assigns parallel coding work to GPT-5.3 Codex subagents with non-overlapping file ownership and explicit no-fallback/no-trading-instruction rules.
- [x] 1.3 Add failing backend tests for label outcome validation, 30% ETF cap, score source gating, dynamic threshold explanation, and AI rule-bound behavior before implementation changes.

## 2. Research Experiment And Label Validation

- [x] 2.1 Add compatible data model or persisted JSON storage for short-term research experiment metadata and label outcome statistics.
- [x] 2.2 Implement deterministic label outcome calculation for observation label + entry timing label combinations across 1/3/5/10 trading-day windows.
- [x] 2.3 Ensure validation results include sample count, data coverage, average return, maximum drawdown, win rate, and insufficient-sample status.
- [x] 2.4 Expose latest experiment metadata and label validation summaries through existing short-research APIs without changing URLs.

## 3. ETF Observation Portfolio Weights

- [x] 3.1 Change single ETF target weight cap to 30%.
- [x] 3.2 Apply deterministic constraints for theme concentration, high correlation, recent volatility, liquidity, and data reliability.
- [x] 3.3 Keep ineligible ETFs as watch-only with explicit reasons and zero target weight.
- [x] 3.4 Add tests proving no ETF exceeds 30%, stale/estimated data receives no weight, and correlated/theme-duplicate ETFs are reduced or excluded.

## 4. Intraday Ranking And Watchlist

- [x] 4.1 Extend live ranking output with daily base score, intraday adjustment score, live total score, score source, and contribution reasons.
- [x] 4.2 Build intraday watchlist from top 20 daily signals, short-watch labels, high-watch labels, and active tracked ETFs with source tags.
- [x] 4.3 Ensure stale, estimated, missing-time, or daily-close data cannot produce intraday timing labels or actionable emails.
- [x] 4.4 Add tests for open-market fresh quote scoring, closed-market daily score display, and fallback data gating.

## 5. Dynamic Exit Line Transparency

- [x] 5.1 Extend tracked-position snapshots with threshold source, rule version, and current distance to hard-stop, take-profit-watch, trailing-giveback, and trend-weakening conditions.
- [x] 5.2 Make profit protection explanation account for volatility, recent drawdown behavior, maximum profit, current profit, and giveback.
- [x] 5.3 Keep ranked observation labels separate from holding signals in API and UI.
- [x] 5.4 Add tests for high-volatility threshold widening, high-profit protection explanation, and high-watch web-only caution without email.

## 6. LLM Conservative Research

- [x] 6.1 Update LLM prompt and validation to require why-ranked, current timing reason, main risks, opposing view, watch conditions, and data limitations.
- [x] 6.2 Ensure AI output cannot change deterministic score, label, timing, portfolio weight, dynamic line, or email eligibility.
- [x] 6.3 Show AI source as AI生成, 规则补齐, or 规则说明 in the frontend.
- [x] 6.4 Add tests for missing opposing view fallback, prohibited trading language rejection, and deterministic value preservation.

## 7. Frontend Short-Term Workbench

- [x] 7.1 Add compact label validation summary and latest experiment timestamp to /short-term.
- [x] 7.2 Show daily base score, intraday adjustment, live total score, and contribution reasons in ETF mode.
- [x] 7.3 Update observation portfolio UI to show 30% cap, watch-only reasons, concentration/correlation reasons, and research-only wording.
- [x] 7.4 Update tracked-position cards and detail sections to show dynamic threshold distances and rule-source explanations without implying automatic trading.

## 8. Review, Testing, And Delivery

- [x] 8.1 Main agent reviews all subagent diffs for scope creep, fallback-data misuse, unsafe financial wording, and redundant code.
- [x] 8.2 Run backend tests covering changed modules plus uv run ruff check . and uv run mypy app.
- [x] 8.3 Run frontend typecheck, lint, and static build.
- [x] 8.4 Manually verify /short-term desktop and mobile states for score explanations, label validation, observation portfolio, and tracked holdings.
- [x] 8.5 Commit with a concise Chinese message only after review and tests pass, then push and deploy if explicitly authorized.
