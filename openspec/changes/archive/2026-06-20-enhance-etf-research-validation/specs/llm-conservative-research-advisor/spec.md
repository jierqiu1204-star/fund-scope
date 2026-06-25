## ADDED Requirements

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
