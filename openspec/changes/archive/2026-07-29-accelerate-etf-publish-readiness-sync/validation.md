# Validation Evidence

## Local 1,500 ETF × 61 Session Benchmark

Run on 2026-07-20 with the production-shaped schema and bounded runner. The fixture
contained 1,500 authoritative ETF codes with 60 committed decision-eligible
total-return-adjusted sessions per code; each slice fetched the exact 61-session
window. Three samples were recorded per profile.

| Profile | Codes | Elapsed samples (s) | P95 (s) | Peak RSS | Rows/s range | Max SQL/page |
|---|---:|---|---:|---:|---:|---:|
| conservative | 10 | 0.656, 0.641, 0.703 | 0.703 | 224.4 MiB | 867.7–951.6 | 3 |
| maximum | 20 | 1.234, 1.063, 1.031 | 1.234 | 224.9 MiB | 988.7–1183.3 | 3 |

Both profiles exited below 60 seconds. The maximum profile stayed below the
30-second P95, 512 MiB RSS, and eight-statements-per-page gates. Durable active-code
checkpoints advanced monotonically across all six slices.

This local benchmark permits the maximum profile implementation to exist, but does
not by itself promote production. Production promotion still requires three healthy
real VPS slices and automatically demotes on resource or provider degradation.

## Conservative Production Deployment

Commit `4d578e642c3300dee303592b505ca1484417d9e9` was deployed from the
`codex-strategy-lab` branch by GitHub Actions run `29753471737` on 2026-07-20.
The workflow completed successfully in 8 minutes 19 seconds after building the
candidate image, verifying a single migration head, taking and checksumming a
database backup, deploying the application, and passing its local health gate.

The deployed scheduler has one weekday `post_close_etf_adjusted_sync` trigger with
`max_instances=1` and coalescing enabled. Its coordinator starts with the
conservative 10-code/five-minute effective profile and the legacy scheduled daily
research job now runs the fund-only wrapper, so it cannot start a second ETF history
sync.

Post-deploy evidence:

- backend, nginx, and PostgreSQL containers were all `Up`;
- `http://110.42.222.9/api/health` returned `{"app":"ok","db":"ok"}`;
- production Compose exposed port 80 only; HTTPS port 443 was not reachable;
- deployment completed after the 22:55 Asia/Shanghai publication-readiness window,
  so no out-of-window provider slice was invoked and no real-slice evidence was
  claimed.

Tasks 8.2 through 8.6 remain blocked on in-window real VPS sessions and the factual
95 percent daily plus 61-session adjusted-price gates.

## Real VPS Slices And Profile Promotion

Production evidence was read directly from the VPS database on 2026-07-24 while
the catch-up scheduler remained inside its configured post-close window. The
authoritative universe contained 1,485 ETFs with snapshot hash
`9e3d9db8f2ae3a125f03cad91a403ece9078d5a2b13fcce192f2216e2bcc9a52`.

Three consecutive maximum-profile slices shared checkpoint identity
`e2e836f4c48ca75cc8e89b3e2eb849e1d2979e619f4b8a7654bdd88382ff0fb5`:

| JobRun | Daily coverage | Daily delta | 61-session coverage | Warm-up delta | Completed / attempted | Elapsed | Peak RSS | Rows/s | Remaining | Response |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 9380 | 74.8822% | +1.2795 pp | 66.8013% | +1.2795 pp | 19 / 20 | 14.025 s | 269.8 MiB | 92.621 | 493 | 1,305 B |
| 9384 | 76.1616% | +1.2795 pp | 68.0808% | +1.2795 pp | 19 / 20 | 13.341 s | 269.8 MiB | 97.669 | 474 | 1,305 B |
| 9388 | 77.4411% | +1.2795 pp | 69.3603% | +1.2795 pp | 19 / 20 | 13.492 s | 269.8 MiB | 96.575 | 455 | 1,305 B |

All three slices stopped with `continuation_required`, used three SQL statements
per page, advanced the remaining count monotonically, and kept the provider circuit
closed. TickFlow supplied accepted total-return-adjusted data without timeout or
rate-limit evidence; Eastmoney and efinance were not needed. For the target date,
all 1,150 persisted rows were decision-eligible `total_return_adjusted` rows from
TickFlow, with zero raw-price decision violations and zero non-finite decision
violations.

Promotion was factual and automatic. Immediately before the first 20-code slice
(JobRun 9174), the three latest conservative slices were:

| JobRun | Profile | Elapsed | Peak RSS | Circuit |
|---:|---:|---:|---:|---|
| 9156 | 10 | 8.247 s | 222.7 MiB | closed |
| 9163 | 10 | 7.160 s | 222.8 MiB | closed |
| 9170 | 10 | 8.099 s | 222.8 MiB | closed |

The first maximum slice then ran in 13.838 seconds at 223.1 MiB RSS with the
circuit still closed. This satisfies the configured elapsed, 512 MiB RSS,
provider-health, lease, and monotonic-checkpoint promotion gates without increasing
worker concurrency or the 60-second hard limit.

At 20:52 Asia/Shanghai, current daily coverage was 77.4411 percent and current
61-session coverage was 69.3603 percent, so publication correctly remained waiting.
Tasks 8.4 through 8.6 remain pending until the factual dual 95 percent gate and
post-publication checks pass.

## Latest Read-Only Audit — 2026-07-25

No provider work was started during this Saturday audit. The final 2026-07-24
post-close measurement (JobRun 9542) recorded:

- authoritative universe: 1,485 ETFs;
- daily adjusted coverage: 1,462 / 1,485 (98.4512 percent);
- 61-session warm-up coverage: 1,342 / 1,485 (90.3704 percent);
- warm-up gaps: 143;
- publication gate: false.

The paired bounded slice (JobRun 9543) used the conservative 10-code profile and
checkpoint identity
`249e5688f8a28a9f1ae688b256f3ee7e1193c033f3c48384e951384b44abf56c`.
It completed 9 of 10 codes in 7.758 seconds, persisted 643 rows, peaked at
231,710,720 bytes RSS, and stopped with `continuation_required`. TickFlow had 30
accepted successes, zero failures/timeouts, and a closed circuit. All 1,462
target-date decision rows used
`tickflow.free.klines.backward_v1`; raw-decision and non-finite violations were
both zero.

All production containers were up. Host memory was 3,399 MiB total with 2,158
MiB available; backend, PostgreSQL, and nginx used 325.1 MiB, 455.2 MiB, and
5.2 MiB respectively.

There is still no current full-scope published dual-ranking snapshot. Because
the 61-session gate is below 95 percent, Tasks 8.4–8.6 remain open and no manual
weekend continuation or publication was attempted.
