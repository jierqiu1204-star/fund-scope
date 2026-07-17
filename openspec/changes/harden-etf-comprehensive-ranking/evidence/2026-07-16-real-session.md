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

## Post-close public-universe expansion repair

At approximately `16:40`-`17:25` Asia/Shanghai, the production universe root cause was repaired and exercised with metadata-only operations. No historical-price synchronization, ranking materialization, publication, alert, or notification was started.

### Root cause and implementation

- Production initially contained only `83` `default_seed` ETF rows and `83` active memberships.
- Live universe discovery depended only on `akshare.fund_etf_spot_em`; the provider remained blocked by `ProxyError -> RemoteDisconnected`.
- The bounded Eastmoney `push2` ETF spot-list request was exposed before quote normalization, so suspended or temporarily unpriced ETFs remain discoverable from code/name metadata.
- Eastmoney discovery is accepted only when declared total, received rows, valid normalized rows, and unique codes agree; AKShare is attempted only when the direct path is incomplete or fails.
- The Eastmoney page/retry chain now has a `20s` total wall-clock timeout.
- Suspicious retention now compares the intersection of old and new codes. An equal-sized but disjoint replacement can no longer pass by count alone.
- Existing ETF metadata is bulk-loaded once before refresh instead of issuing one lookup per discovered code.

### First authoritative metadata-only refresh

- Direct provider result: `1539/1539`, authoritative, source `eastmoney.push2.clist`.
- Refresh result: `1459` inserted, `80` updated, `1389` activated, `70` excluded, `0` failed.
- Price-history rows stayed exactly `40088` before and after.
- Published snapshot count stayed exactly `0` before and after.

The first direct filter omitted Eastmoney board `MK0827`, which caused `511010`, `511260`, and `511380` to be absent and their memberships to be closed. These are still active exchange-traded funds, supported by current Shanghai Stock Exchange records:

- [511010 June 2026 dividend announcement](https://www.sse.com.cn/disclosure/fund/announcement/c/new/2026-06-22/511010_20260622_0EYP.pdf)
- [511260 June 2026 dividend announcement](https://www.sse.com.cn/disclosure/fund/announcement/c/new/2026-06-22/511260_20260622_BWI3.pdf)
- [511380 April 2026 market-maker announcement](https://www.sse.com.cn/disclosure/announcement/general/jjzssgg/c/c_20260410_10814818.shtml)

The direct request filter was corrected to include `b:MK0827`, a regression assertion was added, and only the three false membership closures were rolled back. A later real provider retry failed with `Server disconnected without sending a response`; AKShare remained proxy-blocked, so the failed retry performed no writes and the frozen universe was preserved.

### Final production state

- Stored ETF metadata rows: `1542`.
- Current eligible/watchlist rows: `1472`.
- Current active point-in-time memberships: `1472`.
- Active membership sources: `eastmoney.push2.clist=1389`, `default_seed=83`.
- Latest decision-eligible total-return-adjusted date: `2026-07-13`.
- Adjusted-price coverage against the expanded current universe: `83/1472` (`5.64%`).
- ETF price-history rows: `40088`, unchanged by universe refresh.
- Published canonical snapshots: `0`, unchanged.

This repair restores the full candidate membership path but does **not** pass OpenSpec 11.2 or 11.9. Adjusted history must advance through the existing at-most-20-ETF slices until the real same-date publication barrier passes; no raw Sina/efinance price, simulated evidence, or old snapshot may fill that gap.

## 22:59 post-close read-only recheck

This is a recheck of the same `2026-07-16` exchange session and does not count as an additional OpenSpec 11.9 session. The production database was queried in a read-only transaction. No provider request, history synchronization, materialization, publication, alert, or notification was started.

- Eligible ETF universe and active memberships: `1472/1472`.
- Current-trade-date decision-eligible total-return-adjusted coverage: `0/1472`.
- Latest decision-eligible total-return-adjusted date: `2026-07-13`, with `83/1472` current-universe coverage (`5.64%`).
- ETF price-history rows: `40088`.
- Preserved `14:49:41`-`14:49:47` intraday evidence: `80` immutable rows for `80` codes, all `eastmoney/fresh`.
- Intraday field coverage: volume `80/80`, turnover `80/80`, bid `0/80`, ask `0/80`, IOPV `0/80`, premium/discount `0/80`.
- Provider health remains Eastmoney direct success (`1535`, about `3.5s`) plus AKShare failure (`ProxyError -> RemoteDisconnected`, about `10.5s`); consensus remains single-provider only.
- Published canonical snapshots: `0`; current-date v3 shadow runs: `0`; published snapshot age: unavailable because no canonical snapshot exists.
- Current component availability, cap violations, non-finite rejects, and rank churn are not computable without a current eligible v3 snapshot and were not inferred from legacy runs.
- Latest validation remains failed with `waiting_signal_generation`; all six legacy `2026-07-13` runs remain excluded as `unpublished_snapshot`.

The publication gate therefore still fails. OpenSpec 11.2 and 11.9 remain unchecked, and this date remains a non-passing rollout session.
