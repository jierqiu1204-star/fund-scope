## 1. Locate Documentation

- [x] 1.1 Inspect existing deployment documentation and choose the primary local/IP deployment guide to update.
- [x] 1.2 Confirm the chosen document already belongs to the Git-tracked docs/deploy surface and does not require adding secret material.

## 2. Document Local Build Troubleshooting

- [x] 2.1 Add a section for Windows/Codex `corepack pnpm build:static` hangs, including symptoms, observation window, stale process cleanup, and server Docker build verification.
- [x] 2.2 Add safe PowerShell 7 examples for nested `pwsh -Command` usage, including `$env:*` quoting guidance.
- [x] 2.3 Clarify that sandbox-blocked local static builds do not replace required targeted tests, TypeScript checks, or server build verification.

## 3. Document Server Deployment Fallback

- [x] 3.1 Add a Git/SSH troubleshooting section explaining when to use normal Git pull versus archive upload deployment.
- [x] 3.2 Document the archive upload fallback flow: local archive creation, upload, server backup, extraction, version stamp, Docker rebuild, migration, and health verification.
- [x] 3.3 Document server-only file preservation requirements for `.env`, `deploy/.htpasswd`, and other local-only deployment files.

## 4. Verification

- [x] 4.1 Add a deployment verification checklist covering Docker container status, Alembic head, `/api/health`, and key web pages.
- [x] 4.2 Review the updated document to ensure it contains no passwords, API keys, SMTP authorization codes, private keys, or host-specific secrets beyond already documented public hostnames/IPs.
- [x] 4.3 Run `openspec status --change document-local-deployment-troubleshooting` and confirm the change is apply-ready.
