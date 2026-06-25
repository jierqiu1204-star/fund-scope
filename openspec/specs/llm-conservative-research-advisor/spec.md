# llm-conservative-research-advisor Specification

## Purpose
TBD - created by archiving change add-llm-conservative-research-advisor. Update Purpose after archive.
## Requirements
### Requirement: LLM Research Reports Are Generated After Deterministic Signals
The system SHALL generate LLM research reports only after deterministic short-term fund and ETF signals have been computed, and SHALL use deterministic scores, labels, metrics, and risk flags as the source of truth.

#### Scenario: Report uses existing signal data
- **WHEN** the daily LLM research job runs after short-term signal generation
- **THEN** each LLM request receives the asset code, name, asset type, rank, deterministic label, score, recent return metrics, drawdown, volatility, liquidity where available, data date, risk flags, and investment direction

#### Scenario: LLM does not change ranking
- **WHEN** the LLM returns a research report for an asset
- **THEN** the persisted rank, score, deterministic label, and risk flags remain unchanged from the deterministic signal run

### Requirement: Conservative Observation Actions Are Enforced
The system SHALL expose only conservative observation actions for LLM-assisted research and SHALL NOT expose direct trading commands.

#### Scenario: Allowed action labels are returned
- **WHEN** the API returns an LLM research report
- **THEN** its action label is one of `重点观察`, `高位别追`, `谨慎`, `暂不考虑`, or `退出观察`

#### Scenario: Stronger model action is downgraded
- **WHEN** the deterministic label maps to `谨慎` or `暂不考虑` and the LLM returns a stronger action label
- **THEN** the backend replaces the model action with the conservative rule-derived action before saving or returning the report

#### Scenario: High watch is not treated as buyable
- **WHEN** an asset's deterministic label is high-watch or has chase-risk, surge, or high-volatility risk flags
- **THEN** the LLM-assisted action is `高位别追` or weaker and the explanation highlights the relevant risk

### Requirement: LLM Output Is Structured And Validated
The system SHALL request and store structured LLM output with required fields for beginner-readable research, risks, opposing views, and fallback-source visibility.

#### Scenario: Valid structured report is stored
- **WHEN** the model returns valid structured output
- **THEN** the system stores action label, plain summary, opportunity notes, risk notes, opposing view, watch conditions, holding note, data limitations, model name, prompt version, source status, and generation timestamp

#### Scenario: Missing optional report detail is completed by rules
- **WHEN** the model returns usable structured output but one or more required explanatory fields are empty or malformed
- **THEN** the system fills those fields from deterministic rule fallback and marks the saved report as partially rule-completed

#### Scenario: Invalid model output is rejected
- **WHEN** the model output contains invalid JSON, invalid action labels, direct trading commands, guaranteed-return claims, target-price claims, or prohibited account-sync claims
- **THEN** the system records the LLM attempt as failed and falls back to rule-based explanation without changing the deterministic signal

### Requirement: LLM Failures Degrade Gracefully
The system SHALL keep short-term research usable when the LLM provider is unavailable, the API key is missing, quota is exhausted, or the model response fails validation.

#### Scenario: API key is not configured
- **WHEN** no model API key is configured
- **THEN** deterministic rankings, charts, labels, and rule-based explanations remain available and the UI states that AI analysis is not enabled

#### Scenario: Provider call fails
- **WHEN** a model call fails during daily report generation
- **THEN** the job stores the failed asset code and readable error message while continuing with other selected assets

### Requirement: Daily Advisor Job Is Visible And Idempotent
The system SHALL provide a manual and scheduled job for LLM conservative research reports, and repeated runs for the same signal run SHALL update existing reports instead of creating duplicates.

#### Scenario: Admin runs advisor job from web
- **WHEN** the user clicks the web action to generate AI research reports
- **THEN** the system generates reports for the latest eligible short-term signal run and records job status in admin job history

#### Scenario: Daily scheduled advisor runs after signal generation
- **WHEN** the daily scheduler executes short-term research jobs
- **THEN** the LLM advisor job runs after source data sync and deterministic short-term signal generation

#### Scenario: Re-running same signal is idempotent
- **WHEN** the advisor job runs twice for the same signal run and asset
- **THEN** the second run updates the existing report record rather than inserting duplicate active reports

### Requirement: Frontend Shows Beginner-Friendly Multi-Angle Research
The system SHALL show LLM-assisted research in the short-term fund and ETF interface as concise Chinese cards focused on observation, risk, evidence, and source transparency.

#### Scenario: User opens short-term page with reports
- **WHEN** the user opens `/short-term` and LLM reports exist for the latest signal run
- **THEN** the page shows 今日研究建议, action label, plain summary, why it is observed, main risks, opposing view, watch conditions, and data limitations in Chinese

#### Scenario: Report is absent
- **WHEN** a ranked asset does not have a valid LLM report
- **THEN** the page shows the deterministic rule explanation and does not hide the asset from the ranked list

