from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    EtfSyncCursor,
    EtfTrackedUnderlyingFact,
    EtfUniverseMembership,
    TradableEtf,
)
from app.services.short_research import etf_tracked_underlying as provider
from app.services.short_research import jobs
from app.services.short_research.etf_identity_facts import IdentityFactProviderPage


def _archive_html(code: str, label: str) -> str:
    return f"""
    <html><body>
      <table>
        <tr><td>基金全称</td><td>测试基金</td><td>基金简称</td><td>测试ETF</td></tr>
        <tr><td>基金代码</td><td>{code}（主代码）</td><td>基金类型</td><td>指数型-股票</td></tr>
        <tr><td>业绩比较基准</td><td>{label}</td><td>跟踪标的</td><td>{label}</td></tr>
      </table>
    </body></html>
    """


def _unclosed_code_cell_archive_html(code: str, label: str) -> str:
    """Capture the malformed cell boundary present in the verified public page."""

    return f"""
    <table>
      <tr><th>基金全称</th><td>测试基金</td><th>基金简称</th><td>测试ETF</td></tr>
      <tr><th>基金代码</th><td>{code}（主代码）<th>基金类型</th><td>指数型-股票</td></tr>
      <tr><th>业绩比较基准</th><td>{label}</td><th>跟踪标的</th><td>{label}</td></tr>
    </table>
    """


class _Response:
    def __init__(self, body: str, *, status_code: int = 200) -> None:
        self.text = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Client:
    def __init__(self, bodies: dict[str, str]) -> None:
        self.bodies = bodies
        self.urls: list[str] = []

    async def get(self, url: str) -> _Response:
        self.urls.append(url)
        code = url.rsplit("_", 1)[1].removesuffix(".html")
        return _Response(self.bodies[code])


@pytest.mark.asyncio
async def test_eastmoney_adapter_uses_explicit_field_and_stable_ids() -> None:
    client = _Client(
        {
            "510300": _archive_html("510300", "沪深300指数"),
            "510330": _archive_html("510330", "沪深300指数"),
            "512100": _archive_html("512100", "中证1000指数"),
        }
    )
    observed_at = datetime(2026, 9, 10, 8, 0)

    page = await provider.fetch_eastmoney_tracked_underlying_page(
        ("510300", "510330", "512100"),
        client=client,
        clock=lambda: observed_at,
    )

    records = tuple(page.underlying_records)
    assert len(records) == 3
    assert records[0].tracked_underlying_id == records[1].tracked_underlying_id
    assert records[0].tracked_underlying_id != records[2].tracked_underlying_id
    assert all(record.identity_state == "resolved" for record in records)
    assert all(record.observed_at == observed_at for record in records)
    assert all(record.raw_payload["source_url"].endswith(f"_{record.etf_code}.html") for record in records)
    assert records[0].raw_payload["tracked_underlying_label"] == "沪深300指数"


@pytest.mark.asyncio
async def test_eastmoney_adapter_handles_verified_malformed_html_and_placeholders() -> None:
    client = _Client(
        {
            "510300": _unclosed_code_cell_archive_html("510300", "沪深300指数"),
            "510330": _archive_html("510330", "--"),
        }
    )
    page = await provider.fetch_eastmoney_tracked_underlying_page(
        ("510300",),
        client=client,
        clock=lambda: datetime(2026, 9, 10, 8, 0),
    )
    assert page.underlying_records[0].raw_payload["fund_code"] == "510300"

    with pytest.raises(
        provider.EtfTrackedUnderlyingProviderError,
        match="placeholder_tracked_underlying_field",
    ):
        await provider.fetch_eastmoney_tracked_underlying_page(
            ("510330",),
            client=client,
            clock=lambda: datetime(2026, 9, 10, 8, 0),
        )


