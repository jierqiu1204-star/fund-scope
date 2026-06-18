## 1. Tests First

- [x] 1.1 Add backend tests proving `take_profit_watch` cooldown ignores prior `web_only`, `skipped`, `suppressed`, and failed records, but blocks on prior `sent` records.
- [x] 1.2 Add backend tests proving intraday duplicate suppression does not insert a new suppressed alert row on every repeated poll.
- [x] 1.3 Add backend tests proving intraday ETF emails require fresh intraday quotes and are skipped for `daily_close`, stale quote, missing IOPV-only, or fallback price contexts.
- [x] 1.4 Add advisor tests proving prompt safety text exists, neutral rule terms are accepted, direct trading commands are rejected, and partial rule completion is labeled.

## 2. Market Data Reliability

- [x] 2.1 Add or consolidate backend helpers that classify tracked-position price data as fresh intraday, daily close, stale quote, missing, fallback provider, or AI/rule fallback where applicable.
- [x] 2.2 Return reliability metadata in tracked-position and ETF quote responses without changing existing field names that the frontend already uses.
- [x] 2.3 Ensure daily close ETF snapshots remain visible for estimation but are marked as non-intraday data.
- [x] 2.4 Ensure missing or unusable price data returns a waiting/data-quality status and cannot create actionable alerts.

## 3. Alert Eligibility And Deduplication

- [x] 3.1 Gate intraday `hard_stop`, `trailing_take_profit`, `trend_weakening`, and `take_profit_watch` email eligibility on fresh intraday quote data.
- [x] 3.2 Keep daily close evaluations eligible only in the daily review path, with email/body text clearly saying it is based on daily close or NAV.
- [x] 3.3 Change `take_profit_watch` cooldown queries to count only records with `email_status=sent`.
- [x] 3.4 Change intraday duplicate suppression so ordinary repeated alerts update job statistics or reuse a prior suppression state instead of inserting a suppressed row every minute.
- [x] 3.5 Preserve hard-stop escalation when loss materially worsens after an earlier reminder.

## 4. AI Advisor Safety

- [x] 4.1 Strengthen `short_research_advisor` prompt with safe observation phrasing and explicit forbidden trading-instruction examples.
- [x] 4.2 Refine prohibited-language validation so neutral system rule names are allowed when used as labels or explanations, while direct buy/sell/target/guarantee claims remain rejected.
- [x] 4.3 Persist and return report source status for AI-generated, partially rule-completed, and full rule-fallback reports.
- [x] 4.4 Keep AI output unable to modify ranking, deterministic labels, dynamic lines, or email-triggering rules.

## 5. Frontend And Copy

- [x] 5.1 Update `/short-term` tracked-position cards to show price source, freshness, and whether a reminder is email-eligible or web-only.
- [x] 5.2 Update advisor report display to label `AI生成`, `AI生成，部分规则补齐`, or `规则兜底`.
- [x] 5.3 Update Chinese copy so fallback/daily-close/stale data is never presented as real-time trading data.

## 6. Verification

- [x] 6.1 Run focused backend tests for tracked positions, intraday ETF watch, and advisor safety.
- [x] 6.2 Run `uv run ruff check .`.
- [x] 6.3 Run `corepack pnpm exec tsc --noEmit`.
- [x] 6.4 Run `corepack pnpm build:static`.
- [x] 6.5 Verify `/short-term` static build includes fresh-data eligibility, fallback/stale web-only copy, and AI source labels.
