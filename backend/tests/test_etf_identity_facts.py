from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    EtfSyncCursor,
    EtfTaxonomyFact,
    EtfThemeProfile,
    EtfTrackedUnderlyingFact,
    EtfUniverseMembership,
    TradableEtf,
)
from app.services.short_research.etf_identity_facts import (
    MAX_IDENTITY_FACT_RECORDS,
    IdentityFactIngestionRequest,
    IdentityFactProviderPage,
    TaxonomyFactInput,
    TrackedUnderlyingFactInput,
    identity_fact_contract,
    identity_fact_coverage_at_cutoff,
    persist_etf_identity_facts,
    run_identity_fact_ingestion_slice,
    select_taxonomy_fact_at_cutoff,
    select_tracked_underlying_fact_at_cutoff,
)


def _tradable_etf(code: str = "512710") -> TradableEtf:
    return TradableEtf(
        code=code,
        name="军工龙头ETF",
        exchange="SH",
        theme_tags_json=[],
        trading_rule_label="证券账户 T+1 ETF",
        asset_class="sector",
        is_short_term_eligible=True,
        is_watchlist=True,
    )


def _taxonomy_record(*, observed_at: datetime, primary_theme: str) -> TaxonomyFactInput:
    return TaxonomyFactInput(
        etf_code="512710",
        external_source_id="rules:512710",
        source="local_taxonomy_rules",
        provider_version="taxonomy-provider-v1",
        observed_at=observed_at,
        confidence="high",
        rule_version="taxonomy-rules-v2",
        asset_bucket="equity",
        theme_group="industrial",
        primary_theme=primary_theme,
        secondary_themes=["制造"],
        classification_source="domestic_keyword",
        classification_reason="国内名称命中已审计规则。",
        raw_payload={"code": "512710", "primary_theme": primary_theme},
    )


def _underlying_record(*, observed_at: datetime, underlying_id: str) -> TrackedUnderlyingFactInput:
    return TrackedUnderlyingFactInput(
        etf_code="512710",
        external_source_id="registry:512710",
        source="authoritative_index_registry",
        provider_version="registry-v1",
        observed_at=observed_at,
        identity_state="resolved",
        tracked_underlying_id=underlying_id,
        mapping_basis="authoritative",
        confidence="high",
        rule_version="identity-rules-v1",
        identity_reason="权威指数登记映射。",
        raw_payload={"fund_code": "512710", "underlying_id": underlying_id},
    )


def _active_membership(code: str) -> EtfUniverseMembership:
    return EtfUniverseMembership(
        etf_code=code,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        source="authoritative-test-universe",
        tracked_underlying_id=None,
    )


def _page_underlying_record(
    code: str,
    *,
    observed_at: datetime,
    source: str = "authoritative_index_registry",
    mapping_basis: str = "authoritative",
) -> TrackedUnderlyingFactInput:
    return TrackedUnderlyingFactInput(
        etf_code=code,
        external_source_id=f"registry:{code}",
        source=source,
        provider_version="registry-v1",
        observed_at=observed_at,
        identity_state="resolved",
        tracked_underlying_id=f"IDX-{code}",
        mapping_basis=mapping_basis,
        confidence="high",
        rule_version="identity-rules-v1",
        identity_reason="权威指数登记映射。",
        raw_payload={"fund_code": code, "underlying_id": f"IDX-{code}"},
    )


