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
- Because the source remained blocked/rate-limited, no further long synchronization was started in this session.

### Provider and ranking health

- Provider health after the bounded attempt: Eastmoney success `51`, Sina success `25`, efinance success `7`; `32` ETF health records retain an Eastmoney primary-error summary.
- The production database contains no `ShortResearchSignalRun` rows and no ETF validation runs. Therefore snapshot age, component availability, cap violations, non-finite rejects, rank churn, and validation exclusions are **unavailable**, not zero.
- With the coverage gate closed and no published snapshot, this exchange session does not count toward the three successful sessions required by task 11.9.

### Safety regression added during recovery

- A raw Sina/efinance fallback can no longer overwrite an existing decision-eligible adjusted row for the same ETF and trade date.
- The regression failed before the fix and passed after it; the full price-provenance test file passes.

Result: tasks 11.2 and 11.9 remain open.
