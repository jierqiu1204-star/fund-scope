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
