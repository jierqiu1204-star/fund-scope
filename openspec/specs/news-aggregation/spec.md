# news-aggregation Specification

## Purpose
TBD - created by archiving change add-fundscope-mvp. Update Purpose after archive.
## Requirements
### Requirement: Fetch daily news and announcements per held fund

The system SHALL retrieve news articles and official announcements for every fund present in the user's portfolio or watchlist on a daily schedule.

#### Scenario: Daily news fetch populates news_items

- **WHEN** the `daily_news_fetch` scheduled job runs at 19:30 on date `D`
- **THEN** the system SHALL fetch news published on `D` for each tracked fund from the primary data source and insert new rows into `news_items` with `fund_code`, `published_at`, `title`, `url`, and `raw_content`

#### Scenario: Skip duplicate articles

- **WHEN** the fetcher returns an article whose `url` already exists in `news_items`
- **THEN** the system SHALL skip insertion rather than creating a duplicate row

#### Scenario: Fund with no news on a given day

- **WHEN** a tracked fund has no published news on date `D`
- **THEN** the system SHALL record a successful-empty outcome for that fund in the job run log, not treat it as an error

### Requirement: Generate LLM summary of each news item

The system SHALL call the configured OpenAI-compatible LLM endpoint to produce a short Chinese-language summary for each new news item.

#### Scenario: Summary generated and persisted

- **WHEN** a new row is inserted into `news_items`
- **THEN** the system SHALL call the LLM with the title and raw content, receive a summary of at most 80 Chinese characters, and insert a row into `news_summaries` referencing the news item with the summary text, model name, and generation timestamp

#### Scenario: Summary prompt prohibits investment advice

- **WHEN** the summary prompt is constructed
- **THEN** it SHALL instruct the model to limit output to event type (分红/基金经理变动/规模变化/策略变更/其他) plus the concrete fact, and explicitly forbid speculation about price impact or investment recommendations

#### Scenario: LLM endpoint failure does not block ingestion

- **WHEN** the LLM call fails (timeout, endpoint error, quota exceeded)
- **THEN** the `news_items` row SHALL remain inserted, no `news_summaries` row is created, the news-aggregation UI SHALL fall back to rendering raw titles only, and the error SHALL be recorded in the job run log

#### Scenario: Summary is retryable

- **WHEN** a news item has no corresponding `news_summaries` row due to a prior LLM failure
- **THEN** a later scheduled run OR manual trigger SHALL pick up the item and re-attempt summary generation without duplicating the news item

### Requirement: Classify news by event type

The system SHALL extract a coarse event type from each summary to enable filtering and highlighting.

#### Scenario: Event type stored alongside summary

- **WHEN** a summary is generated
- **THEN** the system SHALL parse the event type from the LLM output (one of: `dividend`, `manager_change`, `size_change`, `strategy_change`, `other`) and store it on the `news_summaries` row

#### Scenario: Critical events flagged for email

- **WHEN** a news summary has event type `manager_change` or `strategy_change` for a fund the user currently holds
- **THEN** the system SHALL include a highlighted "重要事项" section in the next monthly DCA reminder email listing these events

### Requirement: Display news feed

The system SHALL render a news page at `/news` grouped by fund.

#### Scenario: Default news view

- **WHEN** the user visits `/news`
- **THEN** the system SHALL render one section per tracked fund, each containing the last 14 days of news items sorted descending by publish date, displaying title, publish date, LLM summary (if available), event-type badge, and link to the original article

#### Scenario: Filter by event type

- **WHEN** the user selects an event-type filter (e.g. `dividend`)
- **THEN** the system SHALL display only news items whose summary has the matching event type

#### Scenario: Fund with no summaries falls back to raw

- **WHEN** a fund section contains news items that have no corresponding `news_summaries` rows
- **THEN** the system SHALL render the title and a "摘要生成失败" tag in place of the summary, NOT hide the news item

### Requirement: Expose admin retry endpoint

The system SHALL expose an authenticated admin endpoint to retry summary generation for all news items lacking a summary.

#### Scenario: Retry endpoint processes backlog

- **WHEN** a request is made to `POST /api/admin/jobs/news_summary_backfill/run`
- **THEN** the system SHALL iterate over all `news_items` rows without a corresponding `news_summaries` row, attempt summary generation for each, and return a JSON body with counts of succeeded and failed attempts

### Requirement: ETF Catalyst News Ingestion Records Source Receipts
News ingestion used for ETF catalyst research SHALL preserve immutable source receipts with publication and first-received timestamps, content hashes, parser versions, canonical source identity, and fetch outcome.

#### Scenario: Catalyst-capable news item is fetched
- **WHEN** an approved official or public source item is ingested for ETF catalyst research
- **THEN** the news item references an immutable source receipt that can reconstruct when FundScope first possessed that content

#### Scenario: Content changes at the source
- **WHEN** a previously ingested item is corrected or updated
- **THEN** ingestion stores a new receipt version and retains the earlier content hash and timeline

### Requirement: ETF Catalyst News Ingestion Distinguishes Empty And Unavailable
News ingestion used for ETF catalyst research SHALL distinguish successful-empty source observations from source failures and inapplicable policies.

#### Scenario: Source has no qualifying items
- **WHEN** a required source fetch succeeds for a session and returns no qualifying event items
- **THEN** ingestion records a successful-empty observation eligible for `observed_none`

#### Scenario: Source fetch fails
- **WHEN** a required source fetch times out or fails
- **THEN** ingestion records `unavailable` with a reproducible error summary and MUST NOT report a successful-empty observation

#### Scenario: Source does not apply
- **WHEN** the versioned source/theme policy marks a source inapplicable
- **THEN** ingestion records `not_applicable` with the policy identifier

