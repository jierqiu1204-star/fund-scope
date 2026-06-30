from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app" / "services"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _assert_no_forbidden_imports(path: Path, forbidden: tuple[str, ...]) -> None:
    text = _text(path)
    violations = [pattern for pattern in forbidden if pattern in text]
    assert not violations, f"{path.relative_to(ROOT)} has forbidden imports: {violations}"


def test_intraday_etf_layer_does_not_import_research_tracking_or_notification() -> None:
    forbidden = (
        "app.services.short_research",
        "app.services.tracked_positions",
        "app.services.notifier",
    )
    for path in (ROOT / "intraday_etf").glob("*.py"):
        _assert_no_forbidden_imports(path, forbidden)


def test_notification_layer_does_not_import_domain_decision_services() -> None:
    _assert_no_forbidden_imports(
        ROOT / "notifier.py",
        (
            "app.services.intraday_etf",
            "app.services.short_research",
            "app.services.tracked_positions",
        ),
    )


def test_market_data_and_portfolio_allocation_stay_upstream() -> None:
    _assert_no_forbidden_imports(
        ROOT / "market_data.py",
        (
            "app.services.short_research",
            "app.services.tracked_positions",
            "app.services.notifier",
        ),
    )
    _assert_no_forbidden_imports(
        ROOT / "portfolio_allocation.py",
        (
            "app.services.tracked_positions",
            "app.services.notifier",
        ),
    )


def test_short_research_layer_does_not_import_tracking_or_notification() -> None:
    forbidden = (
        "TrackedPosition",
        "app.services.tracked_positions",
        "app.services.notifier",
    )
    for path in (ROOT / "short_research").glob("*.py"):
        _assert_no_forbidden_imports(path, forbidden)


def test_workflows_are_allowed_to_orchestrate_multiple_domains() -> None:
    workflow_text = "\n".join(_text(path) for path in (ROOT / "workflows").glob("*.py"))

    assert "app.services.short_research" in workflow_text
    assert "app.services.tracked_positions" in workflow_text
