## 1. Baseline And Test Fixtures

- [x] 1.1 Add deterministic fixtures for one low-volatility ETF, one high-volatility ETF, one場外基金 with daily NAV, and one strong-uptrend asset with 8%-9% historical drawdown.
- [x] 1.2 Add failing tests that document the current gap: ranking label stays high while tracked-position handling signal must be separate and explainable.
- [x] 1.3 Add tests for web-only warnings so stale quote, missing IOPV, premium/discount, liquidity warning, and high-position watch do not send sell-or-reduce email.

## 2. Backend Exit Strategy Engine

- [x] 2.1 Delegate backend implementation to `gpt-5.3-codex-spark` xhigh with write scope limited to tracked positions, short research, intraday ETF services, schemas, migrations if required, and backend tests.
- [x] 2.2 Refactor tracked-position exit evaluation into a clear strategy helper that returns asset type, price source, thresholds, primary signal, web-only warnings, and Chinese reasons.
- [x] 2.3 Keep ETF strategy intraday-first and volatility-adjusted using recent realized volatility or ATR-like range, with bounded hard-stop, profit-start, and trailing-giveback thresholds.
- [x] 2.4 Add fund strategy using confirmed NAV/share data and daily NAV volatility/drawdown to produce conservative dynamic thresholds without intraday decisions.
- [x] 2.5 Preserve email whitelist: only `hard_stop`, `trailing_take_profit`, `trend_weakening`, and `exit_watch` can send email; all other warnings are recorded as skipped/web-only.
- [x] 2.6 Ensure duplicate intraday ETF alerts remain suppressed within cooldown, with hard-stop escalation only when loss materially worsens.

## 3. Unified Short-Term Ranking Source

- [x] 3.1 Make `/short-term` and tracked-position alert context use `/api/short-research` signal data as the canonical ETF ranking and explanation source.
- [x] 3.2 Keep legacy `/api/short-etf` endpoints compatible, but avoid exposing a conflicting scoring explanation as the primary workbench result.
- [x] 3.3 Add tests proving ETF ranking labels and tracked-position handling signals can differ without being treated as contradictory.

## 4. Frontend Explanation And Tracking UI

- [x] 4.1 Delegate frontend implementation to `gpt-5.3-codex-spark` xhigh with write scope limited to `/short-term` page, frontend types/helpers, and frontend tests.
- [x] 4.2 Update asset detail copy to show “买入观察状态” separately from “持仓处理状态”.
- [x] 4.3 For selected assets, show trend strength, recent returns, 60-day drawdown, volatility, data source, current tracked position state, dynamic threshold values, and latest reason.
- [x] 4.4 For tracked holdings, show whether a warning is `已发邮件`, `仅网页提示`, `已去重`, or `等待数据`, and never label data-only warnings as卖出/减仓提醒.
- [x] 4.5 Keep mobile tabs concise; the new threshold/reason detail belongs in the detail or tracking tab, not in every ranked card.

## 5. Verification And Deployment

- [x] 5.1 Run backend tests for tracked-position exit signals, intraday ETF watch, short research ranking, and legacy endpoint compatibility.
- [x] 5.2 Run frontend typecheck, lint, and static build.
- [x] 5.3 Review subagent patches for scope, duplicated logic, mojibake-facing text, and old ETF scoring usage.
- [x] 5.4 Deploy to `110.42.222.9`, run migrations if present, and verify `/short-term`, `/api/tracked-positions`, `/api/etf-quotes/tracked`, and admin jobs on the server.
- [x] 5.5 Push with a concise Chinese commit message after verification; do not archive the change until server verification passes.
