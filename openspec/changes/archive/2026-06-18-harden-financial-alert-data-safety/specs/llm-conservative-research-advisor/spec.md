## MODIFIED Requirements

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
