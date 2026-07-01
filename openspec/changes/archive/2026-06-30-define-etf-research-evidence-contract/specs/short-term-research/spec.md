## ADDED Requirements

### Requirement: Short-term workbench shows ETF evidence contract status
The short-term research workbench SHALL show whether the selected ETF's current signal and allocation have matching historical evidence.

#### Scenario: Evidence status is available
- **WHEN** a selected ETF has evidence status from the ETF research evidence contract
- **THEN** `/short-term` displays the status as `同源已验证`, `等待验证`, `样本不足`, `版本不一致`, or `旧口径结果`

#### Scenario: Current strategy lacks evidence
- **WHEN** the selected ETF's current signal or allocation version has no matching backtest or validation result
- **THEN** `/short-term` explains that the current label or weight has not yet been historically replayed

#### Scenario: Legacy evidence exists
- **WHEN** only legacy simulation or old strategy-lab results exist
- **THEN** `/short-term` labels them as old evidence and does not present them as proof of the current ETF workbench strategy

### Requirement: Short-term workbench separates current evidence types
The short-term research workbench SHALL distinguish current signal evidence, allocation evidence, label validation evidence, and backtest evidence.

#### Scenario: User opens selected ETF detail
- **WHEN** evidence summaries are available
- **THEN** the detail panel separates label validation, portfolio allocation reason, and strategy backtest metrics into distinct sections

#### Scenario: Evidence has limitations
- **WHEN** evidence was produced using daily bars, limited samples, or missing intraday data
- **THEN** the UI states those limitations near the evidence summary
