# Replay dependency gate — 2026-07-17

This is local integration evidence. It is not production evidence and does not
satisfy any real-session, return-sufficiency, holdout, or rollout gate.

## Ownership

`enable-etf-point-in-time-ranking-replay` remains the exclusive owner of the
pure replay scorer, factual membership audit/backfill, research replay adapter,
paired Top10 five-session endpoint, walk-forward, purge/embargo, policy shadow,
holdout, API/frontend presentation, and real replay acceptance. This repair
change did not add those implementations.

## Compatible dependency outputs verified

All commands were run from `backend` with an outer timeout of at most 60
seconds.

- PIT membership model/migration, cutoff, and survivor-bias batch: 19 passed.
- Stage A replay batch: 32 passed.
- Stage B plus frozen candidates: 20 passed.
- Forward outcomes plus ranking-source contracts: 12 passed.
- Validation endpoints plus replay provenance API: 7 passed.
- Research replay no-production-side-effect integration: 1 passed.

## Integration remains blocked

`uv run pytest tests/test_etf_ranking_walk_forward.py -q` fails during
collection with:

`ModuleNotFoundError: No module named 'app.services.strategy_lab.etf_ranking_walk_forward'`

The owner change still has tasks 2.4, 3.5, 6.4–6.6, 7.1–7.5, 8.1–8.4, and
9.1–9.5 unchecked. Therefore paired/walk-forward/holdout integration and formal
research-return evidence remain unavailable. The repair change must not fill
that gap with legacy scores, production snapshots, raw Sina/efinance prices, or
simulated evidence.
