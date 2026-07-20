from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    EtfCatalystEventVersion,
    EtfCatalystExtractionAttempt,
    EtfThemeCatalystEvent,
)
from app.services.etf_catalyst_shadow.events import (
    CatalystEventValidationError,
    EventCandidate,
    migrate_manual_seeds_display_only,
    normalize_ai_extraction,
    normalize_event_candidate,
    record_ai_extraction_failure,
    verify_pending_event,
)
from app.services.etf_catalyst_shadow.ingestion import (
    ReceiptInput,
    ingest_receipt,
    register_approved_sources,
)
from app.services.etf_catalyst_shadow.policy import CATALYST_TAXONOMY_VERSION

PUBLISHED = datetime(2026, 7, 19, 6, 30)
RECEIVED = datetime(2026, 7, 19, 7, 0)


async def _receipt(session):
    await register_approved_sources(session)
    return await ingest_receipt(
        session,
        ReceiptInput(
            source_id="ndrc_policy",
            fetch_state="item",
            fetched_at=RECEIVED + timedelta(minutes=1),
            first_received_at=RECEIVED,
            external_id="event-fixture-1",
            canonical_url="https://www.ndrc.gov.cn/policy/event.html",
            source_published_at=PUBLISHED,
            raw_content="<html>新能源政策支持</html>",
            raw_content_ref="raw://event-fixture-1",
            metadata={
                "supported_entities": ["国家发展改革委"],
                "supported_theme_ids": ["新能源"],
                "supported_proxy_theme_ids": ["电力设备"],
                "supported_direction": "positive",
            },
        ),
    )


def _candidate(receipt_id: str) -> EventCandidate:
    return EventCandidate(
        event_type="industry_policy",
        entities=("国家发展改革委",),
        title="新能源政策发布",
        summary="发布新能源产业支持政策。",
        receipt_ids=(receipt_id,),
        source_published_at=PUBLISHED,
        effective_start=PUBLISHED,
        effective_end=PUBLISHED + timedelta(days=30),
        direction="positive",
        direct_theme_ids=("新能源",),
    )


def _ai_payload(receipt_id: str) -> dict:
    return {
        "event_type": "industry_policy",
        "entities": ["国家发展改革委"],
        "title": "新能源政策发布",
        "summary": "发布新能源产业支持政策。",
        "receipt_ids": [receipt_id],
        "source_published_at": PUBLISHED.isoformat(),
        "effective_start": PUBLISHED.isoformat(),
        "effective_end": (PUBLISHED + timedelta(days=30)).isoformat(),
        "direction": "positive",
        "direct_theme_ids": ["新能源"],
        "proxy_theme_ids": [],
        "taxonomy_version": CATALYST_TAXONOMY_VERSION,
    }


async def test_event_schema_is_score_free_and_deterministically_verified(app) -> None:
    columns = set(EtfCatalystEventVersion.__table__.columns.keys())
    assert not columns.intersection(
        {
            "strength_score",
            "catalyst_score",
            "sentiment_heat_score",
            "opportunity_score",
            "weight",
            "rank_delta",
        }
    )

    async with app.state.db.session() as session:
        receipt = await _receipt(session)
        event = await normalize_event_candidate(
            session,
            _candidate(receipt.receipt_id),
            extraction_method="deterministic",
            verify=True,
        )

    assert event.verification_state == "verified"
    assert event.direction == "positive"
    assert event.direct_theme_ids_json == ["新能源"]
    assert event.proxy_theme_ids_json == []
    assert event.taxonomy_version == CATALYST_TAXONOMY_VERSION


