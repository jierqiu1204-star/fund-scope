## Why

FundScope now has the core MVP and the new recommendation layer, but the original MVP change still has unfinished verification and release-readiness tasks. Closing those gaps is more valuable than adding new features because it turns the project from "locally working" into a demonstrable, reproducible portfolio-ready system.

## What Changes

- Add focused release-readiness work for the MVP:
  - complete missing portfolio and valuation spec coverage
  - resolve valuation-monitoring gaps around index watchlist management and data-source fallback
  - add deterministic tests for external data fallback behavior without requiring live network calls
  - verify the full stack with Docker Compose or document concrete blockers with fixes
  - add a repeatable manual verification checklist for transactions, holdings snapshots, valuation jobs, reminders, news fallback, recommendation jobs, and screenshots
- Keep external environment tasks explicit:
  - GitHub/Gitee/VPS secrets cannot be configured from code, but the repo SHALL document the exact required secrets and verification steps
  - actual VPS deployment remains optional for local-only use, but the deployment path SHALL be runnable when credentials exist
- Do not add investment functionality beyond the existing MVP and recommendation surfaces.

## Capabilities

### New Capabilities

- `mvp-release-readiness`: Defines the verification, release hardening, and operational readiness needed to close the FundScope MVP.

### Modified Capabilities

- None.

## Impact

- **Backend tests**: expanded pytest coverage for portfolio tracking, valuation monitoring, data-source fallback, and job behavior.
- **Backend APIs**: likely small additions for index watchlist add/remove if retained from the valuation spec.
- **Data ingestion**: valuation data source fallback behavior may need to be implemented or the spec narrowed.
- **Deployment docs**: clearer local Docker Compose validation and external secret setup instructions.
- **OpenSpec**: `add-fundscope-mvp` remaining tasks can be completed or explicitly re-scoped after this change is applied.
