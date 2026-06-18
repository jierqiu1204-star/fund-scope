## 1. Tests First

- [x] 1.1 Add failing tests that verify LLM advisor prompts, fallback reports, prohibited terms, and validation errors are readable Chinese without mojibake.
- [x] 1.2 Add failing tests that verify `take_profit_watch` can create a soft email alert when current profit reaches the dynamic watch threshold.
- [x] 1.3 Add failing tests that verify data-only ETF warnings such as missing IOPV remain web-only and do not send email.
- [x] 1.4 Add failing tests that verify tracked positions still trigger hard stop, trailing take-profit, trend weakening, and take-profit watch when no latest signal item exists.

## 2. Backend Implementation

- [x] 2.1 Fix corrupted Chinese text in the short research advisor prompt, fallback report, action labels, risk labels, prohibited terms, and LLM retry message.
- [x] 2.2 Keep dynamic exit thresholds rule-based and expose clear metadata that AI did not calculate or override the lines.
- [x] 2.3 Add `take_profit_watch` to email-capable alert types with subject/body wording for `止盈观察提醒`.
- [x] 2.4 Preserve existing cooldown and duplicate suppression so the same tracked position does not repeatedly send take-profit-watch emails.
- [x] 2.5 Refactor tracked-position alert creation so technical holding signals can be evaluated without a latest short-term signal item.
- [x] 2.6 Ensure `exit_watch` still requires ranking context and is not created solely because an asset is absent from the latest ranking.
- [x] 2.7 Keep stale quote, missing IOPV, wide spread, premium/discount, and liquidity warnings as `risk_warning` with skipped email status unless a holding threshold is breached.

## 3. Frontend Implementation

- [x] 3.1 Update `/short-term` asset detail and tracked holding cards to show whether explanation source is `AI生成` or `规则兜底`.
- [x] 3.2 Add concise Chinese copy explaining that dynamic lines are calculated by rules from market data and AI only explains evidence and risk.
- [x] 3.3 Show dynamic hard-stop line, take-profit-watch line, trailing-giveback line, latest holding signal, and email/web-only status for tracked holdings.
- [x] 3.4 Render `take_profit_watch` as `止盈观察提醒` with soft wording and without automatic or guaranteed sell language.

## 4. Verification

- [x] 4.1 Run `uv run pytest tests/test_short_research_advisor.py tests/test_tracked_positions.py tests/test_intraday_etf_watch.py`.
- [x] 4.2 Run `uv run ruff check .`.
- [x] 4.3 Run `corepack pnpm exec tsc --noEmit`.
- [x] 4.4 Run `corepack pnpm build:static`.
- [x] 4.5 Manually verify `/short-term` shows readable Chinese AI/rule explanations and distinguishes soft profit-watch emails from sell/reduce alerts.
