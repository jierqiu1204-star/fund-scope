## ADDED Requirements

### Requirement: ETF Theme Profiles Are Normalized
The system SHALL maintain a normalized sector/theme profile for every tradable ETF record in the research universe.

#### Scenario: ETF is classified with high confidence
- **WHEN** an ETF name, index name, category, or manual override maps clearly to a known theme
- **THEN** the system stores asset bucket, theme group, primary theme, optional secondary themes, classification source, classification confidence, and a readable classification reason

#### Scenario: ETF cannot be classified confidently
- **WHEN** an ETF cannot be mapped to a known theme with deterministic evidence
- **THEN** the system stores it as `未分类` with low or unknown confidence rather than inventing a confident theme

### Requirement: Theme Taxonomy Refresh Is Idempotent
The system SHALL provide a web-runnable and scheduler-runnable taxonomy refresh that updates ETF theme profiles without duplicating records.

#### Scenario: Taxonomy refresh runs repeatedly
- **WHEN** the taxonomy refresh job is run multiple times for the same ETF universe
- **THEN** existing theme profiles are updated in place and no duplicate profile rows are created

#### Scenario: New ETF appears
- **WHEN** a newly discovered ETF exists in metadata but has no theme profile
- **THEN** the refresh job creates a theme profile or marks it as `未分类` with a readable reason

### Requirement: Theme Coverage Quality Is Visible
The system SHALL expose theme coverage quality so users and admins can see how much of the ETF universe is classified.

#### Scenario: Coverage status is requested
- **WHEN** the frontend or admin jobs page requests short-term ETF status
- **THEN** the response includes total ETF count, classified count, unknown count, low-confidence count, and latest taxonomy refresh time

### Requirement: Theme Heat Statistics Are Generated
The system SHALL generate theme heat statistics from cached ETF ranking results and latest quote freshness without recomputing all ETF history during page load.

#### Scenario: Theme heat is available
- **WHEN** a latest ETF signal run exists
- **THEN** the system returns theme-level counts, average score, top score, top ETF, average latest change where available, and data freshness summary

#### Scenario: No signal cache exists
- **WHEN** no latest ETF signal run exists
- **THEN** the system returns an empty or waiting state and does not compute full ETF metrics synchronously

### Requirement: Theme Labels Are Research Metadata
The system SHALL present industry/theme labels as research metadata and SHALL NOT use them as standalone buy or sell instructions.

#### Scenario: Theme is displayed in UI
- **WHEN** an ETF card, detail view, heat panel, or portfolio explanation displays a theme
- **THEN** the UI states or implies only exposure classification and does not present the theme label itself as a buy recommendation

### Requirement: Ambiguous Theme Classification Is Auditable
The system SHALL retain enough evidence for ambiguous classifications to be reviewed and corrected.

#### Scenario: ETF has medium or low confidence
- **WHEN** an ETF theme profile has medium, low, or unknown confidence
- **THEN** the API exposes the classification source and reason so the UI or admin diagnostics can explain why it was classified that way
