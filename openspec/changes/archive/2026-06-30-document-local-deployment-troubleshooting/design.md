## Context

FundScope is deployed from a Windows development machine to an Ubuntu server using Docker Compose. Recent deployments showed several recurring failure modes:

- Local `corepack pnpm build:static` can hang under the Windows/Codex sandbox even when the same build succeeds inside the Linux Docker build.
- PowerShell command strings can be misquoted when nesting `pwsh -Command`, especially around `$env:*` and `$_`.
- Server Git or SSH access can be unreliable, so deployment sometimes needs a local archive upload path.
- Server-only secrets and config files must be preserved during archive-based deployment.

The project already has deployment docs, but these operational lessons are not centralized enough for future maintenance.

## Goals / Non-Goals

**Goals:**

- Document known local deployment and verification pitfalls in the project deployment docs.
- Provide a conservative decision tree for local build hangs: observe briefly, terminate stale processes, verify with targeted checks, and rely on server Docker build when local static build is blocked by the sandbox.
- Document safe PowerShell 7 quoting examples for nested commands.
- Document the archive upload deployment fallback and required server-side preservation rules.
- Document deployment verification commands and expected outcomes.

**Non-Goals:**

- No code changes.
- No deployment script rewrite.
- No CI/CD implementation.
- No change to Docker Compose, nginx, database, financial logic, scheduler, or API behavior.
- No secret material should be added to Git-tracked documentation.

## Decisions

- Use existing deployment documentation rather than adding a new standalone guide.
  - Rationale: deployment guidance should be discovered where the user already looks.
  - Alternative considered: add a new `docs/deployment-troubleshooting.md`; this risks splitting deployment instructions across too many files unless existing docs become too large.

- Document symptoms and recovery steps rather than claiming a single root cause for local static build hangs.
  - Rationale: the observed hang involves Windows, Codex sandboxing, PowerShell, Corepack, pnpm, Node, and Next.js. The safe operator action is more important than overfitting the explanation.
  - Alternative considered: remove local build verification entirely; rejected because local type checks and targeted tests remain useful.

- Keep the archive upload fallback as an explicit fallback path, not the default path.
  - Rationale: normal Git-based deployment is simpler when SSH/Git access is healthy, but the archive path is necessary when server credentials or remote Git state block deployment.
  - Alternative considered: always deploy by archive; rejected because it bypasses normal Git traceability unless carefully version-stamped.

- Preserve strict financial deployment verification.
  - Rationale: a successful container build is not enough. The deployment guide must require migrations, health checks, and targeted page/API checks before considering the deployment complete.

## Risks / Trade-offs

- Documentation may drift from real deployment practice.
  - Mitigation: keep steps concrete and command-oriented, and update after future deployment incidents.

- Archive-based deployment can overwrite server files if used carelessly.
  - Mitigation: document the required backup and preservation of `.env`, `.htpasswd`, and server-only local files.

- Local build hang guidance could be misread as permission to skip all frontend verification.
  - Mitigation: clearly distinguish sandbox-blocked static build from required TypeScript checks and server Docker build verification.
