import io
import os
import subprocess
import tarfile
import textwrap
import time
from pathlib import Path

import pytest

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
    assert 'BACKUP_COMPRESSION=${BACKUP_COMPRESSION:-zstd:1}' in script
    assert '--compress="${BACKUP_COMPRESSION}"' in script
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
    assert 'BACKUP_COMPRESSION=${BACKUP_COMPRESSION:-zstd:1}' in script
    assert 'TMP_PATH="${BACKUP_PATH}.tmp"' in script
    assert 'pg_dump --format=custom --compress="${BACKUP_COMPRESSION}"' in script
    assert 'pg_restore --list < "${TMP_PATH}"' in script
    assert 'backup_prune_completed "${BACKUP_KEEP_COUNT}"' in script


@pytest.mark.parametrize("archive_state", ["download_failed", "corrupt", "incomplete", "complete"])
def test_deploy_source_archive_replaces_workspace_only_after_validation(
    tmp_path: Path, archive_state: str
) -> None:
    workflow = _read_repository_file(".github/workflows/deploy.yml")
    script = textwrap.dedent(
        workflow.split("        run: |\n", 1)[1].split("\n      - name: Deploy on VPS", 1)[0]
    )
    archive = tmp_path / "fixture.tar.gz"
    if archive_state == "corrupt":
        archive.write_bytes(b"incomplete download")
    else:
        with tarfile.open(archive, "w:gz") as bundle:
            files = ["frontend/package.json", "deploy/backup-compose.sh"]
            if archive_state == "complete":
                files.append("backend/Dockerfile")
            for name in files:
                entry = tarfile.TarInfo(f"source-commit/{name}")
                entry.size = 6
                bundle.addfile(entry, io.BytesIO(b"source"))
    commands = tmp_path / "bin"
    commands.mkdir()
    curl = commands / "curl"
    curl.write_text(
        '#!/bin/sh\ncase "$*" in *"/tarball/$GITHUB_SHA"*) ;; *) exit 65;; esac\n'
        'test "$ARCHIVE_STATE" != download_failed || exit 22\n'
        'while [ "$#" -gt 1 ]; do shift; done\ncp "$ARCHIVE_FIXTURE" "$1"\n',
        encoding="utf-8",
    )
    curl.chmod(0o755)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    old_source = workspace / "old-source"
    old_source.write_text("previous checkout", encoding="utf-8")
    (workspace / ".git").mkdir()
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    result = subprocess.run(
        ["bash", "-c", script],
        env={
            **os.environ,
            "PATH": f"{commands}{os.pathsep}{os.environ['PATH']}",
            "RUNNER_TEMP": str(runner_temp),
            "GITHUB_WORKSPACE": str(workspace),
            "GITHUB_REPOSITORY": "fixture/fund-scope",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_TOKEN": "fixture-token",
            "ARCHIVE_FIXTURE": str(archive),
            "ARCHIVE_STATE": archive_state,
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    if archive_state == "complete":
        assert result.returncode == 0, result.stderr
        assert (workspace / "backend/Dockerfile").read_text() == "source"
        assert not old_source.exists()
        assert not (workspace / ".git").exists()
    else:
        assert result.returncode != 0
        assert old_source.read_text() == "previous checkout"
        assert (workspace / ".git").exists()
    assert list(runner_temp.iterdir()) == []


def test_deploy_backs_up_before_replacing_source() -> None:
    workflow = _read_repository_file(".github/workflows/deploy.yml")

    candidate_head = workflow.index("candidate_head_count")
    backup = workflow.index('sh "$GITHUB_WORKSPACE/deploy/backup-compose.sh"')
    source_replace = workflow.index('"$GITHUB_WORKSPACE"/ "$deploy_dir"/')
    assert candidate_head < backup < source_replace
    assert 'test -f "$deploy_dir/deploy/$COMPOSE_FILE"' in workflow
    assert "sha256sum -c" in workflow
    assert "previous_deploy_sha" in workflow
    assert "previous_schema_head" in workflow
    assert "previous_revision_count" in workflow
    assert "rollback-metadata" in workflow
    assert "VPS_BACKUP_KEEP_COUNT || '2'" in workflow
    assert "VPS_BACKUP_MIN_FREE_BYTES || '2147483648'" in workflow
    assert "VPS_BACKUP_ESTIMATED_BYTES || '5368709120'" in workflow
    assert "VPS_BACKUP_COMPRESSION || 'zstd:1'" in workflow
    assert 'BACKUP_COMPRESSION="$BACKUP_COMPRESSION"' in workflow
    assert "docker builder prune -af" in workflow


@pytest.mark.parametrize("frontend_changed", [True, False])
@pytest.mark.parametrize("builder_referenced", [True, False])
@pytest.mark.parametrize("candidate_valid", [True, False])
def test_deploy_reclaims_cache_and_cleans_candidate_when_backup_has_no_capacity(
    tmp_path: Path, frontend_changed: bool, builder_referenced: bool, candidate_valid: bool,
) -> None:
    workflow = _read_repository_file(".github/workflows/deploy.yml")
    script = textwrap.dedent(
        workflow.split("      - name: Deploy on VPS\n        run: |\n", 1)[1]
    )
    deploy_dir = tmp_path / "deployed"
    (deploy_dir / "deploy").mkdir(parents=True)
    (deploy_dir / "deploy/docker-compose.ip.yml").write_text("previous compose\n")
    previous_source = deploy_dir / "previous-source"
    previous_source.write_text("previous deployment\n")
    workspace = tmp_path / "workspace"
    (workspace / "backend").mkdir(parents=True)
    (workspace / "deploy").mkdir()
    (deploy_dir / "frontend").mkdir()
    (deploy_dir / "frontend/index.html").write_text("previous frontend")
    (workspace / "frontend").mkdir()
    (workspace / "frontend/index.html").write_text(
        "new frontend" if frontend_changed else "previous frontend"
    )
    for name in ("backup-compose.sh", "backup-retention.sh"):
        (workspace / "deploy" / name).write_text(
            _read_repository_file(f"deploy/{name}"), encoding="utf-8"
        )
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    previous_backup = backup_dir / "fundscope_20261001_000000.dump"
    _write_backup(previous_backup, modified_at=time.time())
    script = script.replace("/srv/fundscope", str(deploy_dir)).replace(
        "/var/backups/fundscope", str(backup_dir)
    )
    commands = tmp_path / "bin"
    commands.mkdir()
    mocks = {
        "sudo": '#!/bin/sh\nprintf "%s\\n" "$*" >> "$COMMAND_LOG"\nexec "$@"\n',
        "docker": textwrap.dedent(r"""
            #!/bin/sh
            case "$*" in
              "image prune -f"|"builder prune -af"|"image rm fundscope-"*|"image rm deploy-frontend-static-builder:latest") ;;
              "image ls --filter reference=fundscope-* --format {{.Repository}}:{{.Tag}}")
                printf 'fundscope-backend-candidate:older\nfundscope-backend-candidate:previous\nfundscope-v2-research:unused\nfundscope-v2-cache:active\nfundscope-v2-research:stopped\nfundscope-v2-research:base\n' ;;
              "ps -aq --filter ancestor=fundscope-v2-cache:active") printf 'active-container\n' ;;
              "ps -aq --filter ancestor=fundscope-v2-research:stopped") printf 'stopped-container\n' ;;
              "ps -aq --filter ancestor=fundscope-v2-research:base") printf 'descendant-container\n' ;;
              "ps -aq --filter ancestor=fundscope-"*) ;;
              "ps -aq --filter ancestor=deploy-frontend-static-builder:latest")
                if [ "$BUILDER_REFERENCED" = true ]; then printf 'builder-container\n'; fi ;;
              "ps -a --format table {{.Names}}\t{{.Image}}\t{{.Status}}")
                printf 'fixture-active fundscope-v2-cache:active Up 1 hour\nfixture-stopped fundscope-v2-research:stopped Exited (0) 1 hour ago\n' ;;
              "image ls --format table "*) printf 'other-project keep fixture-image 1GB\n' ;;
              "build --tag "*) ;;
              "run --rm --entrypoint alembic "*) printf '%s\n' "$CANDIDATE_HEADS" ;;
              "system df") printf 'Build Cache: fixture reclaimable\n' ;;
              "compose -f docker-compose.ip.yml up -d postgres") ;;
              *"pg_isready -U fundscope -d fundscope") printf 'accepting connections\n' ;;
              *"SELECT count(*) FROM alembic_version") printf '1\n' ;;
              *"SELECT version_num FROM alembic_version") printf 'previous\n' ;;
              *"SELECT pg_database_size(current_database())") printf '3000000000\n' ;;
              *"FROM pg_stat_user_tables ORDER BY total_bytes DESC") printf 'public | fixture_table | 3000000000 | 2000000000 | 2000000000 | 1000000000 | 100 | 50\n' ;;
              *) printf 'Unexpected Docker command: %s\n' "$*" >&2; exit 64 ;;
            esac
            """).lstrip(),
        "df": "#!/bin/sh\nprintf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\nfixture 10000000 5194868 4805132 52%% /\\n'\n",
        "flock": "#!/bin/sh\nexit 0\n",
        "find": '#!/bin/sh\ncase "$*" in *-printf*) printf "Backup fundscope_20261001_000000.dump: 6 bytes\\n" ;; esac\n',
    }
    for name, content in mocks.items():
        command = commands / name
        command.write_text(content, encoding="utf-8")
        command.chmod(0o755)
    command_log = tmp_path / "commands.log"
    result = subprocess.run(
        ["bash", "-c", script],
        env={
            **_retention_env(backup_dir),
            "PATH": f"{commands}{os.pathsep}{os.environ['PATH']}",
            "COMMAND_LOG": str(command_log),
            "COMPOSE_FILE": "docker-compose.ip.yml",
            "GITHUB_WORKSPACE": str(workspace),
            "GITHUB_SHA": "a" * 40,
            "BACKUP_KEEP_COUNT": "2",
            "BACKUP_MIN_FREE_BYTES": "2147483648",
            "BACKUP_ESTIMATED_BYTES": "5368709120",
            "BACKUP_COMPRESSION": "zstd:1",
            "BUILDER_REFERENCED": "true" if builder_referenced else "false",
            "CANDIDATE_HEADS": "candidate (head)" if candidate_valid else "one (head)\ntwo (head)",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 1
    log = command_log.read_text(encoding="utf-8").splitlines()
    candidate_remove = f"docker image rm fundscope-backend-candidate:{'a' * 40}"
    if not candidate_valid:
        assert "Insufficient backup capacity" not in result.stderr
        assert not any(command.endswith("/deploy/backup-compose.sh") for command in log)
        assert log[-1] == candidate_remove
        assert log.count(candidate_remove) == 1
        assert log.count("docker builder prune -af") == 1
        assert previous_source.read_text() == "previous deployment\n"
        assert previous_backup.read_bytes() == b"backup"
        return
    assert "Insufficient backup capacity: available=4805132KB required=7340032KB" in result.stderr
    assert "Build Cache: fixture reclaimable" in result.stderr
    assert "PostgreSQL database size (bytes): 3000000000" in result.stderr
    assert "other-project keep fixture-image 1GB" in result.stderr
    assert "fixture-stopped fundscope-v2-research:stopped Exited (0)" in result.stderr
    assert "public | fixture_table | 3000000000" in result.stderr
    assert "fixture" not in result.stdout
    build_index = next(index for index, command in enumerate(log) if command.startswith("docker build "))
    backup_index = next(index for index, command in enumerate(log) if command.endswith("/deploy/backup-compose.sh"))
    assert log.index("docker image prune -f") < build_index < backup_index
    assert log.index("docker builder prune -af") < build_index
    heads_index = next(index for index, command in enumerate(log) if command.startswith("docker run --rm --entrypoint alembic "))
    assert heads_index < log.index(candidate_remove) < backup_index
    assert log.index("docker builder prune -af", log.index(candidate_remove)) < backup_index
    builder_remove = "docker image rm deploy-frontend-static-builder:latest"
    builder_check = "docker ps -aq --filter ancestor=deploy-frontend-static-builder:latest"
    if frontend_changed and not builder_referenced:
        assert log.index(builder_check) < log.index(builder_remove) < build_index
    else:
        assert builder_remove not in log
    if not frontend_changed:
        assert builder_check not in log
    assert log.index("docker image rm fundscope-backend-candidate:older") < build_index
    assert log.index("docker image rm fundscope-backend-candidate:previous") < build_index
    assert log.index("docker image rm fundscope-v2-research:unused") < build_index
    for protected_image in (
        "fundscope-v2-cache:active",
        "fundscope-v2-research:stopped",
        "fundscope-v2-research:base",
    ):
        assert log.index(f"docker ps -aq --filter ancestor={protected_image}") < build_index
        assert f"docker image rm {protected_image}" not in log
        assert f"Keeping FundScope image referenced by a container: {protected_image}" in result.stderr
    assert log[-1] == f"docker image rm fundscope-backend-candidate:{'a' * 40}"
    assert all(
        command.startswith("docker image rm fundscope-") or command == builder_remove
        for command in log if command.startswith("docker image rm ")
    )
    assert not any(command.startswith("rsync ") or " pg_dump " in command for command in log)
    assert not any(" down " in command or "alembic upgrade" in command for command in log)
    assert previous_source.read_text() == "previous deployment\n"
    assert previous_backup.read_bytes() == b"backup"


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


def test_ip_deploy_can_bootstrap_acme_without_exposing_certificate_keys() -> None:
    compose = _read_repository_file("deploy/docker-compose.ip.yml")
    nginx = _read_repository_file("deploy/nginx-ip-http.conf")
    init_script = _read_repository_file("deploy/certbot-init.sh")

    assert "certbot-var:/var/www/certbot:ro" in compose
    assert "certbot-etc:/etc/letsencrypt" in compose
    assert "location /.well-known/acme-challenge/" in nginx
    assert "root /var/www/certbot;" in nginx
    assert "COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.ip.yml}" in init_script
    assert "TIMEOUT_SECONDS=${TIMEOUT_SECONDS:-55}" in init_script
    assert 'timeout "${TIMEOUT_SECONDS}s" docker compose' in init_script
    assert "--non-interactive --agree-tos --keep-until-expiring" in init_script


def test_domain_deploy_requires_certificate_and_checks_real_tls_health() -> None:
    workflow = _read_repository_file(".github/workflows/deploy.yml")

    assert 'if [ "$COMPOSE_FILE" = docker-compose.yml ]; then' in workflow
    assert "test -s /etc/letsencrypt/live/$fqdn/fullchain.pem" in workflow
    assert "test -s /etc/letsencrypt/live/$fqdn/privkey.pem" in workflow
    assert '--resolve "$FQDN:443:127.0.0.1"' in workflow
    assert '"https://$FQDN/api/health"' in workflow


def test_certificate_renewal_is_bounded_and_reload_is_separate() -> None:
    renew_script = _read_repository_file("deploy/certbot-renew.sh")
    installer = _read_repository_file("deploy/install-certbot-renewal.sh")

    assert "TIMEOUT_SECONDS=${TIMEOUT_SECONDS:-55}" in renew_script
    assert 'timeout "${TIMEOUT_SECONDS}s" docker compose' in renew_script
    assert "timeout 10s docker compose" in renew_script
    assert "flock -n /run/lock/fundscope-certbot-renew.lock" in installer
