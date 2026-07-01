# local-deployment-troubleshooting Specification

## Purpose
TBD - created by archiving change document-local-deployment-troubleshooting. Update Purpose after archive.
## Requirements
### Requirement: Document local static build hang recovery
The deployment documentation SHALL describe how to handle local Windows/Codex static build hangs without misdiagnosing them as application failures.

#### Scenario: Local static build produces no output for several minutes
- **WHEN** `corepack pnpm build:static` hangs locally under Windows/Codex for longer than the documented observation window
- **THEN** the documentation explains how to stop stale Node/Corepack processes, continue with targeted local checks, and verify the final static build in the Linux Docker deployment path

### Requirement: Document PowerShell 7 command quoting rules
The deployment documentation SHALL include safe PowerShell 7 examples for nested commands used during local verification and deployment.

#### Scenario: Nested PowerShell command uses environment variables
- **WHEN** a deployment command invokes `pwsh -Command` from another PowerShell session and sets `$env:*`
- **THEN** the documentation shows quoting that prevents the outer shell from expanding the inner command too early

### Requirement: Document SSH and Git fallback deployment
The deployment documentation SHALL describe a safe archive upload fallback for cases where direct server Git/SSH deployment is blocked or unreliable.

#### Scenario: Server cannot pull the target Git commit directly
- **WHEN** server-side Git access, remote credentials, or SSH setup blocks a normal `git pull` deployment
- **THEN** the documentation provides an archive upload flow that preserves server-only config, records the deployed commit, rebuilds Docker Compose, runs migrations, and verifies service health

### Requirement: Preserve server-only deployment files
The deployment documentation SHALL warn that server-only secrets and authentication files must not be overwritten or committed.

#### Scenario: Archive deployment replaces the server project directory
- **WHEN** an archive-based deployment updates `/srv/fundscope`
- **THEN** the documentation requires preserving `.env`, `deploy/.htpasswd`, and any other documented server-only files before rebuilding services

### Requirement: Document deployment verification checklist
The deployment documentation SHALL include a verification checklist covering backend health, database migrations, container status, and key web routes.

#### Scenario: Deployment command finishes successfully
- **WHEN** Docker Compose rebuild and migration commands finish
- **THEN** the documentation requires checking container status, `/api/health`, Alembic head, and the key FundScope pages before declaring the deployment complete

