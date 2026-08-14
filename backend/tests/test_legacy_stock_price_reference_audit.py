from __future__ import annotations

from pathlib import Path

from app.models.entities import Stock, StockFundamental, StockMetric


def test_legacy_stock_price_table_has_no_runtime_consumer() -> None:
    app_root = Path(__file__).resolve().parents[1] / "app"
    runtime_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(app_root.rglob("*.py"))
    )
    legacy_table = "stock_price_" + "history"
    legacy_model = "StockPrice" + "History"
    assert legacy_table not in runtime_source
    assert legacy_model not in runtime_source


def test_retained_stock_tables_still_have_models_and_consumers() -> None:
    assert Stock.__tablename__ == "stocks"
    assert StockFundamental.__tablename__ == "stock_fundamentals"
    assert StockMetric.__tablename__ == "stock_metrics"

    engine_source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "recommendations"
        / "engine.py"
    ).read_text(encoding="utf-8")
    assert "select(Stock)" in engine_source
    assert "select(StockFundamental)" in engine_source
    assert "select(StockMetric)" in engine_source
