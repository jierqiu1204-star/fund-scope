from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]
PANEL = ROOT / "frontend/components/leader-tactics-v2-panel.tsx"
PAGE = ROOT / "frontend/app/short-term/leader-tactics/page.tsx"


def test_leader_tactics_panel_keeps_universes_filters_and_pagination_contract() -> None:
    source = PANEL.read_text()
    for token in (
        'type Universe = "etf" | "ashare"',
        'useState<Universe>("etf")',
        "formula",
        "state",
        "as_of",
        'limit: "50"',
        "next_cursor",
        "has_more",
        "universe",
        "research_only: true",
    ):
        assert token in source


def test_leader_tactics_panel_has_no_fallback_or_live_action_path() -> None:
    source = PANEL.read_text()
    assert "/api/short-research/leader-tactics-v2/candidates" in source
    assert "Research only" in source
    assert "不会改变正式综合排名、持仓、邮件或执行" in source
    assert "Notification" not in source
    assert "smtp" not in source.lower()
    assert "AbortController" not in source
    assert "api.get" in source and "signal" in source
    assert "回填" in source
    assert "<LeaderTacticsV2Panel />" in PAGE.read_text()
