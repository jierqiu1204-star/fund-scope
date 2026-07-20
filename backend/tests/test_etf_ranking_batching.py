from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.defaults.short_research import ASSET_TYPE_ETF, ShortResearchAsset
from app.services.short_research import service
from app.services.short_research.service import ComputedAsset, compute_etf_snapshot_assets


def _asset(code: str) -> ComputedAsset:
    return ComputedAsset(
        metadata=ShortResearchAsset(
            asset_type=ASSET_TYPE_ETF,
            code=code,
            name=f"ETF{code}",
            category="broad",
            theme_tags=("测试",),
            investment_direction="fixture",
            trading_rule_label="T+1",
            exchange="SH",
        ),
        rank=None,
        total_score=50.0,
        conclusion="观察",
        latest_date=date(2026, 7, 17),
        latest_value=1.0,
        usable_days=61,
        sample_level="短历史",
        metrics={"research_score_eligible": False},
        score_breakdown={},
        risk_flags=[],
        rationale={},
        source_note="fixture",
        entry_timing_label="观察",
        entry_timing_reason="fixture",
    )


def _install_batch_fakes(
    monkeypatch: pytest.MonkeyPatch,
    codes: list[str],
) -> list[list[str]]:
    history_calls: list[list[str]] = []

    async def fake_available(_session, **_kwargs):
        return [SimpleNamespace(code=code) for code in reversed(codes)]

    async def fake_history(_session, *, codes, **_kwargs):
        history_calls.append(list(codes))
        return (
            {code: [] for code in codes},
            {code: 61 for code in codes},
            {code: code[0] * 64 for code in codes},
            {},
            {code: "fixture_unavailable" for code in codes},
        )

    async def fake_health(_session, *, codes):
        return {code: None for code in codes}

    async def fake_compute(_session, metadata, **_kwargs):
        return _asset(metadata.code)

    async def passthrough_async(_session, assets, _as_of_date, **_kwargs):
        return assets

    monkeypatch.setattr(service, "_available_assets", fake_available)
    monkeypatch.setattr(service, "_prefetch_etf_snapshot_series", fake_history)
    monkeypatch.setattr(service, "_prefetch_etf_data_health", fake_health)
    monkeypatch.setattr(service, "compute_asset", fake_compute)
    monkeypatch.setattr(service, "_with_final_score_v2", lambda assets: assets)
    monkeypatch.setattr(service, "_with_sector_trend_scores", lambda assets: assets)
    monkeypatch.setattr(service, "_with_opportunity_scores", passthrough_async)
    monkeypatch.setattr(service, "_with_final_score_v3_shadow", passthrough_async)
    return history_calls


@pytest.mark.asyncio
async def test_full_universe_history_loading_uses_at_most_20_etfs_per_query(
    monkeypatch,
) -> None:
    codes = [f"51{index:04d}" for index in range(1405)]
    history_calls = _install_batch_fakes(monkeypatch, codes)
    audit: list[dict[str, object]] = []

    assets = await compute_etf_snapshot_assets(
        object(),  # type: ignore[arg-type]
        codes=codes,
        as_of_date=date(2026, 7, 17),
        decision_cutoff=datetime(2026, 7, 17, 15, 0),
        batch_audit=audit,
    )

    assert [len(batch) for batch in history_calls] == [20] * 70 + [5]
    assert all(len(batch) <= 20 for batch in history_calls)
    assert [asset.metadata.code for asset in assets] == sorted(codes)
    assert [item["remaining"] for item in audit][-3:] == [25, 5, 0]
    assert max(int(item["peak_history_row_bound"]) for item in audit) == 20 * 180


@pytest.mark.asyncio
async def test_ranking_batch_cursor_requires_and_reuses_persisted_assets(
    monkeypatch,
) -> None:
    codes = [f"51{index:04d}" for index in range(45)]
    history_calls = _install_batch_fakes(monkeypatch, codes)
    resumed = {code: _asset(code) for code in sorted(codes)[:20]}
    progress: list[dict[str, object]] = []

    async def record(item):
        progress.append(item)

    assets = await compute_etf_snapshot_assets(
        object(),  # type: ignore[arg-type]
        codes=codes,
        as_of_date=date(2026, 7, 17),
        decision_cutoff=datetime(2026, 7, 17, 15, 0),
        resume_after_code=sorted(codes)[19],
        resumed_assets_by_code=resumed,
        batch_progress_callback=record,
    )

    assert [len(batch) for batch in history_calls] == [20, 5]
    assert len(assets) == 45
    assert [item["cursor"] for item in progress] == [sorted(codes)[39], sorted(codes)[44]]

    with pytest.raises(ValueError, match="no persisted assets"):
        await compute_etf_snapshot_assets(
            object(),  # type: ignore[arg-type]
            codes=codes,
            as_of_date=date(2026, 7, 17),
            decision_cutoff=datetime(2026, 7, 17, 15, 0),
            resume_after_code=sorted(codes)[19],
        )
