# 2026-07-16 Real Session Evidence

## Session identity

- Exchange trade date: `2026-07-16`
- Quote collection window: `14:49:41`–`14:49:47` Asia/Shanghai
- Collection run: `intraday_etf_watch_runs.id=1`
- Run type: `real_acceptance_20260716_1450`
- Collection attempts for this session: `1`
- Historical synchronization: not run
- Ranking materialization/publication: not run
- Alerts/emails: not run (`0`)

## Pre-collection production database state

- Eligible ETF universe: `83`
- Latest decision-eligible total-return-adjusted trade date: `2026-07-13`
- Coverage on that latest date: `83/83` (`100%`)
- Current-session adjusted-price coverage gate: not passed because the latest adjusted date was not the current trade date
- Existing intraday watch runs for `2026-07-16` before collection: `0`

## Real quote collection result

- Market state: `open / afternoon`
- Watched full eligible universe: `83`
- Persisted immutable quote rows: `80`
- Missing codes: `511010`, `511260`, `511380`
- Provider-returned universe rows: `1535`
- Persisted quote source: `eastmoney`
- Provider consensus: `single_provider=80`, `consistent=0`, `diverged=0`
- Fresh decision-eligible quote rows under the current quote contract: `80`
- Latest price coverage: `80/80`
- Volume coverage: `80/80`
- Turnover coverage: `80/80`
- Bid coverage: `0/80`
- Ask coverage: `0/80`
- IOPV coverage: `0/80`
- Premium/discount coverage: `0/80`

No missing bid/ask, IOPV, premium/discount, provider-consensus, or quote-time field was backfilled or estimated. Single-provider evidence remained explicitly single-provider.

## Provider health and reproducible failure summary

- Eastmoney direct provider: success, `1535` quotes, approximately `3.5s`
- AKShare provider: failed after approximately `10.5s`
- Reproducible AKShare error class: `ProxyError -> RemoteDisconnected`
- Error summary: the AKShare Eastmoney request to `88.push2.eastmoney.com` could not connect through the configured proxy because the remote end closed the connection without a response; the provider entered a 30-second backoff.

The successful Eastmoney direct result was retained as degraded single-provider evidence. It was not represented as cross-provider consensus.

## Rollout decision

This collection is valid real intraday quote evidence for `2026-07-16`, but it is **not** a passing rollout session for OpenSpec 11.2 or 11.9. Current-trade-date total-return-adjusted coverage was not available, and score-bearing premium/structure inputs remained unavailable. No v3 shadow or canonical publication was attempted.
