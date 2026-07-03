# ETF Theme Catalyst Scoring

ETF theme catalyst scoring adds a research-attention layer on top of the existing short-term technical score.

## Score Contract

- `total_score` remains the technical short-term score.
- `opportunity_score` is the research attention score.
- Default v1 formula:
  - technical score: 70%
  - theme catalyst score: 20%
  - news/sentiment heat score: 10%

Theme catalysts can raise attention, but they cannot remove risk labels such as `冲高别追`, `数据不足`, `数据滞后`, or `流动性不足`.

## Data Source

v1 uses manual seed events for robotics, semiconductor/chip, and optical-module/CPO proxy themes. Each event has a source URL, event date, effective window, strength score, confidence score, and status.

Run the admin job to refresh snapshots:

```text
POST /api/admin/jobs/daily_etf_theme_catalyst/run
```

Then regenerate ETF short-research signals:

```text
POST /api/admin/jobs/daily_short_research_signals/run
```

## AI Boundary

LLM output is not used in deterministic scoring. Future AI extraction may create pending candidate events, but pending or unverified events must not raise `catalyst_score` or `opportunity_score`.

AI may explain structured evidence. It must not change scores, labels, portfolio weights, dynamic thresholds, risk alerts, or notification triggers.

## Rollback

The old `total_score` and existing labels remain compatible. If catalyst refresh fails, API responses keep existing ranking fields and show catalyst data as unavailable or neutral.
