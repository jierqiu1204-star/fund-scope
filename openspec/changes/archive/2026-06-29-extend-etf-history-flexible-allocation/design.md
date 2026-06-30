## Context

FundScope now has a short-term ETF workbench, ETF observation portfolio, tracked-position alerts, and an ETF portfolio backtest that replays the current `/short-term` workflow. The latest server diagnosis showed two separate causes for long cash-wait backtest periods:

- ETF daily history is shallow: daily scheduled jobs refresh recent data only, and older coverage is sparse.
- The allocation rule requires enough qualified candidates to construct a near-full portfolio, which treats "may use 100% ETF capital" like "must be fully invested."

The product intent is different: the user's ETF account capital may be fully used when enough opportunities exist, but the system should not force exposure when only one or two ETFs pass strict gates.

## Goals / Non-Goals

**Goals:**
- Add a reliable way to backfill longer ETF daily history for research and backtests.
- Preserve strict financial data boundaries: only verified or alternate-provider market data can affect weights/backtests.
- Change ETF portfolio allocation from mandatory full exposure to maximum exposure.
- Allow partial allocations when only a few candidates pass gates, with explicit waiting-cash reasons.
- Improve backtest reporting so users can see requested period, available period, warm-up period, first signal date, first trade date, and cash-wait attribution.
- Include implementation completion steps for push and server deployment.

**Non-Goals:**
- Do not connect brokers or execute real trades.
- Do not relax sell/alert rules or email frequency.
- Do not add Redis, a queue system, or microservices.
- Do not fabricate history when the provider cannot return verified daily data.
- Do not change the 30% single ETF cap.

## Decisions

### Decision 1: Reuse `etf_price_history` for long history

Long-history backfill will write verified daily ETF rows into the existing `etf_price_history` table. The sync path should accept a larger date range (`365 / 730 / 1095` days), use the existing upsert behavior, and update ETF data-health summaries.

Alternatives considered:
- Add a new historical table: rejected for now because it duplicates price storage and increases query complexity.
- Use raw intraday snapshots as historical daily data: rejected because close-quality daily history must come from a daily source or verified near-close conversion.

### Decision 2: Keep daily jobs lightweight, add explicit long backfill jobs

Scheduled daily jobs can keep refreshing recent data. Long-history backfill should be manual/admin-triggered and optionally run after deployment. This avoids making every nightly job slow while still allowing robust research history.

Default server rollout should run a `730` day backfill once, then regenerate ETF signals, observation portfolio, and backtest.

### Decision 3: Portfolio exposure is a cap, not a requirement

`total_exposure_cap=1.0` means the optimizer may allocate up to 100% of ETF account capital. It should no longer require at least four selected ETFs to produce any non-cash result.

Target behavior:
- 1 qualified ETF: allocate up to 30%.
- 2 qualified ETFs: allocate up to 60%.
- 3 qualified ETFs: allocate up to 90%.
- 4+ qualified ETFs: allocate up to 100%, subject to weight/correlation/theme constraints.
- Remaining capital is `cash_weight`, labeled as waiting cash with reasons.

If no ETF is qualified, return `portfolio_mode=cash_wait` and `cash_weight=1.0`.

### Decision 4: Backtest must explain cash wait

Backtest output should not show a flat line without context. Each run should include:
- requested start/end,
- effective data start/end,
- warm-up days,
- first signal date,
- first trade date,
- cash-wait days by reason, such as data warm-up, qualified candidate shortage, risk filters, or no reliable price.

This does not change historical returns directly; it makes the result auditable and easier to interpret.

### Decision 5: No fallback relaxation

Longer data availability must not weaken reliability requirements. Stale, estimated, display-only, or missing provider-date rows cannot drive ranking, validation, weights, or email-triggering decisions.

## Risks / Trade-offs

- **Provider cannot return 730+ days for all ETFs** → Store what is verified, report coverage by ETF/date, and show unavailable/warm-up states rather than fake numbers.
- **Long backfill is slow or rate-limited** → Batch requests, reuse existing retry/failure summaries, and keep daily scheduled jobs short.
- **Partial allocation may look like under-investing** → UI must say "最高可满仓，当前只找到这些合格标的，剩余资金等待" instead of implying an error.
- **Backtest results may change after history backfill** → This is expected; store data coverage and run timestamp so old and new runs are comparable.
- **Very few candidates may create concentrated exposure** → Keep single ETF cap at 30% and theme/correlation constraints. Partial allocation is preferred over forced concentration.

## Migration Plan

1. Implement long-history backfill code and admin job entries.
2. Update allocation logic and tests.
3. Update backtest coverage/cash-wait attribution and tests.
4. Update `/short-term` and admin UI text.
5. Run backend and frontend verification.
6. Commit with concise Chinese message.
7. Push to Gitee and GitHub.
8. Deploy to `110.42.222.9`.
9. Run ETF `730` day backfill, regenerate ETF signals, regenerate ETF observation portfolio, and run ETF portfolio backtest.
10. Verify `/short-term`, `/admin/jobs`, and latest backtest output.

Rollback is straightforward: revert the code commit and redeploy. The longer verified `etf_price_history` rows do not need to be deleted; they are useful market data and remain compatible with previous code.

## Open Questions

- Whether to expose `365 / 730 / 1095` buttons in `/admin/jobs` immediately, or only expose one recommended `730 天 ETF 日线回填` button first.
- Whether future backtests should default from `180` to `365` days after history coverage improves.
