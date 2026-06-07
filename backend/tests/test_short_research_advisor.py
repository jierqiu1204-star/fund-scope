from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    ShortResearchAdvisorAttempt,
    ShortResearchAdvisorReport,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    utcnow,
)
from app.services.short_research.advisor import (
    ACTION_CAUTION,
    ACTION_FOCUS,
    ACTION_SKIP,
    run_advisor_generation,
    validate_advisor_payload,
)


def _valid_report(action_label: str = ACTION_FOCUS) -> str:
    return json.dumps(
        {
            "action_label": action_label,
            "plain_summary": "公开数据表现较强，但只适合作为观察对象。",
            "opportunity": ["近 20 日公开数据较强。"],
            "risks": ["短线数据可能很快失效。"],
            "opposing_view": "市场风格可能切换，近期强势不代表后续仍然强。",
            "watch_conditions": ["继续观察近 20 日趋势。"],
            "holding_note": "如果已经持有，继续看公开数据变化。",
            "data_limitations": "仅使用公开日线和净值数据，不连接账户。",
        },
        ensure_ascii=False,
    )


async def _seed_signal_run(app) -> int:
    async with app.state.db.session() as session:
        run = ShortResearchSignalRun(
            status="success",
            started_at=utcnow(),
            finished_at=utcnow(),
            as_of_date=date(2026, 6, 5),
            config_json={},
            summary_json={"item_count": 2},
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        session.add_all(
            [
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="fund",
                    asset_code="270042",
                    rank=1,
                    total_score=76.6,
                    conclusion="谨慎观察",
                    score_breakdown_json={"risk": {"score": 100}},
                    risk_flags_json=[],
                    rationale_json={"opposing_view": "风格可能切换。"},
                    metrics_json={"return_20d": 0.0425, "max_drawdown_60d": -0.0484},
                ),
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="etf",
                    asset_code="515000",
                    rank=2,
                    total_score=84.7,
                    conclusion="高位观察",
                    score_breakdown_json={"risk": {"score": 70}},
                    risk_flags_json=["追高风险"],
                    rationale_json={},
                    metrics_json={"return_20d": 0.1321, "max_drawdown_60d": -0.069},
                ),
            ]
        )
        await session.commit()
        return run.id


def test_validate_advisor_payload_downgrades_and_rejects_unsafe_language() -> None:
    downgraded = validate_advisor_payload(_valid_report(ACTION_FOCUS), rule_action=ACTION_CAUTION)

    assert downgraded["action_label"] == ACTION_CAUTION

    unsafe = json.loads(_valid_report(ACTION_SKIP))
    unsafe["plain_summary"] = "建议买入，目标价很快到。"
    with pytest.raises(ValueError):
        validate_advisor_payload(unsafe, rule_action=ACTION_SKIP)

    with pytest.raises(ValueError):
        validate_advisor_payload("{bad json", rule_action=ACTION_SKIP)


def test_validate_advisor_payload_accepts_single_text_list_fields() -> None:
    payload = json.loads(_valid_report(ACTION_CAUTION))
    payload["opportunity"] = "趋势还可以继续观察"
    payload["risks"] = "波动仍然偏高"
    payload["watch_conditions"] = "先看近 20 日趋势是否保持"

    report = validate_advisor_payload(payload, rule_action=ACTION_CAUTION)

    assert report["opportunity"] == ["趋势还可以继续观察"]
    assert report["risks"] == ["波动仍然偏高"]
    assert report["watch_conditions"] == ["先看近 20 日趋势是否保持"]


def test_validate_advisor_payload_replaces_unknown_action_with_rule_action() -> None:
    payload = json.loads(_valid_report(ACTION_FOCUS))
    payload["action_label"] = "积极关注"

    report = validate_advisor_payload(payload, rule_action=ACTION_CAUTION)

    assert report["action_label"] == ACTION_CAUTION


@pytest.mark.asyncio
async def test_advisor_generation_is_idempotent_and_does_not_change_signal_items(app, settings) -> None:
    run_id = await _seed_signal_run(app)
    calls = 0

    class FakeClient:
        model_name = "fake-model"

        async def generate_short_research_report(self, *_args: Any, **_kwargs: Any) -> str:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("quota exhausted")
            return _valid_report(ACTION_FOCUS)

    enabled_settings = settings.model_copy(
        update={"llm_advisor_enabled": True, "llm_advisor_max_assets": 2}
    )

    async with app.state.db.session() as session:
        result = await run_advisor_generation(session, enabled_settings, llm_client=FakeClient(), max_assets=2)
        items = (
            await session.scalars(
                select(ShortResearchSignalItem).where(ShortResearchSignalItem.run_id == run_id).order_by(ShortResearchSignalItem.rank)
            )
        ).all()
        reports = (
            await session.scalars(
                select(ShortResearchAdvisorReport).where(ShortResearchAdvisorReport.signal_run_id == run_id)
            )
        ).all()
        attempts = (
            await session.scalars(
                select(ShortResearchAdvisorAttempt).where(ShortResearchAdvisorAttempt.signal_run_id == run_id)
            )
        ).all()

    assert result["selected"] == 2
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert [item.rank for item in items] == [1, 2]
    assert [item.conclusion for item in items] == ["谨慎观察", "高位观察"]
    assert len(reports) == 2
    assert {report.asset_code for report in reports} == {"270042", "515000"}
    assert next(report for report in reports if report.asset_code == "270042").action_label == ACTION_CAUTION
    assert next(report for report in reports if report.asset_code == "515000").source == "fallback"
    assert {attempt.status for attempt in attempts} == {"success", "failed"}

    disabled_settings = settings.model_copy(update={"llm_advisor_enabled": False})
    async with app.state.db.session() as session:
        await run_advisor_generation(session, disabled_settings, max_assets=2)
        report_count = await session.scalar(select(func.count(ShortResearchAdvisorReport.id)))

    assert report_count == 2


@pytest.mark.asyncio
async def test_advisor_api_returns_reports_with_latest_short_research_items(client, app) -> None:
    await _seed_signal_run(app)

    advisor = await client.post("/api/short-research/advisor/run")
    assert advisor.status_code == 200
    assert advisor.json()["fallback"] == 2

    admin_run = await client.post("/api/admin/jobs/daily_short_research_advisor/run")
    assert admin_run.status_code == 200
    assert admin_run.json()["selected"] == 2

    latest = await client.get("/api/short-research/signals/latest")
    assert latest.status_code == 200
    body = latest.json()
    first = body["items"][0]
    assert first["code"] == "270042"
    assert first["advisor_report"]["action_label"] == ACTION_CAUTION
    assert first["advisor_report"]["source"] == "fallback"
