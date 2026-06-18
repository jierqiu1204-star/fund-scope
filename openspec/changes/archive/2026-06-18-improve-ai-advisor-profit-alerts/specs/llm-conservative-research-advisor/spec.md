## ADDED Requirements

### Requirement: LLM Advisor Uses Valid Chinese Prompts And Fallback Text
The system SHALL use readable Chinese prompts, validation messages, fallback reports, and prohibited-language rules for LLM-assisted short-term research.

#### Scenario: Prompt text is readable
- **WHEN** the LLM advisor builds a short-term research request
- **THEN** the system prompt and user-facing fallback text are valid Chinese and do not contain mojibake or replacement-character corruption

#### Scenario: Fallback report is readable
- **WHEN** the LLM is disabled, unavailable, or fails validation
- **THEN** the saved fallback report uses readable Chinese and explains that deterministic rules are being used

### Requirement: LLM Does Not Control Dynamic Exit Lines
The system SHALL NOT allow LLM output to set, override, or directly trigger tracked-position dynamic stop-loss, take-profit-watch, trailing-profit, or trend-weakening thresholds.

#### Scenario: Model returns stronger trading language
- **WHEN** the model output contains direct buy, sell, stop-loss, take-profit, target price, guaranteed-return, or automatic-trading language
- **THEN** the output is rejected and the deterministic rule fallback is saved instead

#### Scenario: Dynamic lines are shown with AI explanation
- **WHEN** an AI research report is displayed with tracked-position dynamic lines
- **THEN** the UI states that dynamic lines are rule-calculated and AI only explains the evidence and risk

