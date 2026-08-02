# Production Validation

- Deployment commit: `1ec08e45a2b1e60c4c9cbd5d9e8ce6d62400696e`
- GitHub workflow: `30742216565` (`success`)
- Source ranking run/date: `121` / `2026-07-31`
- Evidence ID: `3`
- Manifest: `e263900f25ba107210415253374f437e5f6e18c62f2acad882ecdfe6d6dadef7`
- Production projection: `complete`, 15 bounded aggregate rows, zero PIT promotion credit, `production_mutation_allowed=false`

## Coverage

| Metric | Value |
|---|---:|
| Source/loaded ETFs | 1,376 / 1,376 |
| Classified current-vintage ETFs | 1,011 |
| ETFs with 120/180/252/300 sessions | 761 / 761 / 761 / 761 |
| Eligible/evaluated historical signal dates | 237 / 237 |
| Dates with at least one candidate | 84 |
| Zero-candidate dates | 153 |
| Unique candidate signals | 225 |
| Horizon event rows | 1,125 |

## Costed Results

Returns include 5 bps fee and 5 bps slippage per side (20 bps round trip).

| Candidate | Horizon | Signals | Mean net | Win rate | Peer net | Net excess | 95% block-bootstrap interval |
|---|---:|---:|---:|---:|---:|---:|---:|
| All | 1 | 225 | 0.0013% | 46.22% | -0.0425% | 0.0438% | [-0.7439%, 0.5880%] |
| All | 3 | 225 | 0.2574% | 48.44% | 0.3362% | -0.0789% | [-1.1987%, 1.3206%] |
| All | 5 | 225 | 0.2037% | 52.44% | 0.3544% | -0.1507% | [-1.9343%, 2.0204%] |
| All | 10 | 225 | 0.3391% | 51.11% | 0.3343% | 0.0048% | [-0.9377%, 4.1727%] |
| All | 20 | 225 | 1.3144% | 52.44% | 1.1887% | 0.1258% | [-2.5522%, 5.9261%] |
| Breakout | 1 | 224 | 0.0123% | 46.43% | -0.0313% | 0.0436% | [-0.6639%, 0.5729%] |
| Breakout | 3 | 224 | 0.2808% | 48.66% | 0.3298% | -0.0491% | [-1.1063%, 1.4327%] |
| Breakout | 5 | 224 | 0.2475% | 52.68% | 0.3488% | -0.1014% | [-1.6451%, 2.0323%] |
| Breakout | 10 | 224 | 0.3819% | 51.34% | 0.3433% | 0.0386% | [-0.7499%, 3.9117%] |
| Breakout | 20 | 224 | 1.3798% | 52.68% | 1.2563% | 0.1235% | [-2.0294%, 5.8864%] |
| Former-leader repair | 1 | 1 | -2.4609% | 0.00% | -2.5590% | 0.0981% | unavailable |
| Former-leader repair | 3 | 1 | -4.9826% | 0.00% | 1.7714% | -6.7540% | unavailable |
| Former-leader repair | 5 | 1 | -9.5913% | 0.00% | 1.5971% | -11.1884% | unavailable |
| Former-leader repair | 10 | 1 | -9.2435% | 0.00% | -1.6957% | -7.5478% | unavailable |
| Former-leader repair | 20 | 1 | -13.3304% | 0.00% | -13.9576% | 0.6272% | unavailable |

## Interpretation Limits

- Every reported interval includes zero; this run does not establish robust incremental alpha.
- The former-leader repair proxy has one signal and is statistically unusable.
- Current-vintage membership and taxonomy introduce survivorship/lookahead bias even though all price features are date-local.
- Event-series drawdown is deliberately not presented as a capital-constrained portfolio drawdown because horizons overlap.
- Results remain research-only and cannot mutate rankings, positions, alerts, email, or execution state.
