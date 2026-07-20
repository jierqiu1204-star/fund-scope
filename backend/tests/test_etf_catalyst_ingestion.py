from datetime import datetime, timedelta

import pytest

from app.models.entities import CatalystEvidenceImmutableError
from app.services.etf_catalyst_shadow.ingestion import (
    CatalystSourcePolicyError,
    ReceiptInput,
    ingest_receipt,
    register_approved_sources,
)

RECEIVED = datetime(2026, 7, 19, 7, 0)
FETCHED = datetime(2026, 7, 19, 7, 1)
PUBLISHED = datetime(2026, 7, 19, 6, 30)


def _item(**overrides: object) -> ReceiptInput:
    values: dict[str, object] = {
        "source_id": "ndrc_policy",
        "fetch_state": "item",
        "external_id": "ndrc-2026-1",
        "canonical_url": "https://www.ndrc.gov.cn/policy/one.html#fragment",
        "source_published_at": PUBLISHED,
        "first_received_at": RECEIVED,
        "fetched_at": FETCHED,
        "raw_content": "<html>version one</html>",
        "raw_content_ref": "raw://ndrc/2026-1/v1",
        "parser_version": "fixture-v1",
        "metadata": {
            "supported_entities": ["国家发展改革委"],
            "supported_theme_ids": ["新能源"],
            "supported_direction": "positive",
        },
    }
    values.update(overrides)
    return ReceiptInput(**values)  # type: ignore[arg-type]


async def test_registry_and_identical_receipt_ingestion_are_idempotent(app) -> None:
    async with app.state.db.session() as session:
        first_registry = await register_approved_sources(session)
        second_registry = await register_approved_sources(session)
        first = await ingest_receipt(session, _item())
        second = await ingest_receipt(
            session,
            _item(
                fetched_at=FETCHED + timedelta(minutes=5),
                first_received_at=RECEIVED + timedelta(minutes=5),
            ),
        )

    assert len(first_registry) == len(second_registry) == 6
    assert [row.id for row in first_registry] == [row.id for row in second_registry]
    assert first.id == second.id
    assert first.canonical_url == "https://www.ndrc.gov.cn/policy/one.html"
    assert first.correction_of_receipt_id is None


async def test_changed_content_creates_immutable_correction_version(app) -> None:
    async with app.state.db.session() as session:
        await register_approved_sources(session)
        first = await ingest_receipt(session, _item())
        correction = await ingest_receipt(
            session,
            _item(
                raw_content="<html>corrected</html>",
                raw_content_ref="raw://ndrc/2026-1/v2",
                first_received_at=RECEIVED + timedelta(hours=1),
                fetched_at=FETCHED + timedelta(hours=1),
            ),
        )

        assert correction.id != first.id
        assert correction.content_hash != first.content_hash
        assert correction.correction_of_receipt_id == first.id
        first.parser_version = "tampered"
        with pytest.raises(
            CatalystEvidenceImmutableError,
            match="immutable",
        ):
            await session.commit()


@pytest.mark.parametrize(
    ("state", "observation_key", "error_summary"),
    (
        ("successful_empty", "ndrc:2026-07-19", None),
        ("unavailable", "ndrc:2026-07-20", "TimeoutError: 50 seconds"),
        ("not_applicable", "ndrc:theme:banking", None),
    ),
)
async def test_empty_unavailable_and_not_applicable_are_distinct_receipts(
    app,
    state: str,
    observation_key: str,
    error_summary: str | None,
) -> None:
    async with app.state.db.session() as session:
        await register_approved_sources(session)
        receipt = await ingest_receipt(
            session,
            ReceiptInput(
                source_id="ndrc_policy",
                fetch_state=state,  # type: ignore[arg-type]
                observation_key=observation_key,
                first_received_at=RECEIVED,
                fetched_at=FETCHED,
                error_summary=error_summary,
            ),
        )

    assert receipt.fetch_state == state
    assert receipt.error_summary == error_summary


async def test_unregistered_or_wrong_domain_cannot_become_receipt(app) -> None:
    async with app.state.db.session() as session:
        await register_approved_sources(session)
        with pytest.raises(CatalystSourcePolicyError, match="outside"):
            await ingest_receipt(
                session,
                _item(canonical_url="https://finance.example.com/fabricated"),
            )
        with pytest.raises(CatalystSourcePolicyError, match="not registered"):
            await ingest_receipt(
                session,
                _item(source_id="unapproved_media"),
            )
