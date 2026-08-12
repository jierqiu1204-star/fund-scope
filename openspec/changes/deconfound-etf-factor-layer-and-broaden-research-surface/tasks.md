## 1. Ranking eligibility contract

- [x] 1.1 Version the research-quality policy and expose separate score, research, and action-quality states.
- [x] 1.2 Update pure ranking generation so score-ready low-liquidity or unresolved-taxonomy rows retain a research rank but fail action eligibility.
- [x] 1.3 Add unit tests for raw/fallback exclusions and preservation of action gates.

## 2. Snapshot materialization and API

- [x] 2.1 Persist every score-ready research row with quality flags/reasons while preserving action exclusions.
- [x] 2.2 Add layered summary counters and reason counts without weakening idempotency, sealing, or publication verification.
- [x] 2.3 Extend compact snapshot/item schemas, the read model, and the observation endpoint with compatible defaults.
- [x] 2.4 Update frontend wording, badges, and counts so observation-only ranks cannot appear actionable.
- [x] 2.5 Add bounded materialization, read-model, API, and frontend tests.

## 3. Peer support and deconfounding

- [x] 3.1 Correct formal diagnostic peer-count reporting to the weakest required primitive without changing successful scores, weights, or contract hash.
- [x] 3.2 Implement a shadow peer-support report requiring 20 non-clone peers on common support with stable unavailable reasons.
- [x] 3.3 Add deterministic same-date/bucket correlation matrices, clusters, effective factor counts, and residual evidence.
- [x] 3.4 Add an immutable registry of at most three deconfounding candidates and fail-closed observations for missing PIT inputs.
- [x] 3.5 Integrate deconfounding evidence into the existing factor report/checkpoint without production ranking or action mutations.
- [x] 3.6 Add tests for sparse peers, cluster determinism, candidate caps/hashes, no fallback, and no production mutation.

## 4. Verification

- [x] 4.1 Run focused backend test groups with a hard timeout no longer than 60 seconds per command.
- [x] 4.2 Run focused frontend tests and type checks with a hard timeout no longer than 60 seconds per command.
- [x] 4.3 Run Ruff on touched Python files with a hard timeout no longer than 60 seconds.
- [x] 4.4 Run strict OpenSpec validation with a hard timeout no longer than 60 seconds and review the final diff.
