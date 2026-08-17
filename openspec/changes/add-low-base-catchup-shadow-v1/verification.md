## Verification

Verified at 2026-08-17 15:52 CST.

- Backend leader-tactics and domain-boundary suite: `231 passed` in 17.17 seconds.
- Backend Ruff: passed for the full backend tree.
- Frontend TypeScript check: passed.
- Frontend leader-tactics interaction checks: `8 passed`.
- Strict OpenSpec validation: `Change 'add-low-base-catchup-shadow-v1' is valid`.

## Bounded production screening result

No current candidate is asserted from this run. Production evidence was unavailable for two reproducible reasons:

- SSH reached the configured host but timed out during banner exchange within the bounded connection window.
- The registered Eastmoney `创新药` board (`BK1106`) request failed closed with `ProxyError`; a direct no-proxy request returned an empty server response.

The implementation therefore returns no invented candidate and does not use a local or stale database as a substitute. A production materialization after connectivity is restored is still required to obtain the current `watch`, `actionable`, `overextended`, and `invalidated` rows.

## Temporal-setup extension verification

Verified at 2026-08-17 22:14 CST after production SSH connectivity recovered.

- Formula registry: `leader_tactics_formula_registry_v8`, hash `14674ab33514be0e6120fe58c4e46a182207a3d0b200e547da726863546a0901`.
- Strategy/API/lifecycle/domain tests: `51 passed` in 1.13 seconds.
- Bounded continuation, two-stage materialization, and memory-guard tests: `18 passed` in 0.70 seconds.
- Full backend Ruff and `git diff --check`: passed.
- The local `openspec` executable is no longer installed. The Markdown change artifacts were checked manually and through the repository gates above; no replacement package was downloaded during acceptance.

### Read-only production historical diagnostics

The production database contains 186 adjusted rows per exemplar through 2026-08-17. The same frozen price-volume calculations, using no bars after each signal date, produced:

- `000636` Fenghua High-Tech: first actionable launch on 2026-07-29 (`return_5=15.04%`, `overextension_atr=0.8342`).
- `002437` Yuheng Pharmaceutical: an older independent actionable cycle on 2026-07-01. For the late-July low-base cycle, 2026-08-07 is a single-day-volume watch (`latest_relative_volume=2.650`, five-day expansion `1.079`, `overextension_atr=1.3582`); repeated volume first passed on 2026-08-10, when ATR extension had already reached `3.2282`, so the entry was overextended.
- `002131` Leo Group: an early actionable launch on 2026-07-28; the stronger 2026-07-31 launch is `extended_watch` (`return_5=21.04%`, `overextension_atr=1.7819`) and is not actionable.

These are retrospective diagnostics, not strict historical PIT recommendations: all three histories were first received on 2026-08-07, and no matching fine-theme membership facts existed for these assets. The implementation therefore exposes `retrospective_hypothesis_replay=true` and `historical_validation_eligible=false`; it does not backdate the data or claim that the complete hot-theme gate was available on those historical dates.
