## Why

Recent deployments exposed repeatable local and server-side pitfalls: Windows sandbox process hangs during static builds, PowerShell quoting issues, and server deployments requiring a tar-based fallback instead of direct Git pull. These issues should be captured in the project deployment documentation so future deployments do not repeat the same troubleshooting loop.

## What Changes

- Add a local deployment troubleshooting section to the project deployment documentation.
- Document the known Windows/Codex static build hang pattern and the safer verification/deployment path.
- Document the PowerShell 7 command quoting rules that avoid `$env` and `$_` being expanded by the outer shell.
- Document the server deployment fallback used when Git/SSH access is unreliable: archive locally, upload tarball, preserve server-only config, rebuild Docker, run migrations, and verify health.
- Document the verification checklist for backend, frontend, migration, container, and `/api/health` checks.
- No API, database, investment logic, scheduler, alert, or UI behavior changes.

## Capabilities

### New Capabilities
- `local-deployment-troubleshooting`: Deployment documentation must describe known local build, shell, sandbox, SSH/Git, and server verification issues with safe recovery steps.

### Modified Capabilities

## Impact

- Documentation only.
- Expected documentation targets include deployment docs such as `deploy/README-ip.md`, `docs/`, or another existing local deployment guide discovered during implementation.
- No runtime code, API, database schema, financial calculation, or production behavior changes.
