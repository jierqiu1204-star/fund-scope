## 1. Backend Portfolio Logic

- [x] 1.1 Update ETF observation portfolio selection to require eligible observation label and entry timing before assigning primary target weights.
- [x] 1.2 Split candidates into primary weighted items, watch-only high/overextended items, and excluded/wait items with readable Chinese reasons.
- [x] 1.3 Add simplified risk constraints for single ETF cap, total ETF cap, volatility/drawdown reduction, theme concentration, and recent-return correlation reduction.
- [x] 1.4 Preserve existing `items`, `cash_weight`, `research_only`, and `no_trade_instruction` fields while adding watch-only/excluded groups and methodology text.

## 2. Frontend Presentation

- [x] 2.1 Update `/short-term` ETF observation portfolio section to show primary observation combo separately from strong-but-don't-chase and wait/avoid groups.
- [x] 2.2 Make empty primary combo state explicit when no ETF passes entry timing gates.
- [x] 2.3 Replace wording that implies buying with research-only manual-reference wording.

## 3. Tests

- [x] 3.1 Add backend tests proving high-watch, chase-warning, stale, insufficient, and weak-entry ETFs do not receive primary weights.
- [x] 3.2 Add backend tests proving healthy-pullback/trend-continuation ETFs can receive weights and cash weight increases when candidates are risky.
- [x] 3.3 Add backend tests for correlation/theme concentration reducing or skipping overlapping candidates.
- [x] 3.4 Add frontend type/build coverage for the expanded observation portfolio response.

## 4. Validation

- [x] 4.1 Run targeted backend tests for short research observation portfolio.
- [x] 4.2 Run `uv run ruff check .`.
- [x] 4.3 Run `corepack pnpm exec tsc --noEmit`.
- [x] 4.4 Run `corepack pnpm build:static`.
- [x] 4.5 Verify `/short-term` shows no primary weights for `高位别追` ETFs and shows them under watch-only explanation instead.
