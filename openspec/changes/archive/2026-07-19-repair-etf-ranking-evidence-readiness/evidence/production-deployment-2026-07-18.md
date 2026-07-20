# Production deployment evidence — 2026-07-18

This evidence covers task 9.4 only. It does not authorize or satisfy signed
readiness, synchronization, shadow publication, rollout-session, or formal
return-evidence tasks 9.5–9.9.

## Deployment identity

- Git commit: `5c3900b521b35e2daf391027af723add7c4d20ab`
- GitHub Actions run:
  `https://github.com/jierqiu1204-star/fund-scope/actions/runs/29643818515`
- Workflow/job conclusion: `success`
- VPS deploy step completed at `2026-07-18T12:16:23Z`.

## Pre-migration gates and rollback point

- The candidate image passed the workflow's exactly-one Alembic-head check
  before live source replacement.
- The production database passed the exactly-one current-revision check. The
  migration log proves its previous head was `20260715_000046`.
- The PostgreSQL custom-format backup was created at
  `/var/backups/fundscope/fundscope_20260718_201133.dump`.
- `backup-compose.sh` validated the dump with `pg_restore --list`; the workflow
  then verified the sidecar checksum and logged
  `/var/backups/fundscope/fundscope_20260718_201133.dump: OK`.
- Rollback metadata containing the previous deploy SHA/schema head, backup path,
  candidate SHA/head, deployed head, and health result was retained under
  `/var/backups/fundscope/rollback-metadata-*.txt` and recorded in the workflow
  step summary.

## Migration and health

- Alembic applied the single chain `20260715_000046` through revisions 47–51.
- The workflow required the deployed database to contain exactly one revision
  equal to candidate head `20260717_000051`.
- The internal deployment health gate required both application and database
  status to be `ok` before writing `.deploy-version`.
- A separate external read-only check of `http://taslr2.xyz/api/health` returned
  `{"app":"ok","db":"ok"}` after the workflow completed.

No ETF synchronization, shadow generation, ranking publication, alert, or
notification work was started as part of this deployment verification.