@pytest.mark.asyncio
async def test_eastmoney_adapter_keeps_mixed_page_failures_out_of_identity_facts() -> None:
    class PartialClient(_Client):
        async def get(self, url: str) -> _Response:
            code = url.rsplit("_", 1)[1].removesuffix(".html")
            if code == "510330":
                raise RuntimeError("HTTP 404")
            return await super().get(url)

    page = await provider.fetch_eastmoney_tracked_underlying_page(
        ("510300", "510330"),
        client=PartialClient(
            {"510300": _archive_html("510300", "沪深300指数")}
        ),
        clock=lambda: datetime(2026, 9, 10, 8, 0),
    )
    assert [record.etf_code for record in page.underlying_records] == ["510300"]
    assert page.provider_errors == (("510330", "provider_request_failed:RuntimeError"),)


@pytest.mark.asyncio
async def test_eastmoney_adapter_rejects_conflicting_explicit_fields() -> None:
    body = _archive_html("510300", "沪深300指数").replace(
        "</table>",
        "<tr><th>跟踪标的</th><td>中证1000指数</td></tr></table>",
    )
    with pytest.raises(
        provider.EtfTrackedUnderlyingProviderError,
        match="conflicting_tracked_underlying_fields",
    ):
        await provider.fetch_eastmoney_tracked_underlying_page(
            ("510300",),
            client=_Client({"510300": body}),
            clock=lambda: datetime(2026, 9, 10, 8, 0),
        )


@pytest.mark.asyncio
async def test_eastmoney_adapter_preserves_explicit_no_underlying_and_rejects_bad_pages() -> None:
    client = _Client(
        {
            "511990": _archive_html("511990", "该基金无跟踪标的"),
            "510300": _archive_html("510330", "沪深300指数"),
        }
    )

    page = await provider.fetch_eastmoney_tracked_underlying_page(
        ("511990",),
        client=client,
        clock=lambda: datetime(2026, 9, 10, 8, 0),
    )
    record = page.underlying_records[0]
    assert record.identity_state == "unresolved"
    assert record.tracked_underlying_id is None
    assert "provider_explicit_no_underlying" in record.identity_reason

    with pytest.raises(provider.EtfTrackedUnderlyingProviderError, match="provider_code_mismatch"):
        await provider.fetch_eastmoney_tracked_underlying_page(
            ("510300",),
            client=client,
            clock=lambda: datetime(2026, 9, 10, 8, 0),
        )


@pytest.mark.asyncio
async def test_tracked_underlying_job_is_resumable_and_deduplicates_repeated_pages(
    app,
    monkeypatch,
) -> None:
    code = "510300"
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code=code,
                name="沪深300ETF",
                exchange="SH",
                theme_tags_json=[],
                trading_rule_label="证券账户 T+1 ETF",
                asset_class="broad_index",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        session.add(
            EtfUniverseMembership(
                etf_code=code,
                effective_from=datetime(2026, 1, 1).date(),
                effective_to=None,
                source="test-universe",
                tracked_underlying_id=None,
            )
        )
        await session.commit()

    async def fake_fetch(codes: tuple[str, ...]) -> IdentityFactProviderPage:
        return await provider.fetch_eastmoney_tracked_underlying_page(
            codes,
            client=_Client({code: _archive_html(code, "沪深300指数")}),
            clock=lambda: datetime(2026, 9, 10, 8, 0),
        )

    monkeypatch.setattr(jobs, "fetch_eastmoney_tracked_underlying_page", fake_fetch)

    async with app.state.db.session() as session:
        first = await jobs.etf_tracked_underlying_ingestion_job(session)
        second = await jobs.etf_tracked_underlying_ingestion_job(session)
        facts = (
            await session.scalars(
                select(EtfTrackedUnderlyingFact).where(
                    EtfTrackedUnderlyingFact.etf_code == code,
                )
            )
        ).all()
        membership = await session.scalar(
            select(EtfUniverseMembership).where(
                EtfUniverseMembership.etf_code == code,
                EtfUniverseMembership.effective_to.is_(None),
            )
        )

    assert first["status"] == "complete"
    assert first["underlying_facts_inserted"] == 1
    assert second["selected_count"] == 0
    assert second["underlying_facts_inserted"] == 0
    assert len(facts) == 1
    assert membership is not None
    assert membership.tracked_underlying_id == facts[0].tracked_underlying_id


