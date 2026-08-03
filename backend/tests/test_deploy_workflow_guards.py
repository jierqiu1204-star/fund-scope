import os
import subprocess
import time
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _read_repository_file(path: str) -> str:
    return (REPOSITORY_ROOT / path).read_text(encoding="utf-8")


def _retention_env(backup_dir: Path) -> dict[str, str]:
    return {
        **os.environ,
        "BACKUP_DIR": str(backup_dir),
        "BACKUP_KEEP_COUNT": "3",
        "BACKUP_RETENTION_DAYS": "365",
        "BACKUP_STALE_TMP_MINUTES": "360",
        "BACKUP_MIN_FREE_BYTES": "0",
        "BACKUP_ESTIMATED_BYTES": "0",
    }


def _write_backup(path: Path, *, modified_at: float, sealed: bool = True) -> None:
    path.write_bytes(b"backup")
    os.utime(path, (modified_at, modified_at))
    if sealed:
        checksum = Path(f"{path}.sha256")
        checksum.write_text("test checksum\n", encoding="utf-8")
        os.utime(checksum, (modified_at, modified_at))


def test_compose_backup_is_atomic_and_restore_verified() -> None:
    script = _read_repository_file("deploy/backup-compose.sh")

    assert "--format=custom" in script
    assert 'TMP_PATH="${BACKUP_PATH}.tmp"' in script
    assert 'pg_restore --list < "${TMP_PATH}"' in script
    assert 'mv "${TMP_PATH}" "${BACKUP_PATH}"' in script
    assert 'sha256sum "${BACKUP_PATH}"' in script


def test_backup_retention_is_bounded_and_checks_capacity_before_dump() -> None:
    script = _read_repository_file("deploy/backup-compose.sh")
    retention = _read_repository_file("deploy/backup-retention.sh")

    lock = script.index("backup_acquire_lock")
    stale_cleanup = script.index("backup_cleanup_stale_artifacts")
    pre_prune = script.index("backup_prune_completed")
    capacity = script.index("backup_require_capacity")
    dump = script.index("pg_dump")
    post_prune = script.rindex("backup_prune_completed")

    assert lock < stale_cleanup < pre_prune < capacity < dump < post_prune
    assert "BACKUP_KEEP_COUNT=${BACKUP_KEEP_COUNT:-3}" in retention
    assert "BACKUP_RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-7}" in retention
    assert (
        "BACKUP_STALE_TMP_MINUTES=${BACKUP_STALE_TMP_MINUTES:-360}"
        in retention
    )
    assert (
        "BACKUP_MIN_FREE_BYTES=${BACKUP_MIN_FREE_BYTES:-2147483648}"
        in retention
    )
    assert (
        "BACKUP_ESTIMATED_BYTES=${BACKUP_ESTIMATED_BYTES:-3221225472}"
        in retention
    )
    assert 'if [ "${index}" -eq 1 ]' in retention
    assert "Another FundScope backup is already running" in retention
    assert "Insufficient backup capacity" in retention


def test_backup_retention_keeps_three_newest_pairs_and_cleans_stale_files(
    tmp_path: Path,
) -> None:
    now = time.time()
    for index in range(5):
        _write_backup(
            tmp_path / f"fundscope_20260803_00000{index}.dump",
            modified_at=now - index * 60,
        )
    stale_tmp = tmp_path / "fundscope_20260731_000000.dump.tmp"
    stale_tmp.write_bytes(b"partial")
    os.utime(stale_tmp, (now - 8 * 3600, now - 8 * 3600))
    stale_unsealed = tmp_path / "fundscope_20260731_000001.dump"
    _write_backup(stale_unsealed, modified_at=now - 8 * 3600, sealed=False)

    retention_script = REPOSITORY_ROOT / "deploy/backup-retention.sh"
    command = (
        f'. "{retention_script}"; '
        "backup_validate_retention_config; "
        "backup_cleanup_stale_artifacts; "
        'backup_prune_completed "$BACKUP_KEEP_COUNT"'
    )
    subprocess.run(
        ["sh", "-c", command],
        check=True,
        env=_retention_env(tmp_path),
        capture_output=True,
        text=True,
    )

    remaining = sorted(path.name for path in tmp_path.glob("fundscope_*.dump"))
    assert remaining == [
        "fundscope_20260803_000000.dump",
        "fundscope_20260803_000001.dump",
        "fundscope_20260803_000002.dump",
    ]
    assert not stale_tmp.exists()
    assert not stale_unsealed.exists()
    assert all(Path(f"{tmp_path / name}.sha256").is_file() for name in remaining)


def test_backup_retention_fails_closed_when_capacity_is_insufficient(
    tmp_path: Path,
) -> None:
    retention_script = REPOSITORY_ROOT / "deploy/backup-retention.sh"
    env = _retention_env(tmp_path)
    env["BACKUP_ESTIMATED_BYTES"] = "999999999999999"

    completed = subprocess.run(
        ["sh", "-c", f'. "{retention_script}"; backup_require_capacity'],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "Insufficient backup capacity" in completed.stderr


def test_manual_backup_uses_the_same_atomic_retention_contract() -> None:
    script = _read_repository_file("deploy/backup.sh")

    assert '. "${SCRIPT_DIR}/backup-retention.sh"' in script
    assert "backup_require_capacity" in script
    assert 'TMP_PATH="${BACKUP_PATH}.tmp"' in script
    assert 'pg_dump --format=custom "${DATABASE_URL}"' in script
    assert 'pg_restore --list < "${TMP_PATH}"' in script
    assert 'backup_prune_completed "${BACKUP_KEEP_COUNT}"' in script


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
    assert "VPS_BACKUP_KEEP_COUNT || '3'" in workflow
    assert "VPS_BACKUP_MIN_FREE_BYTES || '2147483648'" in workflow


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
