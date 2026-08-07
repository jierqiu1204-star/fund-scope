# V2 implementation verification (2026-08-04)

## Completed in this pass

- Added an isolated V2 source/formula/lifecycle contract with exactly three
  frozen candidates and transparent-proxy wording.
- Added additive V2 and A-share PIT tables, append-only manifest/candidate/
  transition/source-label/holdout/checkpoint storage, and a rollback-safe
  downgrade path for the V2 migration itself.
- Added fail-closed A-share adjusted-price and PIT membership adapters. Raw or
  audit-only providers, missing receipt time, post-cutoff facts, non-finite
  values, and non-adjusted price bases do not increase decision coverage.
- Added a serial bounded collector primitive with deterministic ordering,
  adaptive 5–20 batches, a hard maximum 55-second continuation budget, and
  durable success/failure checkpoint payloads. It is not enabled for
  production capture yet.
- Replaced the blocking all-market BaoStock industry request with a primary
  TickFlow SW1 batch plus resumable BaoStock pages of at most 20 missing
  symbols. TickFlow adjusted bars are now admitted by the A-share read and
  readiness contracts; no ETF ranking threshold or publication input changed.
- Added a stable unavailable-reason registry with exact numerator/denominator
  availability gates on the summary surface, and wired replay/promotion failures
  through that contract.
- Kept API, capture, materialization, navigation, and direct-page access behind
  separate deployment flags that all default to disabled. A disabled direct URL
  renders an explicit research-only message without mounting the query panel.
- Added the pure dual-universe screen, causal lifecycle reducer, common-support
  five-session net-excess calculation, source-label policy, one-time holdout
  guard, locked-case evaluator, read-only candidate and summary APIs,
  research-only UI, and production-boundary guard.
- Added an ETF primary-evidence bridge that delegates T+1 adjusted-close
  outcomes, costs, walk-forward folds, and block bootstrap to existing
  Strategy Lab components; added separate source-label/economic diagnostics;
  fixed the signed maximum-drawdown promotion check to compare absolute
  drawdown degradation.

## Evidence

- V2 backend, API, migration, lifecycle, PIT, validation, and frontend-contract
  group: 144 passed in 15.63 seconds under a 55-second hard timeout after the
  bounded production provider and scheduler wiring was added.
- Backend-domain-boundary tests: 10 passed in 0.03 seconds. Changed-file Ruff,
  `git diff --check`, and strict OpenSpec validation passed on 2026-08-05.
- Migration and backend-domain-boundary group on the current remote baseline:
  48 passed. V2 now extends the existing migration chain as
  `20260804_000062 -> 20260802_000061`; isolated upgrade/downgrade and same-day
  universe revision preservation passed.
- Changed-file Ruff checks passed for the production-provider/scheduler Python
  files touched in this pass. Earlier changed-file Ruff and format checks passed
  for the broader 55-file implementation set. The
  repository-wide Ruff command still reports one pre-existing import-order issue
  in `etf_point_in_time_research_loop.py`, which was not modified by this change.
- Frontend TypeScript `tsc --noEmit` passed. Five executable interaction-contract
  tests passed for universe isolation, filters, cursor pagination, provenance,
  research-only/no-fallback enforcement, stale responses, and cancellation; the
  source contract additionally verifies the default-disabled navigation/direct
  route guard, and the existing V1 leader-tactics check also passed.
- Strict OpenSpec validation passed with the official CLI:
  `openspec validate operationalize-dual-universe-leader-tactics-v2 --strict`.
- The 1,400-security, 300-session pure-screen benchmark completed in about 8.1
  seconds with about 270 MiB peak RSS. A global database lease now prevents
  different manifests from launching concurrent V2 capture runs.

## Deliberately not claimed

Production A-share provider capture, factual 252-session accumulation, real
locked-case data evaluation, economic walk-forward/bootstrap evidence,
deployment enablement, and formal promotion are not complete. Same-thread
blocking provider SDKs cannot be physically pre-empted by Python and therefore
remain prohibited at the provider boundary; late returns fail closed and cannot
create successful evidence. V2 remains research-only and cannot change ranking,
positions, alerts, email, SMTP, or execution state.

## Disabled production baseline (2026-08-05)

- Deployed artifact: `e3c1bf7`; backend image
  `sha256:39cf557e027f60795807536cc0b5b46df807d604aefd7bd7b023698d150b9d1b`;
  nginx image
  `sha256:6769dc3a703c719c1d2756bda113659be28ae16cf0da58dd5fd823d6b9a050ea`.
- PostgreSQL migration is `20260804_000062 (head)` and backend, PostgreSQL,
  and nginx containers were healthy/up. Effective V2 `api`, `capture`, and
  `materialize` flags were all `false`; no V2 scheduler job was registered.
- V2 run manifests, candidate observations, A-share universe, theme, and
  adjusted-price tables all contained zero rows before capture enablement.
- Host headroom was 2,097 MiB available memory and 19 GiB free disk. No swap
  was configured, so materialization now also has a 768 MiB fail-closed
  headroom gate.
- Production-boundary baselines use `table:count,max_id` SHA-256 identities:
  `short_etf_signal_runs=03fb2be29de534c1b2b9aea2b31844b8664ee2c304e4b1af489114bd365d0697`,
  `etf_optimized_allocation_snapshots=b4312c0d9656d48ffdc16bc254850c0c68ec155993e39e55e915ea9aa84b892a`,
  `tracked_positions=75e858dba66334bb31eadb8110880935552a131d9969698d7f44c6505099c731`,
  `tracked_position_alerts=f6f0bc7e1212a3efcd0b859eb22c004a2bba76f0649062262a39c5a979c499cf`,
  and `notification_log=2100d86b6246b53810165a71ce5071c718cc9e3d02852860e65c67d2ae8bb996`.
- The approved capture schedule is workdays 21:00-23:59 Asia/Shanghai every
  two minutes. Each invocation has a 52-second work budget, uses one serial
  provider connection with adaptive 5-20-security pages, persists its
  checkpoint, skips completed sessions, and cools down for 30 minutes after an
  Eastmoney provider failure. Materialization, API, and UI remain disabled
  until exact-date factual coverage reaches 95%.

## A-share provider recovery note (2026-08-07)

- Eastmoney `push2` returned reproducible empty/remote-protocol responses from
  the VPS, the local command-line route, and the in-app browser; one bounded
  production capture failed closed without writing decision facts.
- A bounded production-network probe verified TickFlow's current
  `CN_Equity_A` universe at 5,540 unique securities, complete instrument
  metadata for all 5,540, and explicit backward-adjusted A-share daily bars.
- BaoStock's current industry snapshot covered 5,203 of those 5,540 securities
  (93.92 percent). The operational A-share research threshold is therefore the
  previously approved 90 percent, while all ETF publication and ranking
  thresholds remain unchanged. Missing industry rows remain explicit
  exclusions and cannot be backfilled to an earlier cutoff.
- BaoStock runs only in a physically terminable child process; TickFlow HTTP
  work remains serial. The 5-20-security adaptive checkpoint and 55-second hard
  continuation bounds are unchanged. Deployment, factual capture completion,
  and production A-share materialization remain part of task 9.7 and are not
  claimed by this note.
