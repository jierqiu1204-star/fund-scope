# Verification evidence

All commands used an external hard timeout of 55 seconds or a tool yield below that limit.
The test process explicitly removed inherited proxy variables and pinned `PYTHONPATH` to the
active worktree so it could not import the retired backup checkout.

## Backend

- Ruff targeted check/fix: `All checks passed!`
- Related strategy group: `88 passed` in 2.40 seconds.
- Owner scope and shared alert/action pipeline group: `27 passed` in 1.26 seconds.
- Covered migration upgrade/downgrade, backward-compatible defaults, ETF-only validation,
  manual and candidate-backed provenance, manifest mismatch rejection, owner scope, immediate
  bounded entry, same-session suppression, hard-stop priority, morning-high causality, closed
  10-minute MA5 failure, mandatory 10:30 exit, stale/future quote rejection, policy state reset,
  full-exit mapping, and research/ranking/notification isolation.

## Frontend

- `pnpm typecheck`: passed.
- `pnpm test:late-day-turnaround`: passed.
- `pnpm exec prettier --check lib/types.ts scripts/check-late-day-turnaround.mjs`: passed.
- The legacy `app/short-term/page.tsx` is intentionally not globally reformatted: a global
  Prettier write would create more than 4,000 unrelated changed lines. Type checking and the
  focused contract script verify its selector, default, API field, policy explanation, and
  provenance display while keeping the source diff to the intended 75 added lines.

## Boundaries

- Late-day research materialization remains shadow-only and cannot import tracked-position,
  notification, transaction, comprehensive-ranking, or risk-action writers.
- Candidate-backed tracking validates an existing complete ETF manifest but does not mutate the
  manifest, create ranking runs, create alerts, or create execution state.
- Manual policy selection is labeled `manual_selection`; it is never represented as a generated
  candidate or automatic trade.
- `late_day_t1_exit` reuses the existing email, cooldown, audit, owner-recipient, and absolute-zero
  position-action path.

## OpenSpec

- Strict validation: passed 1/1 with zero issues in 8 ms.

## Leader-tactics sell-only extension

- Focused evaluator, policy, migration, action, and domain-boundary group: `65 passed` in
  1.94 seconds.
- Existing tracked-position, owner-recipient, MA5, notification-envelope, template, delivery,
  and policy regressions: `72 passed` in 11.91 seconds.
- Full backend Ruff: `All checks passed!`; `git diff --check` passed.
- Frontend leader tracking contract: `5 passed`; existing leader V2 interaction contract:
  `8 passed`; pagination/static contract passed; TypeScript `--noEmit` passed; targeted
  Prettier check passed.
- Verified ETF/A-share policy validation, authoritative A-share name lookup, candidate-backed
  PIT provenance, bounded adjusted reads, common adjustment-version enforcement, corrupt frozen
  state rejection, ATR20 risk freeze, high-water 1R arming, hard-stop/breakeven/MA5 priority,
  daily-close-only routing, full-exit mapping, SMTP failure retry, success-only suppression, and
  absence of candidate/buy email behavior.
- Every acceptance command used a 55-second process alarm. The local `openspec` executable was
  not installed during this extension review; the previously recorded strict validation remains
  unchanged, and the updated Markdown artifacts were checked through the repository test and
  formatting gates above.
