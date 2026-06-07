## 1. Scope Audit

- [x] 1.1 Map every remaining `add-fundscope-mvp` task to current code, existing tests, or a missing implementation gap.
- [x] 1.2 Decide whether valuation watchlist add/remove remains in scope; if yes, keep the spec and implement minimal API support.
- [x] 1.3 Identify any remaining MVP task that is external-environment-only and define evidence needed to mark it complete.

## 2. Portfolio Spec Coverage

- [x] 2.1 Add or extend pytest coverage for successful CSV import.
- [x] 2.2 Add or extend pytest coverage for holdings with latest NAV and allocation chart source data.
- [x] 2.3 Add or extend pytest coverage for daily holdings snapshot idempotency.
- [x] 2.4 Confirm all `portfolio-tracking/spec.md` scenarios are covered and mark MVP task 6.9 complete.

## 3. Valuation Spec Coverage and Gaps

- [x] 3.1 Add tests for default index seed data.
- [x] 3.2 Add minimal index watchlist add/remove API support and tests, or explicitly update the MVP spec if fixed watchlist is chosen.
- [x] 3.3 Add index valuation fallback provider boundary and tests for primary failure followed by fallback success.
- [x] 3.4 Add tests for valuation job failure/partial failure logging behavior.
- [x] 3.5 Confirm all `valuation-monitoring/spec.md` scenarios are covered and mark MVP task 7.7 complete.

## 4. Data Ingestion Fallback Tests

- [x] 4.1 Replace or augment the existing fund data tests with recorded/local fixture coverage for primary and fallback parser behavior.
- [x] 4.2 Add index data tests that verify retry/fallback without live network calls.
- [x] 4.3 Mark MVP task 4.4 complete after ingestion fallback tests are deterministic.

## 5. Local Full-Stack Verification

- [x] 5.1 Run or attempt `docker compose` full-stack startup from the documented deployment path.
- [x] 5.2 Verify backend health, nginx proxy, static frontend, and core UI routes when Docker is available.
- [x] 5.3 Record any Docker blocker with exact command output and next action if local startup is unavailable.
- [x] 5.4 Mark MVP task 13.1 complete with evidence.

## 6. Manual MVP Workflow Checks

- [x] 6.1 Verify record-transaction to holdings to value-history flow and mark MVP task 13.2 complete.
- [x] 6.2 Verify `daily_valuation` behavior with fixture or live spot-check evidence and mark MVP task 13.3 complete.
- [x] 6.3 Verify `monthly_dca_reminder` amount bands with controlled percentile fixtures and mark MVP task 13.4 complete.
- [x] 6.4 Verify `daily_news_fetch` summary and raw-fallback paths and mark MVP task 13.5 complete.
- [x] 6.5 Verify recommendation job remains compatible with MVP admin jobs after the release-hardening changes.

## 7. Deployment and Portfolio Evidence

- [x] 7.1 Update deployment docs with required secret names, where to configure them, and how to verify deployment readiness; mark MVP task 12.3 complete when documented.
- [x] 7.2 Add VPS deployment checklist for HTTPS, basic auth, backend health, nginx proxying, scheduler, and backup verification.
- [x] 7.3 Mark MVP task 13.6 complete only if real VPS verification is performed, otherwise record it as external and not locally complete.
- [x] 7.4 Capture or generate portfolio-ready screenshots for `/portfolio`, `/valuation`, `/news`, `/recommendations`, and a sample email; mark MVP task 13.7 complete when stored or documented.

## 8. Final Verification

- [x] 8.1 Run backend tests, ruff, mypy, frontend lint, frontend typecheck, recommendation page check, and static build.
- [x] 8.2 Run `openspec validate finish-fundscope-mvp-verification --strict`.
- [x] 8.3 Run `openspec validate add-fundscope-mvp --strict`.
- [x] 8.4 Ensure `add-fundscope-mvp` is either 81/81 complete or has only explicitly external unresolved tasks.
- [x] 8.5 Commit and push the release-readiness proposal or implementation changes as appropriate.