@pytest.mark.asyncio
async def test_identity_facts_are_idempotent_revisioned_and_cutoff_aware(app) -> None:
    observed_at = datetime(2026, 8, 1, 9, 30)
    revised_at = observed_at + timedelta(days=1)
    first_taxonomy = _taxonomy_record(observed_at=observed_at, primary_theme="军工")
    first_underlying = _underlying_record(observed_at=observed_at, underlying_id="CSI-MIL-OLD")

    async with app.state.db.session() as session:
        session.add(_tradable_etf())
        session.add(
            EtfUniverseMembership(
                etf_code="512710",
                effective_from=date(2026, 1, 1),
                effective_to=None,
                source="test",
                tracked_underlying_id=None,
            )
        )
        await session.commit()

        first = await persist_etf_identity_facts(
            session,
            taxonomy_records=[first_taxonomy],
            underlying_records=[first_underlying],
        )
        await session.commit()

        duplicate = await persist_etf_identity_facts(
            session,
            taxonomy_records=[first_taxonomy],
            underlying_records=[first_underlying],
        )
        await session.commit()

        revised = await persist_etf_identity_facts(
            session,
            taxonomy_records=[
                _taxonomy_record(observed_at=revised_at, primary_theme="高端制造"),
            ],
            underlying_records=[
                _underlying_record(observed_at=revised_at, underlying_id="CSI-MIL-NEW"),
            ],
        )
        await session.commit()

        taxonomy_facts = (
            await session.scalars(
                select(EtfTaxonomyFact)
                .where(EtfTaxonomyFact.etf_code == "512710")
                .order_by(EtfTaxonomyFact.observed_at.asc()),
            )
        ).all()
        underlying_facts = (
            await session.scalars(
                select(EtfTrackedUnderlyingFact)
                .where(EtfTrackedUnderlyingFact.etf_code == "512710")
                .order_by(EtfTrackedUnderlyingFact.observed_at.asc()),
            )
        ).all()
        profile = await session.get(EtfThemeProfile, "512710")
        membership = await session.scalar(
            select(EtfUniverseMembership).where(
                EtfUniverseMembership.etf_code == "512710",
                EtfUniverseMembership.effective_to.is_(None),
            )
        )
        taxonomy_at_first_cutoff = await select_taxonomy_fact_at_cutoff(
            session,
            etf_code="512710",
            cutoff=observed_at + timedelta(seconds=1),
        )
        taxonomy_at_revision_cutoff = await select_taxonomy_fact_at_cutoff(
            session,
            etf_code="512710",
            cutoff=revised_at + timedelta(seconds=1),
        )
        underlying_at_first_cutoff = await select_tracked_underlying_fact_at_cutoff(
            session,
            etf_code="512710",
            cutoff=observed_at + timedelta(seconds=1),
        )
        underlying_at_revision_cutoff = await select_tracked_underlying_fact_at_cutoff(
            session,
            etf_code="512710",
            cutoff=revised_at + timedelta(seconds=1),
        )

    assert first.taxonomy_facts_inserted == 1
    assert first.underlying_facts_inserted == 1
    assert first.taxonomy_projections_inserted == 1
    assert first.membership_projections_updated == 1
    assert duplicate.taxonomy_facts_existing == 1
    assert duplicate.underlying_facts_existing == 1
    assert revised.taxonomy_facts_inserted == 1
    assert revised.underlying_facts_inserted == 1
    assert len(taxonomy_facts) == 2
    assert len(underlying_facts) == 2
    assert taxonomy_facts[1].supersedes_fact_id == taxonomy_facts[0].id
    assert underlying_facts[1].supersedes_fact_id == underlying_facts[0].id
    assert all(len(fact.raw_payload_hash) == 64 for fact in taxonomy_facts)
    assert all(len(fact.evidence_hash) == 64 for fact in underlying_facts)
    assert all(len(fact.fact_hash) == 64 for fact in [*taxonomy_facts, *underlying_facts])
    assert profile is not None
    assert profile.primary_theme == "高端制造"
    assert membership is not None
    assert membership.tracked_underlying_id == "CSI-MIL-NEW"
    assert taxonomy_at_first_cutoff is not None
    assert taxonomy_at_first_cutoff.primary_theme == "军工"
    assert taxonomy_at_revision_cutoff is not None
    assert taxonomy_at_revision_cutoff.primary_theme == "高端制造"
    assert underlying_at_first_cutoff is not None
    assert underlying_at_first_cutoff.tracked_underlying_id == "CSI-MIL-OLD"
    assert underlying_at_revision_cutoff is not None
    assert underlying_at_revision_cutoff.tracked_underlying_id == "CSI-MIL-NEW"


