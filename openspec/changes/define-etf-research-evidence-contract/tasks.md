## 1. Contract Boundary Setup

- [x] 1.1 Add an ETF research evidence contract module with dataclasses or Pydantic schemas for signal, allocation, replay, and evidence summaries.
- [x] 1.2 Define stable contract version constants for signal, allocation, replay, execution model, fee model, and evidence schema.
- [x] 1.3 Add helpers to compute a deterministic contract hash from version, data date, execution model, and relevant config.
- [x] 1.4 Document allowed dependency direction in the backend architecture notes or `AGENTS.md` reference section without changing runtime behavior.

## 2. Architecture Guardrails

- [x] 2.1 Add a lightweight dependency validation test or script for forbidden imports between market data, research, allocation, tracking, risk alerts, and notification modules.
- [x] 2.2 Allow scheduler, admin, API, and workflow modules to orchestrate multiple domains without being flagged.
- [x] 2.3 Run the guardrail against the current codebase and list any existing violations before changing domain logic.
- [x] 2.4 Fix only violations directly needed for this change; document unrelated violations instead of broad refactoring.

## 3. Signal And Allocation Contract Emission

- [x] 3.1 Add signal contract metadata to ETF short-term signal runs or serialized signal items.
- [x] 3.2 Add allocation contract metadata to ETF observation portfolio snapshots without breaking old response fields.
- [x] 3.3 Ensure market data reliability state and data timestamps are included in signal and allocation contract payloads.
- [x] 3.4 Ensure old signal and allocation snapshots without contract fields are treated as legacy evidence.

## 4. Backtest And Validation Contract Consumption

- [x] 4.1 Update ETF portfolio backtest creation to record replay contract metadata and contract hash.
- [x] 4.2 Ensure ETF portfolio backtest consumes current signal/allocation contract data instead of copying short-term page rules.
- [x] 4.3 Update ETF label validation to group outcomes by observation label, entry timing label, signal version, data reliability, and signal date.
- [x] 4.4 Mark validation samples as pending when their forward windows have not completed.
- [x] 4.5 Prevent backtest or validation output from mutating live ranking, allocation, tracking, or email thresholds.

## 5. Evidence Summary API And UI

- [x] 5.1 Extend short-term detail or bootstrap response with evidence status: `同源已验证`, `等待验证`, `样本不足`, `版本不一致`, or `旧口径结果`.
- [x] 5.2 Add evidence summary fields for sample count, forward-window metrics, backtest metrics, coverage, and caveats when available.
- [x] 5.3 Update `/short-term` to display evidence status separately from observation label, entry timing label, portfolio weight, and holding alert.
- [x] 5.4 Ensure old strategy-lab simulation or legacy backtests are shown as old evidence, not current ETF strategy proof.
- [x] 5.5 Keep UI language research-only and avoid implying labels are guaranteed reliable.

## 6. Tests

- [x] 6.1 Add unit tests for contract hash stability and version mismatch detection.
- [x] 6.2 Add tests proving old evidence is not treated as current evidence.
- [x] 6.3 Add tests for dependency guardrail forbidden and allowed imports.
- [x] 6.4 Add tests that backtest stores replay contract metadata and does not mutate production strategy state.
- [x] 6.5 Add tests that label validation excludes incomplete forward windows from completed samples.
- [x] 6.6 Add frontend type coverage for evidence status and legacy/empty states.

## 7. Verification

- [x] 7.1 Run focused backend tests for short research, ETF backtest, signal validation, and dependency guardrails.
- [x] 7.2 Run `uv run ruff check .`.
- [x] 7.3 Run `corepack pnpm exec tsc --noEmit`.
- [x] 7.4 Run `corepack pnpm build:static`.
- [x] 7.5 Verify `/short-term` can show current ETF labels and portfolio data even when evidence status is waiting or sample-insufficient.
