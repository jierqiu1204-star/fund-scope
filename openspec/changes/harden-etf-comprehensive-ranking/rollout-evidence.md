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

### Coverage recovery and full-universe synchronization at 20:14 +08:00

- TickFlow's independent free HTTPS K-line API was verified against `510050.SH` and `159755.SZ` using separate raw (`adjust=none`) and back-adjusted (`adjust=backward`) requests. The adapter records `tickflow.free.klines.backward_v1`; raw rows without matching adjusted values remain decision-ineligible.
- The first bounded recovery request exposed TickFlow's default 100-bar limit. That result was not treated as a full-history synchronization. The adapter now explicitly requests `count=10000`, and the full run was repeated for the complete `83`-ETF universe from `2024-07-15` through `2026-07-13`.
- Full-universe result: `83/83` ETF fetches succeeded, `49` rows were inserted, `40,039` rows were updated, and there were `0` terminal failures.
- Publication coverage is now `83/83` (`100%`) on both the last previously complete session (`2026-07-10`) and the current exchange session (`2026-07-13`). Every counted row is `decision_eligible=true`, uses `total_return_adjusted`, and has TickFlow adjustment/provider provenance. There are no remaining source or price-basis exclusions for the publication barrier.
- Provider health after the full run is TickFlow success `83`, failed `0`. Sina/efinance raw fallback rows were replaced only after verified adjusted values were available; no raw fallback was promoted to decision data.
- TickFlow is now the first historical-price provider, with Eastmoney retained as a later verified-adjustment fallback. This prevents the known blocked Eastmoney endpoint from consuming its full retry budget for every ETF before reaching the healthy independent source.
- Task 11.2 is complete because the full-universe adjusted-price synchronization and the 95% publication barrier both passed with real production data.

### Exchange-session monitoring sample 1 of 3

- A real full-scope signal generation was run for `2026-07-13` after coverage passed. The latest run (`id=2`) completed with `83` items, but remained a `final_score_v2` result with no canonical v3 snapshot identity. Publication failed closed with `SnapshotPublicationError: snapshot trade date is required`; the production database has `0` published snapshots.
- The publication call initially exposed an implicit-transaction lifecycle defect after signal generation. A regression test now requires `run_signal_generation` to return without an open transaction; after the fix, the publication probe reached the intended fail-closed identity check above.
- Snapshot age: unavailable because no canonical v3 snapshot was published.
- Component availability from the real v3 shadow: sector trend `67/83`; premium/discount `0/83`; risk quality `0/83`; structure/liquidity `0/83`; technical momentum `0/83`; theme/catalyst `0/83`. V3 score coverage is `0/83`.
- Cap violations and non-finite rejects: unavailable because no item reached eligible v3 ordering. `caps.applied_count=0` is recorded as a shadow diagnostic and is not interpreted as proof of zero violations.
- Rank churn: unavailable because there are no two published canonical v3 snapshots to compare.
- Validation exclusions: unavailable because the database contains `0` ETF validation runs. Shadow exclusion reasons are retained in run `id=2` and are dominated by unavailable or unreliable premium, structure, catalyst, peer-count, and quality-gate inputs.
- This is the first distinct exchange session with a genuinely passing adjusted-price coverage gate. Task 11.9 remains open until two additional exchange sessions are observed and the required metrics can be evaluated without substituting legacy, simulated, or stale evidence.

Result: task 11.2 is complete; task 11.9 remains open at `1/3` qualifying exchange sessions.

### Same-session v3 producer audit at 20:55 +08:00

- No additional historical synchronization was run. The real adjusted-price gate remains `83/83` (`100%`) for `2026-07-13`, and TickFlow historical provider health remains success `83`, failed `0`.
- The production taxonomy job processed `83` ETFs: `71` classified and `12` unknown/low-confidence. The theme/catalyst job refreshed `4` configured production seeds and produced `4` available snapshots.
- A fresh real signal run (`id=4`) still produced `0/83` eligible v3 scores and no published canonical snapshot. Snapshot age therefore remains unavailable.
- Component availability from run `id=4`: technical momentum `68/83`, sector trend `67/83`, theme/catalyst `7/83`, risk quality `0/83`, structure/liquidity `0/83`, and premium/discount `0/83`.
- Primary real exclusions are missing `overextension_atr` for risk quality; missing `spread_bps` and `structure_quality` for structure/liquidity; missing `premium_discount_bps`, `iopv_observed_at`, and `premium_provider_consensus` for premium/discount; missing broad catalyst evidence for `72` ETFs; and unknown/incompatible taxonomy buckets for `12` ETFs.
- A cross-component peer-count defect had made technical momentum unavailable whenever an unrelated primitive was absent. The corrected derivation restores technical availability without bypassing primitive-specific peer checks. A separate reliability propagation defect dropped verified premium reliability before scoring; that path is now covered by an integration regression.
- AKShare's actual ETF spot columns `IOPV实时估值` and `基金折价率` were not mapped by the normalizer. A failing regression reproduced the loss, and the minimal mapping fix now preserves those fields. This does not establish independent provider consensus and does not make current premium inputs decision-eligible by itself.
- TickFlow's free quote and depth endpoints were checked once and rejected access (`FREE_TIER_RESTRICTED` for quotes and `NO_DEPTH_PERMISSION` for CN depth). They are not used as live structure or premium producers, and no repeated network probing was performed.
- Cap violations and non-finite rejects remain unavailable because no item reached eligible v3 ordering; the shadow diagnostic still records no applied caps and must not be interpreted as proof of zero violations. Rank churn remains unavailable because there are not two published canonical v3 snapshots. Validation exclusions remain unavailable because there are `0` ETF validation runs; the run-level shadow exclusions above are retained separately.
- This is follow-up evidence for the same `2026-07-13` exchange session, not a second session. Task 11.9 remains open at `1/3`; no simulated, stale, raw-price, or legacy evidence was substituted.
