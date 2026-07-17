# Task 1.4 brief

## Task

Freeze the `final_score_v3` component DAG/manifest, compatibility aliases, stale/unavailable caps, homogeneous asset buckets, anti-double-count lineage, and current score-version selector in one reviewed configuration contract.

## Scope and artifact

- This task freezes the target contract as one machine-readable OpenSpec artifact; production loading, hashing, scoring, and cutover are later tasks.
- Add one JSON or YAML contract under `openspec/changes/harden-etf-comprehensive-ranking/` and link it from the design.
- Do not modify production code or tests and do not activate v3 publication.
- Mark only task 1.4 complete.

## Required contract content

- Target/current contract selector: `final_score_v3`, canonical score field `ranking_score`, and explicit no-fallback-to-v2 behavior when no compatible v3 snapshot exists.
- Fixed, sum-to-one score-bearing weights for disjoint component paths. Use a minimal coherent initial DAG:
  - momentum/technical cross-section: 0.30
  - risk/quality cross-section: 0.20
  - structure/liquidity: 0.15
  - sector trend: 0.15
  - theme/catalyst: 0.10
  - premium/discount: 0.10
- Reliability is a gate/cap applied after aggregation, not a positive weighted component. Label validation, historical performance, healthcheck, AI prose, and user state are explanatory/research-only.
- Required finite input names, eligibility/freshness rules, and primitive lineage for every score-bearing component. A primitive id may appear in exactly one score-bearing path.
- Missing/display-only score-bearing input makes v3 unavailable; weights are never renormalized and no neutral/zero replacement is allowed.
- Frozen hard caps: stale `55`, unavailable `45`; adding risk cannot increase a score; caps apply last.
- Homogeneous bucket ids and comparability rules: broad-equity, sector/theme-equity, fixed-income, commodity, cross-border; unknown/incompatible bucket is not ranked as comparable.
- Compatibility aliases are output/read aliases only: `total_score -> ranking_score`, `rank -> global_rank`, `live_rank -> global_live_rank`, and `opportunity` sort -> `ranking_score`. `opportunity_score` and `final_score_v2` are never aliases to v3.
- Stable tie/missing policy and explicit manifest/schema version.
- Explain that the selector is the target canonical contract but actual readers stay behind publication/cutover gates until later tasks.

## Validation

- Parse the machine-readable artifact.
- Verify weight sum, unique primitive lineage, required caps/buckets/aliases, and absence of validation/history/user-state score paths with a bounded validation command or one-off read-only check.
- Strict-validate the hardening change.
- Commit one focused docs/config commit and report files, checks, and concerns.
