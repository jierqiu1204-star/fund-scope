from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

from app.cli import build_parser


def test_etf_readiness_cli_accepts_an_explicit_trade_date() -> None:
    args = build_parser().parse_args(
        ["etf-readiness", "--target-date", "2026-07-17"]
    )

    assert args.command == "etf-readiness"
    assert args.target_date == date(2026, 7, 17)


def test_database_identity_cli_requires_non_secret_attestation_identity() -> None:
    args = build_parser().parse_args(
        [
            "provision-database-identity",
            "--environment",
            "production",
            "--deploy-artifact",
            "fundscope@sha256:fixture",
            "--attestation-key-id",
            "vps-readiness-key-v1",
        ]
    )

    assert args.command == "provision-database-identity"
    assert args.environment == "production"
    assert args.attestation_key_id == "vps-readiness-key-v1"
    assert not hasattr(args, "attestation_secret")


def test_validation_continuation_cli_is_one_bounded_registered_run() -> None:
    args = build_parser().parse_args(
        ["continue-etf-validation", "--run-id", "7"]
    )

    assert args.command == "continue-etf-validation"
    assert args.run_id == 7


@pytest.mark.asyncio
async def test_instance_can_read_bounded_fail_closed_etf_readiness(client) -> None:
    response = await client.get(
        "/api/admin/jobs/etf-readiness?target_date=2026-07-17"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["read_only"] is True
    assert payload["trade_date"] == "2026-07-17"
    assert payload["production_attested"] is False
    assert payload["limits"]["observed_sql_statements"] <= 25
    assert "test-auth-secret" not in response.text
    assert "secret" not in payload["attestation"]


@pytest.mark.asyncio
async def test_etf_readiness_endpoint_needs_no_login(client) -> None:
    response = await client.get(
        "/api/admin/jobs/etf-readiness",
        headers={"Authorization": ""},
    )

    assert response.status_code == 200
    assert response.json()["read_only"] is True
    assert "secret" not in response.json()["attestation"]


@pytest.mark.asyncio
async def test_etf_readiness_fails_closed_when_identity_migration_is_not_deployed(
    client,
    app,
) -> None:
    async with app.state.db.session() as session:
        await session.execute(text("DROP TABLE database_instance_identity"))
        await session.commit()

    response = await client.get(
        "/api/admin/jobs/etf-readiness?target_date=2026-07-17"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["read_only"] is True
    assert payload["production_attested"] is False
    assert payload["history_readiness"]["status"] == "unavailable"
    assert {
        blocker["key"] for blocker in payload["blockers"]
    } >= {
        "readiness_schema_unavailable",
        "database_instance_identity_table_missing",
    }
    assert "UndefinedTableError" not in response.text
    assert "SELECT database_instance_identity" not in response.text