@pytest.mark.asyncio
async def test_identity_fact_persist_is_bounded_and_rejects_name_inference(app) -> None:
    observed_at = datetime(2026, 8, 1, 9, 30)
    taxonomy = _taxonomy_record(observed_at=observed_at, primary_theme="军工")

    async with app.state.db.session() as session:
        session.add(_tradable_etf())
        await session.commit()

        with pytest.raises(ValueError, match=f"at most {MAX_IDENTITY_FACT_RECORDS}"):
            await persist_etf_identity_facts(
                session,
                taxonomy_records=[taxonomy] * (MAX_IDENTITY_FACT_RECORDS + 1),
            )

        with pytest.raises(ValueError, match="cannot be inferred from fund names"):
            await persist_etf_identity_facts(
                session,
                underlying_records=[
                    TrackedUnderlyingFactInput(
                        etf_code="512710",
                        external_source_id="name:512710",
                        source="fund_name",
                        provider_version="local-v1",
                        observed_at=observed_at,
                        identity_state="resolved",
                        tracked_underlying_id="CSI-MIL-OLD",
                        mapping_basis="manual",
                        confidence="low",
                        rule_version="name-rule-v1",
                        identity_reason="名称相似。",
                    )
                ],
            )


@pytest.mark.asyncio
async def test_identity_ingestion_slice_resumes_one_deterministic_page_at_a_time(app) -> None:
    codes = [f"5127{index:02d}" for index in range(6)]
    observed_at = datetime(2026, 8, 2, 9, 30)
    calls: list[tuple[str, ...]] = []

    async def fetch_page(page_codes: tuple[str, ...]) -> IdentityFactProviderPage:
        calls.append(page_codes)
        return IdentityFactProviderPage(
            underlying_records=tuple(
                _page_underlying_record(code, observed_at=observed_at)
                for code in page_codes
            )
        )

    request = IdentityFactIngestionRequest(
        scope="identity-facts:resume-test",
        target_page_size=2,
    )
    async with app.state.db.session() as session:
        session.add_all([_tradable_etf(code) for code in codes])
        session.add_all([_active_membership(code) for code in codes])
        await session.commit()

        first = await run_identity_fact_ingestion_slice(
            session,
            request=request,
            fetch_page=fetch_page,
        )
        cursor_after_first = await session.get(EtfSyncCursor, request.scope)
        cursor_after_first_code = (
            cursor_after_first.last_regular_code if cursor_after_first is not None else None
        )
        await session.commit()

        second = await run_identity_fact_ingestion_slice(
            session,
            request=request,
            fetch_page=fetch_page,
        )
        cursor_after_second = await session.get(EtfSyncCursor, request.scope)
        cursor_after_second_code = (
            cursor_after_second.last_regular_code if cursor_after_second is not None else None
        )
        await session.commit()
        fact_count = await session.scalar(
            select(func.count()).select_from(EtfTrackedUnderlyingFact),
        )

    assert first.status == "partial"
    assert first.selected_codes == tuple(codes[:5])
    assert first.page_size == 5
    assert first.has_more is True
    assert cursor_after_first_code == codes[4]
    assert second.status == "complete"
    assert second.selected_codes == (codes[5],)
    assert second.cursor_before == codes[4]
    assert cursor_after_second_code == codes[5]
    assert calls == [tuple(codes[:5]), (codes[5],)]
    assert fact_count == 6


