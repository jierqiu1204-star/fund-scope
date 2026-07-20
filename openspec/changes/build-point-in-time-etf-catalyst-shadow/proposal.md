## Why

Theme catalysts are currently too sparse, manual, subjective, and weakly timestamped to influence ETF ranking safely. Before assigning any catalyst weight, FundScope needs point-in-time source receipts and shadow event evidence that distinguishes “no event observed” from “source unavailable” and cannot rewrite history after outcomes are known.

## What Changes

- Add a source registry and immutable receipts for official announcements and approved public sources, including source ID, canonical URL, published time, first-received time, content hash, fetch state, and correction lineage.
- Store structured ETF/theme event facts with stable event ID, event type, direct or proxy theme mapping, direction, effective period, verification state, and source receipts, but no strength score or ranking weight.
- Record per-theme and per-session coverage as `active`, `observed_none`, `unavailable`, or `not_applicable` so missing ingestion is never treated as neutral evidence.
- Enforce point-in-time availability: historical shadow snapshots may use only receipts first received by the signal cutoff and retain the original version when a source is later corrected.
- Allow AI to extract, normalize, classify, and summarize candidate facts only; it cannot create a verified event without source evidence or affect score, rank, allocation, alerts, or notifications.
- Mark manual seeds and proxy-theme events as display-only unless they independently satisfy the verified source and mapping contract; they never enter ranking.
- Generate catalyst shadow snapshots alongside, but separate from, research and actionable ranks.
- Run pre-registered event studies using decision-eligible adjusted outcomes and matched baselines before proposing any formula, cap, or weight.
- Explicitly retire the archived idea of assigning default `±2.5` points or a fixed catalyst percentage without evidence.

## Capabilities

### New Capabilities
- `etf-point-in-time-catalyst-shadow`: Defines source receipts, structured event facts, point-in-time shadow snapshots, coverage states, AI boundaries, and research-only event studies.

### Modified Capabilities
- `news-aggregation`: Adds auditable source receipt and successful-empty semantics required by ETF catalyst extraction without changing existing news display behavior.
- `short-term-research`: Displays catalyst shadow facts and limitations separately from ranking and action eligibility.
- `etf-research-evidence-contract`: Records catalyst source, snapshot, mapping, and event-study contracts without treating them as score evidence.

## Impact

- Official/public source ingestion, immutable event storage, theme taxonomy mapping, Strategy Lab event studies, evidence APIs, and `/short-term` detail explanations.
- No catalyst score, rank weight, ranking reorder, portfolio input, tracked-position signal, or email trigger.
- Implementation follows the ranking-surface separation and factor-evidence changes so catalyst studies reuse their point-in-time and production-isolation boundaries.
