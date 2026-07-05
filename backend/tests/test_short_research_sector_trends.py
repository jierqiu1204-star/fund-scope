from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.short_research.sector_trends import build_sector_trend_payloads
from app.services.short_research.theme_taxonomy import classify_etf_theme


def _asset(
    code: str,
    *,
    primary_theme: str = "机器人",
    theme_group: str = "technology",
    return_5d: float = 0.06,
    return_20d: float = 0.12,
    technical_score: float = 76,
    distance_to_ma5_pct: float = 0.02,
    distance_to_ma20_pct: float = 0.08,
    turnover_20d: float = 180_000_000,
    turnover_60d: float = 120_000_000,
    default_display_eligible: bool = True,
    risk_flags: list[str] | None = None,
) -> SimpleNamespace:
    metrics: dict[str, Any] = {
        "return_5d": return_5d,
        "return_20d": return_20d,
        "technical_score": technical_score,
        "distance_to_ma5_pct": distance_to_ma5_pct,
        "distance_to_ma20_pct": distance_to_ma20_pct,
        "average_turnover_20d": turnover_20d,
        "average_turnover_60d": turnover_60d,
        "default_display_eligible": default_display_eligible,
        "theme_profile": {
            "primary_theme": primary_theme,
            "theme_group": theme_group,
            "asset_bucket": "equity",
        },
    }
    return SimpleNamespace(
        metadata=SimpleNamespace(code=code, name=f"{primary_theme}ETF{code}", theme_tags=[primary_theme]),
        metrics=metrics,
        risk_flags=risk_flags or [],
        total_score=technical_score,
    )


def test_sector_trend_scores_broad_peer_participation() -> None:
    payloads = build_sector_trend_payloads(
        [
            _asset("159001", return_5d=0.08, return_20d=0.16, technical_score=82),
            _asset("159002", return_5d=0.05, return_20d=0.11, technical_score=78),
            _asset("159003", return_5d=0.04, return_20d=0.09, technical_score=75),
        ]
    )

    payload = payloads["159001"]
    assert payload["metrics"]["sector_trend_status"] == "success"
    assert payload["metrics"]["sector_peer_count"] == 3
    assert payload["metrics"]["sector_trend_score"] >= 75
    assert payload["metrics"]["sector_trend_label"] in {"板块强势", "板块升温"}
    assert "机器人" in payload["metrics"]["sector_trend_summary"]
    assert payload["breakdown"]["score_version"] == "sector_trend_v1"


def test_sector_trend_limits_score_when_breadth_is_weak() -> None:
    strong = _asset("159010", return_5d=0.12, return_20d=0.22, technical_score=88)
    weak_1 = _asset("159011", return_5d=-0.01, return_20d=0.01, technical_score=62, distance_to_ma5_pct=-0.02)
    weak_2 = _asset("159012", return_5d=-0.02, return_20d=-0.03, technical_score=58, distance_to_ma20_pct=-0.04)

    payloads = build_sector_trend_payloads([strong, weak_1, weak_2])

    assert payloads["159010"]["metrics"]["sector_trend_score"] < 75
    assert "上涨家数" in payloads["159010"]["metrics"]["sector_trend_summary"]


def test_sector_trend_returns_unavailable_for_insufficient_or_unclassified_peers() -> None:
    single = build_sector_trend_payloads([_asset("159020")])
    assert single["159020"]["metrics"]["sector_trend_score"] is None
    assert single["159020"]["metrics"]["sector_trend_status"] == "unavailable"

    unclassified = _asset("159021", primary_theme="未分类", theme_group="unknown")
    payloads = build_sector_trend_payloads([unclassified, _asset("159022", primary_theme="未分类", theme_group="unknown")])
    assert payloads["159021"]["metrics"]["sector_trend_score"] is None
    assert "未分类" in payloads["159021"]["metrics"]["sector_trend_reason"]


def test_sector_trend_excludes_stale_or_low_quality_peers() -> None:
    payloads = build_sector_trend_payloads(
        [
            _asset("159030"),
            _asset("159031", risk_flags=["数据滞后"]),
            _asset("159032", default_display_eligible=False),
        ]
    )

    assert payloads["159030"]["metrics"]["sector_trend_score"] is None
    assert "有效样本不足" in payloads["159030"]["metrics"]["sector_trend_reason"]


@pytest.mark.parametrize(
    ("name", "tags", "expected"),
    [
        ("机器人ETF华夏", ["高端制造"], "机器人"),
        ("港股创新药ETF", ["医药"], "创新药"),
        ("生物药ETF", ["医药"], "创新药"),
    ],
)
def test_robotics_and_innovative_drug_theme_classification(name: str, tags: list[str], expected: str) -> None:
    profile = classify_etf_theme(code="", name=name, asset_class="sector", theme_tags=tags)

    assert profile.primary_theme == expected
    assert expected in {profile.primary_theme, *profile.secondary_themes}
