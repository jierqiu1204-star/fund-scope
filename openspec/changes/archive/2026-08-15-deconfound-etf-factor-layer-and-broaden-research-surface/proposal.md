## Why

The current canonical ETF snapshot can calculate a valid adjusted-daily score for most of the authoritative universe, but the discovery-oriented research surface applies the same absolute tradability and taxonomy gates used for action safety, reducing the latest production research surface to 442 of 1,500 ETFs. At the same time, the score-bearing factor graph contains correlated price, risk, and liquidity primitives and permits statistically unstable two-member peer groups, while production has only one factual PIT capture date, so changing formal weights now would confuse coverage effects with incremental alpha.

## What Changes

- Separate score eligibility, research discovery eligibility, and actionable eligibility: a finite point-in-time adjusted `daily_reconstructable_v1` score may appear on the research surface even when liquidity or taxonomy quality makes it observation-only and non-actionable.
- Preserve the existing 50 million yuan average-turnover gate, taxonomy requirements, 120-session history gate, provider-health gates, and execution evidence on the actionable surface; this change does not lower trading safeguards or publication readiness thresholds.
- Publish explicit observation-only quality flags, exclusion reasons, and coverage counters so consumers can distinguish calculable coverage, research coverage, and action coverage.
- Correct formal peer-count reporting to use the weakest required primitive, and introduce a stricter per-primitive/common-support policy for shadow experiments; the stricter threshold MUST NOT alter `final_score_v3` until promoted through a separate score-version change.
- Add factor-redundancy diagnostics that measure pairwise rank correlation, correlation clusters, effective factor count, residual IC, and common-support marginal contribution before a primitive can be treated as independent alpha.
- Register no more than three immutable research-only candidates: the current baseline, a deconfounded residual-momentum/breadth candidate, and an optional PIT flow/constituent-breadth candidate that remains unavailable until authoritative inputs exist.
- Keep `final_score_v3`, production weights, `daily_reconstructable_v1`, allocation, alerts, and notifications unchanged until the existing promotion gates pass and a separate manually approved score-version change is created.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `etf-ranking-surfaces`: Broaden the daily research discovery surface without weakening actionable eligibility, and expose separate score, research, observation-only, and action coverage semantics.
- `etf-factor-incremental-alpha-validation`: Require stable per-primitive/common peer support, redundancy clustering, and a frozen three-candidate deconfounding experiment before any factor is considered incremental alpha.

## Impact

- Backend ranking-quality policy, dual-surface materialization, read models, API schemas, and `/short-term` evidence labels.
- `final_score_v3` peer-distribution construction and diagnostics, without changing its production weights or contract identity.
- Existing PIT factor experiment manifests, panel construction, diagnostics, evidence reports, and bounded checkpoints.
- Focused backend/frontend tests and OpenSpec validation; no new runtime dependency, provider, concurrent worker, or production mutation path.