@pytest.mark.asyncio
async def test_identity_ingestion_failure_keeps_cursor_unchanged_and_retries_same_page(app) -> None:
    codes = [f"5128{index:02d}" for index in range(5)]
    observed_at = datetime(2026, 8, 2, 9, 30)
    attempted_pages: list[tuple[str, ...]] = []
    request = IdentityFactIngestionRequest(
        scope="identity-facts:failure-test",
        target_page_size=5,
    )

    async def fail_fetch(page_codes: tuple[str, ...]) -> IdentityFactProviderPage:
        attempted_pages.append(page_codes)
        raise RuntimeError("provider unavailable")

    async def succeed_fetch(page_codes: tuple[str, ...]) -> IdentityFactProviderPage:
        attempted_pages.append(page_codes)
        return IdentityFactProviderPage(
            underlying_records=tuple(
                _page_underlying_record(code, observed_at=observed_at)
                for code in page_codes
            )
        )

    async with app.state.db.session() as session:
        session.add_all([_tradable_etf(code) for code in codes])
        session.add_all([_active_membership(code) for code in codes])
        session.add(
            EtfSyncCursor(
                scope=request.scope,
                last_regular_code="512799",
                last_lane="identity_facts",
            )
        )
        await session.commit()

        failed = await run_identity_fact_ingestion_slice(
            session,
            request=request,
            fetch_page=fail_fetch,
        )
        cursor_after_failure = await session.get(EtfSyncCursor, request.scope)
        cursor_after_failure_code = (
            cursor_after_failure.last_regular_code if cursor_after_failure is not None else None
        )
        fact_count_after_failure = await session.scalar(
            select(func.count()).select_from(EtfTrackedUnderlyingFact),
        )

        resumed = await run_identity_fact_ingestion_slice(
            session,
            request=request,
            fetch_page=succeed_fetch,
        )
        await session.commit()
        cursor_after_resume = await session.get(EtfSyncCursor, request.scope)
        cursor_after_resume_code = (
            cursor_after_resume.last_regular_code if cursor_after_resume is not None else None
        )

    assert failed.status == "failed"
    assert failed.stop_reason == "provider_fetch_failed"
    assert cursor_after_failure_code == "512799"
    assert fact_count_after_failure == 0
    assert resumed.status == "complete"
    assert resumed.cursor_before == "512799"
    assert attempted_pages == [tuple(codes), tuple(codes)]
    assert cursor_after_resume_code == codes[-1]


@pytest.mark.asyncio
async def test_identity_ingestion_rejects_oversize_or_non_authoritative_provider_pages(app) -> None:
    codes = [f"5129{index:02d}" for index in range(5)]
    observed_at = datetime(2026, 8, 2, 9, 30)
    request = IdentityFactIngestionRequest(
        scope="identity-facts:validation-test",
        target_page_size=5,
    )

    async def oversized_fetch(_: tuple[str, ...]) -> IdentityFactProviderPage:
        return IdentityFactProviderPage(
            underlying_records=tuple(
                _page_underlying_record(codes[0], observed_at=observed_at)
                for _ in range(MAX_IDENTITY_FACT_RECORDS + 1)
            )
        )

    async def manual_fetch(page_codes: tuple[str, ...]) -> IdentityFactProviderPage:
        return IdentityFactProviderPage(
            underlying_records=(
                _page_underlying_record(
                    page_codes[0],
                    observed_at=observed_at,
                    mapping_basis="manual",
                ),
            )
        )

    async def name_source_fetch(page_codes: tuple[str, ...]) -> IdentityFactProviderPage:
        return IdentityFactProviderPage(
            underlying_records=(
                _page_underlying_record(
                    page_codes[0],
                    observed_at=observed_at,
                    source="fund_name",
                ),
            )
        )

    async with app.state.db.session() as session:
        session.add_all([_tradable_etf(code) for code in codes])
        session.add_all([_active_membership(code) for code in codes])
        await session.commit()

        oversized = await run_identity_fact_ingestion_slice(
            session,
            request=request,
            fetch_page=oversized_fetch,
        )
        manual = await run_identity_fact_ingestion_slice(
            session,
            request=request,
            fetch_page=manual_fetch,
        )
        name_source = await run_identity_fact_ingestion_slice(
            session,
            request=request,
            fetch_page=name_source_fetch,
        )
        cursor = await session.get(EtfSyncCursor, request.scope)
        fact_count = await session.scalar(
            select(func.count()).select_from(EtfTrackedUnderlyingFact),
        )

    assert oversized.status == "failed"
    assert oversized.stop_reason == "identity_fact_persist_failed"
    assert oversized.error_summary is not None
    assert f"exceeds {MAX_IDENTITY_FACT_RECORDS}" in oversized.error_summary
    assert manual.status == "failed"
    assert manual.error_summary is not None
    assert "only authoritative" in manual.error_summary
    assert name_source.status == "failed"
    assert name_source.error_summary is not None
    assert "cannot be inferred from fund names" in name_source.error_summary
    assert cursor is None
    assert fact_count == 0


