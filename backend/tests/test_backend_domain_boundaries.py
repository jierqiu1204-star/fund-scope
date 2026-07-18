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
            "app.services.strategy_lab",
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


def test_risk_alerts_layer_stays_pure_rule_logic() -> None:
    _assert_no_forbidden_imports(
        ROOT / "risk_alerts.py",
        (
            "app.api",
            "app.services.notifier",
            "app.services.tracked_positions",
            "app.services.short_research",
        ),
    )


def test_workflows_are_allowed_to_orchestrate_multiple_domains() -> None:
    workflow_text = "\n".join(_text(path) for path in (ROOT / "workflows").glob("*.py"))

    assert "app.services.short_research" in workflow_text
    assert "app.services.tracked_positions" in workflow_text


def test_etf_strategy_lab_ranking_modules_cannot_mutate_daily_decision_domains() -> None:
    forbidden = (
        "app.services.portfolio_allocation",
        "app.services.tracked_positions.service",
        "app.services.risk_alerts",
        "app.services.notifier",
        "app.services.workflows",
    )
    for path in (ROOT / "strategy_lab").glob("etf_*.py"):
        _assert_no_forbidden_imports(path, forbidden)


def test_notification_remains_downstream_of_research_and_strategy_lab() -> None:
    _assert_no_forbidden_imports(
        ROOT / "notifier.py",
        (
            "app.services.market_data",
            "app.services.short_research",
            "app.services.strategy_lab",
            "app.services.tracked_positions",
        ),
    )


def test_etf_research_evidence_contract_has_no_database_or_business_service_dependency() -> None:
    _assert_no_forbidden_imports(
        ROOT / "etf_research_evidence.py",
        (
            "sqlalchemy",
            "app.models",
            "app.services.market_data",
            "app.services.short_research",
            "app.services.strategy_lab",
            "app.services.tracked_positions",
            "app.services.risk_alerts",
            "app.services.notifier",
        ),
    )
