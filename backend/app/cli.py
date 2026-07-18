from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import date, timedelta
from typing import cast

from sqlalchemy import delete

from app.core.config import get_settings
from app.core.db import DatabaseManager
from app.models.entities import Index, IndexValuationHistory
from app.services.index_data import fetch_index_valuation
from app.services.strategy_lab.etf_validation_continuation import (
    continue_registered_production_validation,
)
from app.services.strategy_lab.etf_validation_manifest_repair import (
    repair_legacy_validation_manifests,
)
from app.services.tracked_positions.lifecycle_backfill import backfill_position_lifecycle_batch
from app.services.valuation import compute_percentile
from app.services.workflows.etf_readiness_attestation import (
    build_bounded_attested_etf_readiness_report,
    provision_database_instance_identity,
)


async def backfill_valuation(index_code: str, years: int) -> None:
    settings = get_settings()
    db = DatabaseManager(settings.database_url)
    async with db.session() as session:
        if await session.get(Index, index_code) is None:
            session.add(Index(code=index_code, name=index_code, region="CN", is_watchlist=True))
            await session.commit()

        await session.execute(delete(IndexValuationHistory).where(IndexValuationHistory.index_code == index_code))
        history_values: list[float] = []
        start = date.today() - timedelta(days=years * 365)
        current = start

        while current <= date.today():
            if current.weekday() < 5:
                payload = await fetch_index_valuation(index_code, current)
                pe = float(cast(float, payload["pe"]))
                pb = float(cast(float, payload["pb"]))
                history_values.append(pe)
                pe_stats = compute_percentile(history_values, pe)
                pb_stats = compute_percentile(history_values, pb)
                session.add(
                    IndexValuationHistory(
                        index_code=index_code,
                        valuation_date=current,
                        pe=pe,
                        pb=pb,
                        dividend_yield=float(cast(float, payload["dividend_yield"])),
                        pe_percentile=pe_stats.percentile,
                        pb_percentile=pb_stats.percentile,
                        effective_window=pe_stats.effective_window,
                    )
                )
            current += timedelta(days=1)
        await session.commit()
    await db.engine.dispose()


async def backfill_etf_alert_lifecycle(
    position_ids: list[int],
    cutoff: date,
    max_items: int,
) -> None:
    settings = get_settings()
    db = DatabaseManager(settings.database_url)
    async with db.session() as session:
        result = await backfill_position_lifecycle_batch(
            session,
            position_ids=position_ids,
            cutoff=cutoff,
            max_items=max_items,
        )
        await session.commit()
    await db.engine.dispose()
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))


async def repair_etf_validation_manifests(*, apply: bool, limit: int) -> None:
    settings = get_settings()
    db = DatabaseManager(settings.database_url)
    async with db.session() as session:
        result = await repair_legacy_validation_manifests(
            session,
            apply=apply,
            limit=limit,
        )
        if apply:
            await session.commit()
        else:
            await session.rollback()
    await db.engine.dispose()
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))


async def read_etf_readiness(target_date: date | None) -> None:
    settings = get_settings()
    db = DatabaseManager(settings.database_url)
    try:
        async with db.session() as session:
            report = await build_bounded_attested_etf_readiness_report(
                session,
                settings=settings,
                target_date=target_date,
            )
            await session.rollback()
    finally:
        await db.engine.dispose()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


async def provision_readiness_database_identity(
    *,
    environment: str,
    deploy_artifact: str,
    attestation_key_id: str,
    instance_uuid: str | None,
) -> None:
    settings = get_settings()
    db = DatabaseManager(settings.database_url)
    try:
        async with db.session() as session:
            identity = await provision_database_instance_identity(
                session,
                declared_environment=environment,
                deploy_artifact=deploy_artifact,
                attestation_key_id=attestation_key_id,
                instance_uuid=instance_uuid,
                creation_metadata={"provisioner": "fundscope-cli"},
            )
            await session.commit()
            payload = {
                "database_instance_uuid": identity.instance_uuid,
                "declared_environment": identity.declared_environment,
                "provisioned_by_deploy": identity.provisioned_by_deploy,
                "attestation_key_id": identity.attestation_key_id,
            }
    finally:
        await db.engine.dispose()
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


async def continue_etf_validation(validation_run_id: int) -> None:
    settings = get_settings()
    db = DatabaseManager(settings.database_url)
    try:
        async with db.session() as session:
            result = await continue_registered_production_validation(
                session,
                validation_run_id=validation_run_id,
            )
    finally:
        await db.engine.dispose()
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FundScope CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backfill = subparsers.add_parser("backfill-valuation")
    backfill.add_argument("--index", required=True)
    backfill.add_argument("--years", type=int, default=10)

    lifecycle = subparsers.add_parser("backfill-etf-alert-lifecycle")
    lifecycle.add_argument("--position-id", dest="position_ids", action="append", type=int, required=True)
    lifecycle.add_argument("--cutoff", type=date.fromisoformat, required=True)
    lifecycle.add_argument("--max-items", type=int, default=100)
    manifest_repair = subparsers.add_parser("repair-etf-validation-manifests")
    manifest_repair.add_argument("--apply", action="store_true")
    manifest_repair.add_argument("--limit", type=int, default=100)
    readiness = subparsers.add_parser("etf-readiness")
    readiness.add_argument("--target-date", type=date.fromisoformat)
    provision_identity = subparsers.add_parser("provision-database-identity")
    provision_identity.add_argument("--environment", required=True)
    provision_identity.add_argument("--deploy-artifact", required=True)
    provision_identity.add_argument("--attestation-key-id", required=True)
    provision_identity.add_argument("--instance-uuid")
    validation_continuation = subparsers.add_parser("continue-etf-validation")
    validation_continuation.add_argument("--run-id", type=int, required=True)
    return parser


def main() -> None:
    parser = build_parser()

    args = parser.parse_args()
    if args.command == "backfill-valuation":
        asyncio.run(backfill_valuation(args.index, args.years))
    elif args.command == "backfill-etf-alert-lifecycle":
        asyncio.run(backfill_etf_alert_lifecycle(args.position_ids, args.cutoff, args.max_items))
    elif args.command == "repair-etf-validation-manifests":
        asyncio.run(
            repair_etf_validation_manifests(
                apply=args.apply,
                limit=args.limit,
            )
        )
    elif args.command == "etf-readiness":
        asyncio.run(read_etf_readiness(args.target_date))
    elif args.command == "provision-database-identity":
        asyncio.run(
            provision_readiness_database_identity(
                environment=args.environment,
                deploy_artifact=args.deploy_artifact,
                attestation_key_id=args.attestation_key_id,
                instance_uuid=args.instance_uuid,
            )
        )
    elif args.command == "continue-etf-validation":
        asyncio.run(continue_etf_validation(args.run_id))


if __name__ == "__main__":
    main()