@pytest.mark.asyncio
async def test_tracked_underlying_provider_failure_does_not_advance_cursor(app, monkeypatch) -> None:
    code = "510300"
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code=code,
                name="沪深300ETF",
                exchange="SH",
                theme_tags_json=[],
                trading_rule_label="证券账户 T+1 ETF",
                asset_class="broad_index",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        session.add(
            EtfUniverseMembership(
                etf_code=code,
                effective_from=datetime(2026, 1, 1).date(),
                effective_to=None,
                source="test-universe",
                tracked_underlying_id=None,
            )
        )
        await session.commit()

    async def fail_fetch(_: tuple[str, ...]) -> IdentityFactProviderPage:
        raise provider.EtfTrackedUnderlyingProviderError(code, "provider_request_failed:TimeoutError")

    monkeypatch.setattr(jobs, "fetch_eastmoney_tracked_underlying_page", fail_fetch)

    async with app.state.db.session() as session:
        result = await jobs.etf_tracked_underlying_ingestion_job(session)
        fact_count = await session.scalar(select(func.count()).select_from(EtfTrackedUnderlyingFact))

    assert result["status"] == "failed"
    assert result["stop_reason"] == "provider_fetch_failed"
    assert result["provider_failure_count"] == 1
    assert result["cursor_after"] is None
    assert fact_count == 0