#### Scenario: Report source is visible
- **WHEN** the page renders an advisor report
- **THEN** it clearly labels the report as `AI生成`, `AI生成，部分规则补齐`, or `规则兜底`

### Requirement: Research Language Avoids Trading Instruction Claims
The system SHALL frame LLM-assisted outputs as research observations and SHALL reject or hide outputs that present direct buy, sell, target price, guaranteed return, or automatic trading claims.

#### Scenario: Prompt discourages unsafe trading language
- **WHEN** the LLM advisor builds its system prompt
- **THEN** the prompt states that the model must not output direct trading instructions, target prices, guaranteed returns, or claims of broker or Alipay account synchronization

#### Scenario: Prompt provides safe alternatives
- **WHEN** the LLM advisor asks for a report
- **THEN** the prompt provides safe observation phrasing such as `进入观察名单`, `需要人工复核`, `风险偏高`, and `不代表买入建议`

#### Scenario: Prohibited language is filtered
- **WHEN** the model output contains direct trading commands, target prices, guaranteed profit claims, or claims of syncing with Alipay real-time account data
- **THEN** the output is rejected and the UI displays a conservative fallback explanation

#### Scenario: Neutral rule terms are not treated as commands
- **WHEN** the model or fallback explanation references system rule names such as `止盈观察`, `移动止盈`, `硬止损`, or `趋势转弱` as labels or rule descriptions
- **THEN** the backend does not reject the output solely for that neutral rule-name usage

#### Scenario: Disclaimer is visible
- **WHEN** the frontend renders LLM-assisted research
- **THEN** it displays that the result is based on public data and model-assisted explanation, does not connect to Alipay or brokers, and is not an automatic trade instruction

### Requirement: LLM Advisor Uses Valid Chinese Prompts And Fallback Text
The system SHALL use readable Chinese prompts, validation messages, fallback reports, and prohibited-language rules for LLM-assisted short-term research.

#### Scenario: Prompt text is readable
- **WHEN** the LLM advisor builds a short-term research request
- **THEN** the system prompt and user-facing fallback text are valid Chinese and do not contain mojibake or replacement-character corruption

#### Scenario: Fallback report is readable
- **WHEN** the LLM is disabled, unavailable, or fails validation
- **THEN** the saved fallback report uses readable Chinese and explains that deterministic rules are being used

#### Scenario: Partial fallback report is readable
- **WHEN** the model output is partially completed by deterministic rules
- **THEN** the saved report uses readable Chinese and explains that part of the explanation was completed by rules

### Requirement: LLM Does Not Control Dynamic Exit Lines
The system SHALL NOT allow LLM output to set, override, or directly trigger tracked-position dynamic stop-loss, take-profit-watch, trailing-profit, or trend-weakening thresholds.

#### Scenario: Model returns stronger trading language
- **WHEN** the model output contains direct buy, sell, stop-loss, take-profit, target price, guaranteed-return, or automatic-trading language
- **THEN** the output is rejected and the deterministic rule fallback is saved instead

#### Scenario: Dynamic lines are shown with AI explanation
- **WHEN** an AI research report is displayed with tracked-position dynamic lines
- **THEN** the UI states that dynamic lines are rule-calculated and AI only explains the evidence and risk

### Requirement: LLM Research Provides Multi-Angle Explanation Without Changing Rules
The system SHALL use LLM output only to explain deterministic ETF or fund research results from multiple angles.

#### Scenario: Report explains deterministic result
- **WHEN** an LLM research report is generated for a ranked asset
- **THEN** the report explains why it ranked, why its current entry timing label applies, main risks, opposing view, watch conditions, and data limitations

#### Scenario: LLM cannot change rule outputs
- **WHEN** the model output disagrees with deterministic score, observation label, timing label, portfolio weight, or holding signal
- **THEN** the backend preserves the deterministic value and stores the model text only as explanation if it passes validation

### Requirement: LLM Report Includes Opposing View And Data Limits
The system SHALL require every accepted LLM report to include a beginner-readable opposing view and explicit data limitation section.

#### Scenario: Opposing view is missing
- **WHEN** the model output omits opposing view or data limitation fields
- **THEN** the system fills them from rule-based fallback and marks the report as partially rule-completed

#### Scenario: Unsafe trading language appears
- **WHEN** the model output contains direct buy/sell instructions, guaranteed return, target price, automatic trading claims, or account-sync claims
- **THEN** the output is rejected and rule-based explanation is displayed instead

### Requirement: AI Source Transparency Is Visible
The system SHALL clearly identify whether each explanation is AI-generated, partially rule-completed, or rule-only.

#### Scenario: User opens asset detail
- **WHEN** the selected asset has a research explanation
- **THEN** the UI displays AI生成, 规则补齐, or 规则说明 and states that AI does not control scores, weights, dynamic lines, or emails

