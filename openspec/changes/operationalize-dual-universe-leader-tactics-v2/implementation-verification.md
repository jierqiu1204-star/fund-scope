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
  group: 127 passed in 13.61 seconds under a 55-second hard timeout.
- Migration and backend-domain-boundary group on the current remote baseline:
  48 passed. V2 now extends the existing migration chain as
  `20260804_000062 -> 20260802_000061`; isolated upgrade/downgrade and same-day
  universe revision preservation passed.
- Changed-file Ruff and Ruff format checks passed for 55 Python files. The
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