async def test_ai_candidate_remains_pending_until_new_reviewed_version(app) -> None:
    async with app.state.db.session() as session:
        receipt = await _receipt(session)
        pending = await normalize_ai_extraction(
            session,
            receipt_id=receipt.receipt_id,
            extractor_version="fixture-ai-v1",
            payload=_ai_payload(receipt.receipt_id),
        )
        verified = await verify_pending_event(session, pending.id)

    assert pending.verification_state == "pending"
    assert verified.verification_state == "verified"
    assert verified.id != pending.id
    assert verified.event_id == pending.event_id
    assert verified.event_version == 2
    assert verified.supersedes_event_version_id == pending.id


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (
            {"source_published_at": (PUBLISHED - timedelta(days=1)).isoformat()},
            "published time",
        ),
        ({"entities": ["不存在机构"]}, "entities"),
        ({"direction": "negative"}, "direction"),
        ({"direct_theme_ids": ["机器人"]}, "theme mapping"),
        ({"strength_score": 90}, "scoring fields"),
    ),
)
async def test_ai_hallucinations_are_audited_and_rejected(
    app,
    change: dict,
    message: str,
) -> None:
    async with app.state.db.session() as session:
        receipt = await _receipt(session)
        payload = {**_ai_payload(receipt.receipt_id), **change}
        with pytest.raises(CatalystEventValidationError, match=message):
            await normalize_ai_extraction(
                session,
                receipt_id=receipt.receipt_id,
                extractor_version=f"invalid-{message}",
                payload=payload,
            )
        attempt = await session.scalar(
            select(EtfCatalystExtractionAttempt)
            .where(EtfCatalystExtractionAttempt.receipt_id == receipt.id)
            .order_by(EtfCatalystExtractionAttempt.id.desc())
        )
        event_count = int(
            await session.scalar(
                select(func.count()).select_from(EtfCatalystEventVersion)
            )
            or 0
        )

    assert attempt is not None
    assert attempt.status == "invalid"
    assert attempt.error_summary
    assert event_count == 0


async def test_ai_failure_preserves_receipt_without_fabricated_event(app) -> None:
    async with app.state.db.session() as session:
        receipt = await _receipt(session)
        attempt = await record_ai_extraction_failure(
            session,
            receipt_id=receipt.receipt_id,
            extractor_version="fixture-timeout",
            error_summary="TimeoutError: 50 seconds",
        )
        event_count = int(
            await session.scalar(
                select(func.count()).select_from(EtfCatalystEventVersion)
            )
            or 0
        )

    assert attempt.status == "failed"
    assert attempt.error_summary == "TimeoutError: 50 seconds"
    assert event_count == 0
    assert receipt.raw_content_ref == "raw://event-fixture-1"


async def test_manual_seed_migrates_to_display_only_without_receipt(app) -> None:
    async with app.state.db.session() as session:
        seed = EtfThemeCatalystEvent(
            theme_key="机器人",
            theme_name="机器人",
            catalyst_type="manual_theme",
            title="旧人工主题",
            summary="仅供展示",
            source_url="https://example.com/manual",
            event_date=date(2026, 7, 1),
            effective_start=date(2026, 7, 1),
            effective_end=date(2026, 12, 31),
            direction="positive",
            strength_score=99,
            confidence_score=99,
            status="active",
            source_type="manual_seed",
            metadata_json={},
        )
        session.add(seed)
        await session.commit()
        migrated = await migrate_manual_seeds_display_only(session)
        repeated = await migrate_manual_seeds_display_only(session)

    assert len(migrated) == len(repeated) == 1
    assert migrated[0].id == repeated[0].id
    assert migrated[0].verification_state == "manual_display_only"
    assert migrated[0].supporting_receipt_ids_json == []
    assert any("excluded" in item for item in migrated[0].limitations_json)


async def test_ai_cannot_directly_mark_event_verified(app) -> None:
    async with app.state.db.session() as session:
        receipt = await _receipt(session)
        with pytest.raises(CatalystEventValidationError, match="cannot directly"):
            await normalize_event_candidate(
                session,
                replace(_candidate(receipt.receipt_id)),
                extraction_method="ai",
                verify=True,
            )
