## ADDED Requirements

### Requirement: Intraday ETF Profit Watch Can Notify Without Treating Data Warnings As Sell Signals
The system SHALL allow profitable tracked ETFs to send soft take-profit-watch emails while keeping stale quote, missing IOPV, and structure warnings as web-only data quality messages.

#### Scenario: Profitable ETF reaches watch threshold
- **WHEN** a tracked ETF has fresh intraday quote data and reaches its dynamic take-profit-watch threshold
- **THEN** the system may send one `止盈观察提醒` email subject to alert deduplication and cooldown rules

#### Scenario: Missing IOPV remains web-only
- **WHEN** a tracked ETF only has missing IOPV or structure-warning context without a holding alert threshold breach
- **THEN** the system displays the warning on the page and MUST NOT send a sell, reduce, or take-profit email
