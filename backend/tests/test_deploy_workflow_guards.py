from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _read_repository_file(path: str) -> str:
    return (REPOSITORY_ROOT / path).read_text(encoding="utf-8")


def test_compose_backup_is_atomic_and_restore_verified() -> None:
    script = _read_repository_file("deploy/backup-compose.sh")

    assert "--format=custom" in script
    assert 'TMP_PATH="${BACKUP_PATH}.tmp"' in script
    assert 'pg_restore --list < "${TMP_PATH}"' in script
    assert 'mv "${TMP_PATH}" "${BACKUP_PATH}"' in script
    assert 'sha256sum "${BACKUP_PATH}"' in script


def test_deploy_backs_up_before_replacing_source() -> None:
    workflow = _read_repository_file(".github/workflows/deploy.yml")

    candidate_head = workflow.index("candidate_head_count")
    backup = workflow.index("backup-compose.sh")
    source_replace = workflow.index('"$GITHUB_WORKSPACE"/ "$deploy_dir"/')
    assert candidate_head < backup < source_replace
    assert 'test -f "$deploy_dir/deploy/$COMPOSE_FILE"' in workflow
    assert "sha256sum -c" in workflow
    assert "previous_deploy_sha" in workflow
    assert "previous_schema_head" in workflow
    assert "previous_revision_count" in workflow
    assert "rollback-metadata" in workflow


def test_deploy_checks_single_head_before_migration_and_health_afterward() -> None:
    workflow = _read_repository_file(".github/workflows/deploy.yml")

    single_head = workflow.index("candidate_head_count")
    database_ready = workflow.index("pg_isready")
    migration = workflow.index("alembic upgrade head")
    schema_health = workflow.index("deployed_schema_head")
    http_health = workflow.rindex("curl --fail")

    assert single_head < database_ready < migration < schema_health < http_health
    assert 'test "$candidate_head_count" -eq 1' in workflow
    assert 'test "$previous_revision_count" -eq 1' in workflow
    assert 'test "$deployed_revision_count" -eq 1' in workflow
    assert 'test "$deployed_schema_head" = "$candidate_schema_head"' in workflow
    assert "http://127.0.0.1/api/health" in workflow
