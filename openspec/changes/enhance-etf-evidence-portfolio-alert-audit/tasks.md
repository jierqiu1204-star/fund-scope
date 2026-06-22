## 1. Boundaries And Existing Flow Review

- [x] 1.1 Review current short-term ETF ranking, observation portfolio, intraday watch, tracked-position alert, and notification services before editing.
- [x] 1.2 Document current API fields that must remain backward compatible for `/short-term`, tracked positions, observation portfolio, and notification logs.
- [x] 1.3 Confirm no changes will be made to login, SMTP setup, broker integration, automatic trading, or tracked-position creation behavior.

## 2. Backend Label Evidence Enhancements

- [x] 2.1 Extend existing ETF label evidence summaries with sample count, confidence, freshness, exclusion reasons, and recent degradation indicators.
- [x] 2.2 Ensure evidence calculations only use decision-eligible ETF price data and exclude stale, estimated, display-only, or unavailable data.
- [x] 2.3 Add API fields for label evidence summaries without removing or renaming existing ranking fields.
- [x] 2.4 Add tests for sufficient evidence, insufficient evidence, stale data exclusion, and recent degradation warnings.

## 3. Backend Observation Portfolio Explanations

- [x] 3.1 Extend observation portfolio output with per-included-ETF weight explanations using score, validation confidence, volatility, drawdown, liquidity, correlation, and theme concentration.
- [x] 3.2 Extend observation portfolio output with per-excluded-candidate reasons.
- [x] 3.3 Preserve the existing 30% single ETF max weight cap and decision-ineligible exclusion rule.
- [x] 3.4 Add tests for included explanation fields, excluded reasons, 30% cap preservation, and no-weight assignment for decision-ineligible assets.

## 4. Backend Alert Audit Trail

- [x] 4.1 Add alert audit persistence using a backward-compatible table or existing structured JSON where sufficient.
- [x] 4.2 Record sent, failed, skipped, suppressed, web-only, and data-ineligible outcomes for tracked-position evaluations.
- [x] 4.3 Record data source, quote freshness, signal type, threshold context, duplicate/cooldown reason, SMTP result, and linked tracked-position alert where available.
- [x] 4.4 Add owner-scoped read endpoints for alert audit history by tracked position.
- [x] 4.5 Ensure audit recording observes the existing alert decision path and does not recompute or override email-send decisions.
- [x] 4.6 Add tests for sent audit, web-only audit, duplicate suppression audit, data-ineligible audit, and cross-user access denial.

## 5. Intraday Watch Audit Output

- [x] 5.1 Extend intraday watch job result details with watchlist source reasons for each watched ETF.
- [x] 5.2 Expose quote freshness and decision eligibility in watch results without changing the current trading-session schedule.
- [x] 5.3 Avoid writing one unchanged audit row per minute when no state changed; record meaningful transitions and send/suppression events.
- [x] 5.4 Add tests proving stale or missing quote time is display-only and cannot trigger email decisions.

## 6. Frontend Evidence, Portfolio, And Audit UI

- [x] 6.1 Update `/short-term` ETF cards and detail panel to show compact label evidence confidence and sample quality.
- [x] 6.2 Update observation portfolio UI to explain why each ETF got its weight and why excluded candidates were removed.
- [x] 6.3 Update `我的持仓` to show an alert audit timeline with sent, skipped, suppressed, web-only, and data-ineligible states.
- [x] 6.4 Keep beginner-facing Chinese copy short first, with detailed fields behind expandable sections.
- [x] 6.5 Verify mobile and PC layouts do not introduce horizontal overflow or long unreadable blocks.

## 7. AI Explanation Guardrails

- [x] 7.1 Update AI prompts so AI can summarize evidence, weights, and audit reasons but cannot alter labels, weights, thresholds, or send decisions.
- [x] 7.2 Ensure fallback/rule explanations are labeled as rule explanations, not full AI conclusions.
- [x] 7.3 Add tests or fixtures ensuring prohibited buy/sell guarantee language is filtered or rejected.

## 8. Subagent Execution Boundaries

- [x] 8.1 Assign backend evidence/API work to one 5.3 codex subagent with scope limited to label evidence and portfolio explanation fields.
- [x] 8.2 Assign backend alert audit work to one 5.3 codex subagent with scope limited to audit persistence, read endpoints, and tests.
- [x] 8.3 Assign frontend display work to one 5.3 codex subagent with scope limited to `/short-term` and tracked-holding audit display.
- [x] 8.4 Review all subagent changes centrally before merging, removing duplicated logic, dead imports, broad refactors, and any fallback data misuse.

## 9. Verification

- [x] 9.1 Run targeted backend tests for label evidence, portfolio explanations, alert audit, intraday watch freshness, and investment reminders.
- [x] 9.2 Run `uv run ruff check .`.
- [x] 9.3 Run `uv run mypy app`.
- [x] 9.4 Run `corepack pnpm exec tsc --noEmit`.
- [x] 9.5 Run `corepack pnpm build:static`.
- [x] 9.6 Run `openspec validate enhance-etf-evidence-portfolio-alert-audit --strict`.
- [ ] 9.7 Deploy to `110.42.222.9` only after tests pass and verify existing email, intraday watch, and `/short-term` flows still work.