@pytest.mark.asyncio
async def test_mixed_provider_failure_persists_success_and_advances_bounded_cursor(
    app,
    monkeypatch,
) -> None:
    class FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2026, 9, 10)

    monkeypatch.setattr(jobs, "date", FixedDate)
    codes = ("510300", "510330")
    async with app.state.db.session() as session:
        for code in codes:
            session.add(
                TradableEtf(
                    code=code,
                    name=f"测试{code}",
                    exchange="SH",
                    theme_tags_json=[],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="broad_index",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
            )
            session.add(
                EtfUniverseMembership(
                    etf_code=code,
                    effective_from=date(2026, 1, 1),
                    effective_to=None,
                    source="test-universe",
                    tracked_underlying_id=None,
                )
            )
        await session.commit()

    async def partial_fetch(selected: tuple[str, ...]) -> IdentityFactProviderPage:
        return await provider.fetch_eastmoney_tracked_underlying_page(
            selected,
            client=_PartialClient(
                {"510300": _archive_html("510300", "沪深300指数")}
            ),
            clock=lambda: datetime(2026, 9, 10, 8, 0),
        )

    class _PartialClient(_Client):
        async def get(self, url: str) -> _Response:
            code = url.rsplit("_", 1)[1].removesuffix(".html")
            if code == "510330":
                raise RuntimeError("HTTP 404")
            return await super().get(url)

    monkeypatch.setattr(jobs, "fetch_eastmoney_tracked_underlying_page", partial_fetch)

    async with app.state.db.session() as session:
        result = await jobs.etf_tracked_underlying_ingestion_job(session)
        cursor = await session.get(
            EtfSyncCursor,
            jobs.ETF_TRACKED_UNDERLYING_FACT_SCOPE,
        )
        facts = (await session.scalars(select(EtfTrackedUnderlyingFact))).all()

    assert result["status"] == "partial"
    assert result["job_status"] == "partial"
    assert result["underlying_facts_inserted"] == 1
    assert result["provider_failure_count"] == 1
    assert result["provider_failures"] == [
        {"etf_code": "510330", "reason": "provider_request_failed:RuntimeError"}
    ]
    assert cursor is not None
    # The final ordinal page completed the sweep; clear the cursor so the
    # failed code is retried from the head on the next daily sweep.
    assert cursor.last_regular_code is None
    assert cursor.last_lane == "retry:2026-09-10"
    assert [fact.etf_code for fact in facts] == ["510300"]


@pytest.mark.asyncio
async def test_underlying_cursor_continues_across_days_until_full_sweep(app, monkeypatch) -> None:
    codes = tuple(f"5103{index:02d}" for index in range(8))
    async with app.state.db.session() as session:
        for code in codes:
            session.add(
                TradableEtf(
                    code=code,
                    name=f"测试{code}",
                    exchange="SH",
                    theme_tags_json=[],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="broad_index",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
            )
            session.add(
                EtfUniverseMembership(
                    etf_code=code,
                    effective_from=date(2026, 1, 1),
                    effective_to=None,
                    source="test-universe",
                    tracked_underlying_id=None,
                )
            )
        await session.commit()

    class FixedDate(date):
        current = date(2026, 9, 10)

        @classmethod
        def today(cls) -> date:
            return cls.current

    calls: list[tuple[str, ...]] = []

    async def fetch_page(selected: tuple[str, ...]) -> IdentityFactProviderPage:
        calls.append(selected)
        return await provider.fetch_eastmoney_tracked_underlying_page(
            selected,
            client=_Client(
                {code: _archive_html(code, "沪深300指数") for code in selected}
            ),
            clock=lambda: datetime(2026, 9, 10, 8, 0),
        )

    monkeypatch.setattr(jobs, "date", FixedDate)
    monkeypatch.setattr(jobs, "fetch_eastmoney_tracked_underlying_page", fetch_page)

    async with app.state.db.session() as session:
        first = await jobs.etf_tracked_underlying_ingestion_job(session)
        cursor_first = await session.get(EtfSyncCursor, jobs.ETF_TRACKED_UNDERLYING_FACT_SCOPE)

    assert first["status"] == "partial"
    assert cursor_first is not None
    assert cursor_first.last_regular_code == codes[5]
    assert cursor_first.last_lane == jobs.ETF_TRACKED_UNDERLYING_SWEEP_LANE

    FixedDate.current = date(2026, 9, 11)
    async with app.state.db.session() as session:
        second = await jobs.etf_tracked_underlying_ingestion_job(session)
        cursor_second = await session.get(EtfSyncCursor, jobs.ETF_TRACKED_UNDERLYING_FACT_SCOPE)

    assert second["status"] == "complete"
    assert calls == [codes[:6], codes[6:]]
    assert cursor_second is not None
    assert cursor_second.last_regular_code is None
    assert cursor_second.last_lane == "done:2026-09-11"


@pytest.mark.asyncio
async def test_incomplete_provider_failure_keeps_tail_cursor_across_days(app, monkeypatch) -> None:
    codes = tuple(f"5103{index:02d}" for index in range(8))
    async with app.state.db.session() as session:
        for code in codes:
            session.add(
                TradableEtf(
                    code=code,
                    name=f"测试{code}",
                    exchange="SH",
                    theme_tags_json=[],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="broad_index",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
            )
            session.add(
                EtfUniverseMembership(
                    etf_code=code,
                    effective_from=date(2026, 1, 1),
                    effective_to=None,
                    source="test-universe",
                    tracked_underlying_id=None,
                )
            )
        await session.commit()

    class FixedDate(date):
        current = date(2026, 9, 10)

        @classmethod
        def today(cls) -> date:
            return cls.current

    class PartialClient(_Client):
        async def get(self, url: str) -> _Response:
            code = url.rsplit("_", 1)[1].removesuffix(".html")
            if code == codes[1]:
                raise RuntimeError("HTTP 404")
            return await super().get(url)

    calls: list[tuple[str, ...]] = []

    async def fetch_page(selected: tuple[str, ...]) -> IdentityFactProviderPage:
        calls.append(selected)
        return await provider.fetch_eastmoney_tracked_underlying_page(
            selected,
            client=PartialClient(
                {code: _archive_html(code, "沪深300指数") for code in selected}
            ),
            clock=lambda: datetime(2026, 9, 10, 8, 0),
        )

    monkeypatch.setattr(jobs, "date", FixedDate)
    monkeypatch.setattr(jobs, "fetch_eastmoney_tracked_underlying_page", fetch_page)

    async with app.state.db.session() as session:
        first = await jobs.etf_tracked_underlying_ingestion_job(session)
        cursor_first = await session.get(EtfSyncCursor, jobs.ETF_TRACKED_UNDERLYING_FACT_SCOPE)

    assert first["status"] == "partial"
    assert first["provider_failure_count"] == 1
    assert cursor_first is not None
    assert cursor_first.last_regular_code == codes[5]
    assert cursor_first.last_lane == "retry:2026-09-10"

    FixedDate.current = date(2026, 9, 11)
    async with app.state.db.session() as session:
        second = await jobs.etf_tracked_underlying_ingestion_job(session)
        cursor_second = await session.get(EtfSyncCursor, jobs.ETF_TRACKED_UNDERLYING_FACT_SCOPE)

    assert second["status"] == "complete"
    assert calls == [codes[:6], codes[6:]]
    assert cursor_second is not None
    assert cursor_second.last_regular_code is None
    assert cursor_second.last_lane == "done:2026-09-11"


@pytest.mark.asyncio
async def test_completed_sweep_waits_for_next_day_then_rediscovers_new_etf(
    app,
    monkeypatch,
) -> None:
    class FixedDate(date):
        current = date(2026, 9, 10)

        @classmethod
        def today(cls) -> date:
            return cls.current

    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code="510300",
                name="测试510300",
                exchange="SH",
                theme_tags_json=[],
                trading_rule_label="证券账户 T+1 ETF",
                asset_class="broad_index",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        session.add(
            EtfUniverseMembership(
                etf_code="510300",
                effective_from=date(2026, 1, 1),
                effective_to=None,
                source="test-universe",
                tracked_underlying_id=None,
            )
        )
        await session.commit()

    calls: list[tuple[str, ...]] = []

    async def fetch_page(selected: tuple[str, ...]) -> IdentityFactProviderPage:
        calls.append(selected)
        return await provider.fetch_eastmoney_tracked_underlying_page(
            selected,
            client=_Client(
                {code: _archive_html(code, "沪深300指数") for code in selected}
            ),
            clock=lambda: datetime(2026, 9, 10, 8, 0),
        )

    monkeypatch.setattr(jobs, "date", FixedDate)
    monkeypatch.setattr(jobs, "fetch_eastmoney_tracked_underlying_page", fetch_page)

    async with app.state.db.session() as session:
        first = await jobs.etf_tracked_underlying_ingestion_job(session)
    assert first["status"] == "complete"

    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code="510330",
                name="测试510330",
                exchange="SH",
                theme_tags_json=[],
                trading_rule_label="证券账户 T+1 ETF",
                asset_class="broad_index",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        session.add(
            EtfUniverseMembership(
                etf_code="510330",
                effective_from=date(2026, 1, 1),
                effective_to=None,
                source="test-universe",
                tracked_underlying_id=None,
            )
        )
        await session.commit()

    async with app.state.db.session() as session:
        same_day = await jobs.etf_tracked_underlying_ingestion_job(session)
    assert same_day["stop_reason"] == "daily_sweep_complete"
    assert calls == [("510300",)]

    FixedDate.current = date(2026, 9, 11)
    async with app.state.db.session() as session:
        next_day = await jobs.etf_tracked_underlying_ingestion_job(session)
        facts = (await session.scalars(select(EtfTrackedUnderlyingFact))).all()

    assert next_day["status"] == "complete"
    assert calls == [("510300",), ("510300", "510330")]
    assert sorted(fact.etf_code for fact in facts) == ["510300", "510330"]