def test_identity_ingestion_request_caps_slice_budget() -> None:
    with pytest.raises(ValueError, match="between 0 and 55"):
        IdentityFactIngestionRequest(
            scope="identity-facts:budget-test",
            slice_budget_seconds=55.1,
        )


def test_identity_fact_contract_freezes_precedence_and_name_inference() -> None:
    contract = identity_fact_contract()

    assert contract["contract_version"] == "etf_identity_fact_contract_v1"
    assert contract["taxonomy_precedence"][1] == "cross_border_marker"
    assert contract["visibility_field"] == "observed_at"
    assert contract["name_only_formal_underlying_inference"] == "forbidden"


@pytest.mark.asyncio
async def test_identity_coverage_projection_is_compact_and_cutoff_aware(
    app,
    client,
) -> None:
    visible_at = datetime(2026, 8, 1, 9, 30)
    late_at = visible_at + timedelta(days=1)
    taxonomy = _taxonomy_record(observed_at=visible_at, primary_theme="军工")
    underlying = _underlying_record(
        observed_at=visible_at,
        underlying_id="CSI-MIL",
    )
    async with app.state.db.session() as session:
        session.add_all([_tradable_etf("512710"), _tradable_etf("512711")])
        session.add_all([_active_membership("512710"), _active_membership("512711")])
        await session.commit()
        await persist_etf_identity_facts(
            session,
            taxonomy_records=[taxonomy],
            underlying_records=[underlying],
        )
        await session.commit()
        await persist_etf_identity_facts(
            session,
            taxonomy_records=[
                replace(
                    taxonomy,
                    etf_code="512711",
                    external_source_id="rules:512711",
                    observed_at=late_at,
                )
            ],
            underlying_records=[
                replace(
                    underlying,
                    etf_code="512711",
                    external_source_id="registry:512711",
                    observed_at=late_at,
                    tracked_underlying_id="CSI-LATE",
                )
            ],
        )
        await session.commit()
        projection = await identity_fact_coverage_at_cutoff(
            session,
            cutoff=visible_at + timedelta(seconds=1),
        )

    assert projection.universe_count == 2
    assert projection.known_taxonomy_count == 1
    assert projection.unknown_taxonomy_count == 1
    assert projection.taxonomy_coverage_ratio == 0.5
    assert projection.resolved_underlying_count == 1
    assert projection.unresolved_underlying_count == 1
    assert projection.tracked_underlying_coverage_ratio == 0.5
    assert {group["fact_kind"] for group in projection.evidence_groups} == {
        "taxonomy",
        "tracked_underlying",
    }
    assert all(group["count"] == 1 for group in projection.evidence_groups)

    response = await client.get(
        "/api/short-research/assets/identity-coverage",
        params={"cutoff": (visible_at + timedelta(seconds=1)).isoformat()},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["universe_count"] == 2
    assert (
        payload["identity_fact_contract"]["contract_version"]
        == "etf_identity_fact_contract_v1"
    )
    assert payload["unknown_taxonomy_count"] == 1
    assert payload["unresolved_underlying_count"] == 1
    assert {group["rule_version"] for group in payload["evidence_groups"]} == {
        "taxonomy-rules-v2",
        "identity-rules-v1",
    }
