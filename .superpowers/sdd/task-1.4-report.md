# Task 1.4 implementation report

- Status: complete, pending independent review
- Commit: `f006375` (`docs: freeze final score v3 contract`)
- Added one machine-readable target contract and linked it from the design; only task 1.4 was checked.
- No production reader/scorer was changed and v3 was not activated.
- Contract check passed: JSON parsed, fixed weights sum to 1, 20 primitive lineage ids are unique, hard caps are 55/45, and fallback is `none`.
- `openspec validate harden-etf-comprehensive-ranking --strict --no-interactive` passed.
- Concerns: the initial weights are frozen policy parameters and must be evaluated in shadow before publication; changing them creates a new contract/version.
