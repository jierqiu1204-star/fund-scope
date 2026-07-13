# Rollout Evidence

This file records real-environment evidence for tasks 11.2 and 11.9. Raw-price fallback rows are never counted as decision-eligible adjusted-price coverage.

## 2026-07-13 (Asia/Shanghai)

Recorded at `2026-07-13 19:15:08 +08:00`, after the exchange session.

### Adjusted-price publication gate

- Expected eligible/watchlist universe: `83` ETFs.
- Required 95% publication threshold: at least `79` ETFs.
- Last complete trade date (`2026-07-10`): `83/83` rows present, `62/83` decision-eligible `total_return_adjusted` rows (`74.70%`). Gate: **failed**.
- Source split on `2026-07-10`: Eastmoney `62`, efinance `7`, Sina `14`.
- The complete historical synchronization updated `40,005` rows, reported `0` terminal failures, and used raw-price fallbacks for `21` ETFs. Those fallback rows remain `decision_eligible=false`, have no research price basis, and carry `missing_total_return_provenance`.
- A bounded `2026-07-13` close-day synchronization was stopped after `13/83` ETFs because Eastmoney succeeded for only the first `2`; the next `11` consecutive ETFs fell back to Sina. At stop time, `2/83` were decision-eligible, `11/83` were raw-only, and `70/83` were deliberately not retried to avoid an unbounded blocked-source run. No snapshot was published.

Unresolved `2026-07-10` source/basis exclusions:

- Sina raw-only (`14`): `159755`, `159839`, `159870`, `159998`, `510900`, `512170`, `512690`, `513050`, `513550`, `515220`, `515790`, `516970`, `588200`, `588220`.
- efinance raw-only (`7`): `159871`, `159934`, `159941`, `510050`, `511010`, `513060`, `517090`.
- Common exclusion: `research_price_basis=null`, `missing_total_return_provenance`.

### Eastmoney connectivity and rollback

- The production fetcher uses HTTPS, browser-compatible request headers, `invt=2`, separate raw (`fqt=0`) and back-adjusted (`fqt=2`) requests, and adjustment version `eastmoney.push2his.kline.hfq_v1`.
- Clash selector was changed from `新加坡SG-HY2` to `自动选择` only for a single-code probe (`159611`).
- The corrected HTTPS probe failed with `httpx.RemoteProtocolError: Server disconnected without sending a response`.
- The selector was immediately restored to `新加坡SG-HY2`; no proxy setting remained changed.
- Because the source remained blocked/rate-limited, no unbounded synchronization was allowed to continue in this session.

### Provider and ranking health

- Provider health after the bounded attempt: Eastmoney success `51`, Sina success `25`, efinance success `7`; `32` ETF health records retain an Eastmoney primary-error summary.
- The production database contains no `ShortResearchSignalRun` rows and no ETF validation runs. Therefore snapshot age, component availability, cap violations, non-finite rejects, rank churn, and validation exclusions are **unavailable**, not zero.
- With the coverage gate closed and no published snapshot, this exchange session does not count toward the three successful sessions required by task 11.9.

### Safety regression added during recovery

- A raw Sina/efinance fallback can no longer overwrite an existing decision-eligible adjusted row for the same ETF and trade date.
- The regression failed before the fix and passed after it; the full price-provenance test file passes.

### Follow-up recovery at 19:33 +08:00

- Local efinance source inspection showed that `stock.get_quote_history` defaults to `fqt=1` (forward adjusted). The previous adapter incorrectly treated that default output as raw OHLC.
- The adapter now explicitly requests `fqt=0` and `fqt=2`, joins them by trade date, and records `efinance.stock.get_quote_history.fqt2_v1`. Missing or unmatched adjusted values remain decision-ineligible.
- Real single-day probes for `510050` and `159755` on `2026-07-10` returned `total_return_adjusted` rows through the corrected efinance adapter. A later `2026-07-13` probe was disconnected by the proxy; after a 30-second cooldown, a single `2026-07-10` probe succeeded again.
- A low-frequency historical gap sync was then started for only the 21 unresolved ETFs, with zero provider-level retries and a five-second inter-ETF delay. The first three ETFs produced no coverage increase, so the process was terminated. Coverage stayed `62/83` (`74.70%`); the raw-only source split changed to efinance `4` and Sina `17` without promoting any row.
- A final single-code Eastmoney probe with all HTTP(S) proxy environment variables removed still failed with `RemoteProtocolError: Server disconnected without sending a response`. Proxy bypass is therefore not a working recovery path on this host.
- Upstream efinance reports Eastmoney IP-frequency limits and the same `RemoteDisconnected` failure mode: <https://github.com/Micro-sheep/efinance/discussions/216>. Its maintainer also notes that domestic access through a proxy can fail: <https://github.com/Micro-sheep/efinance/issues/159>.
- The production database still has no publishable ranking snapshot, so this follow-up does not count as a successful task 11.9 session.

Result: tasks 11.2 and 11.9 remain open.
