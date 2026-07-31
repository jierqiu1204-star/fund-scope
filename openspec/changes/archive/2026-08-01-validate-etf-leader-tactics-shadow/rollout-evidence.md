# Production Rollout Evidence

Recorded on 2026-08-01 in Asia/Shanghai.

## Deployment

- GitHub deployment branch advanced by fast-forward from `98e6420` to implementation commit `edeeaac`, followed by low-resource deployment guard commit `78b7e61`.
- The self-hosted `Deploy` workflow completed with result `Succeeded` at 2026-08-01 00:05:38 Asia/Shanghai.
- `/srv/fundscope/.deploy-version` is `78b7e61d99ddea7f9d0516c872256fbd1bbcbc7c`.
- Alembic has one head, `20260731_000058`; nullable columns `experiment_family` and `hypothesis_registry_hash` exist on `etf_factor_experiment_evidence`.
- `GET http://127.0.0.1/api/health` returned `{"app":"ok","db":"ok"}`.
- Backend, nginx, and PostgreSQL containers were all running after deployment.

## Disabled Production Surface

- `ETF_LEADER_TACTICS_CONTINUATION_ENABLED=false`.
- `ETF_LEADER_TACTICS_EVIDENCE_API_ENABLED=false`.
- `ETF_LEADER_TACTICS_CODE_VERSION` remains unset in the production service.
- No scheduler registration was added for the leader continuation.
- The production candidate registry hash remained `eb06cb60c325b08dd380ddca4bbc84b6a0f6bdfe59312eb0df53bd10b717f42b`.
- The separate three-candidate leader registry hash is `aa8c2c167e64e9ee03cd1dc78dd2a4bb45c97654c2366b4bfe24565c60c91546` and does not replace the production registry.

## One Bounded Real-Data Check

Exactly one one-off process temporarily enabled discovery for the process only. It returned:

```json
{
  "advanced_pages": 0,
  "eligible_pit_sessions": 0,
  "live_provider_calls": 0,
  "production_mutation_allowed": false,
  "required_pit_sessions": 252,
  "status": "insufficient_data",
  "unavailable_reason": "leader_tactics_requires_252_pit_sessions"
}
```

No second continuation was run. The persistent service flags were re-read after the process and remained disabled.

## No-Side-Effect Comparison

The aggregate production snapshot was identical before and after the bounded check:

| Surface | Before | After |
| --- | ---: | ---: |
| Short-research signal runs | 118 | 118 |
| Allocation snapshots | 2 | 2 |
| Tracked positions | 29 | 29 |
| Risk alerts | 2360 | 2360 |
| Notification logs | 216 | 216 |
| Factor evidence rows | 0 | 0 |
| Factor checkpoints | 0 | 0 |

SMTP remained configured, but the workflow imports no notifier and produced no notification row. With zero leader evidence and zero checkpoint rows, neither holdout state nor production action state was consumed or mutated.

## Rollback Record

- Previous deploy: `1b156b8c4210350890ebe7446286383270f8c0e8`.
- Previous schema head: `20260727_000056`.
- Validated database backup: `/var/backups/fundscope/fundscope_20260731_235313.dump`.
- Rollback metadata: `/var/backups/fundscope/rollback-metadata-20260731_235311.txt`.

Validate the rollback material before any incident response:

```sh
sudo sha256sum -c /var/backups/fundscope/fundscope_20260731_235313.dump.sha256
cd /srv/fundscope/deploy
sudo docker compose -f docker-compose.ip.yml exec -T postgres pg_restore --list \
  < /var/backups/fundscope/fundscope_20260731_235313.dump > /dev/null
```

Actual source rollback and database restoration remain explicit, manually approved incident-response actions. Redeploy the recorded previous SHA and restore the validated custom-format dump together; never downgrade only the code or only the database.
