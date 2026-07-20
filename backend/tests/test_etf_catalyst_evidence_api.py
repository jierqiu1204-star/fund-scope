from datetime import datetime, time, timedelta
from pathlib import Path

from app.models.entities import TradableEtf
from app.services.etf_catalyst_shadow.events import (
    EventCandidate,
    normalize_event_candidate,
)
from app.services.etf_catalyst_shadow.evidence import catalyst_shadow_contexts
from app.services.etf_catalyst_shadow.ingestion import (
    ReceiptInput,
    ingest_receipt,
    register_approved_sources,
)
from app.services.etf_catalyst_shadow.snapshots import build_shadow_snapshot
from app.services.short_research.dual_snapshot_materialization import (
    materialize_dual_ranking_snapshot,
    publish_dual_ranking_snapshot,
)
from app.services.short_research.snapshot_selector import (
    required_etf_snapshot_trade_date,
)
from tests.test_etf_dual_snapshot_materialization import _asset, _install


async def _seed_robot_shadow(app) -> None:
    trade_date = required_etf_snapshot_trade_date()
    published = datetime.combine(trade_date, time(9, 0))
    received = datetime.combine(trade_date, time(9, 5))
    cutoff = datetime.combine(trade_date, time(15, 0))
    async with app.state.db.session() as session:
        await register_approved_sources(session)
        receipt = await ingest_receipt(
            session,
            ReceiptInput(
                source_id="miit_policy",
                fetch_state="item",
                fetched_at=received + timedelta(minutes=1),
                first_received_at=received,
                external_id="robot-policy-1",
                canonical_url="https://www.miit.gov.cn/policy/robot.html",
                source_published_at=published,
                raw_content="<html>机器人产业政策</html>",
                raw_content_ref="raw://robot-policy-1",
                metadata={
                    "supported_entities": ["工业和信息化部"],
                    "supported_theme_ids": ["机器人"],
                    "supported_direction": "positive",
                },
            ),
        )
        await normalize_event_candidate(
            session,
            EventCandidate(
                event_id="robot-policy-event",
                event_type="industry_policy",
                entities=("工业和信息化部",),
                title="机器人产业政策",
                summary="工信部发布机器人产业政策。",
                receipt_ids=(receipt.receipt_id,),
                source_published_at=published,
                effective_start=published,
                effective_end=published + timedelta(days=30),
                direction="positive",
                direct_theme_ids=("机器人",),
            ),
            extraction_method="deterministic",
            verify=True,
        )
        await build_shadow_snapshot(
            session,
            theme_id="机器人",
            session_date=trade_date,
            cutoff_at=cutoff,
        )


async def test_evidence_contract_serializes_full_shadow_provenance(app) -> None:
    await _seed_robot_shadow(app)
    trade_date = required_etf_snapshot_trade_date()
    async with app.state.db.session() as session:
        contexts = await catalyst_shadow_contexts(
            session,
            theme_ids=("机器人",),
            as_of_date=trade_date,
        )

    evidence = contexts["机器人"]
    assert evidence["source_registry"]
    assert evidence["receipts"][0]["canonical_url"].endswith("/policy/robot.html")
    assert evidence["events"][0]["verification_state"] == "verified"
    assert evidence["mappings"][0]["mapping_kind"] == "direct"
    assert evidence["snapshot"]["coverage_state"] == "active"
    assert len(evidence["snapshot"]["snapshot_hash"]) == 64
    assert len(evidence["evidence_hash"]) == 64
    assert evidence["production_isolation"] == {
        "research_only": True,
        "ranking_weight": 0.0,
        "may_change_score": False,
        "may_change_rank": False,
        "may_change_allocation": False,
        "may_change_alert": False,
        "may_change_notification": False,
    }


async def test_short_term_list_and_detail_read_cached_shadow_only(
    client,
    app,
    monkeypatch,
) -> None:
    trade_date = required_etf_snapshot_trade_date()
    _install(
        monkeypatch,
        [_asset("159001", research_score=80.0, actionable_score=75.0)],
    )
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code="159001",
                name="机器人ETF",
                exchange="SZ",
                theme_tags_json=["机器人"],
                trading_rule_label="证券账户 T+1 ETF",
                asset_class="industry_theme",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        run = await materialize_dual_ranking_snapshot(
            session,
            trade_date=trade_date,
            decision_cutoff=datetime.combine(trade_date, time(15, 0)),
        )
        await session.commit()
        await publish_dual_ranking_snapshot(session, run_id=run.id)
    await _seed_robot_shadow(app)

    list_response = await client.get(
        "/api/short-research/assets?asset_type=etf&sort=opportunity&universe=all"
    )
    detail_response = await client.get("/api/short-research/assets/etf/159001")

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    list_assets = {
        item["code"]: item for item in list_response.json()["items"]
    }
    assert "159001" in list_assets
    list_asset = list_assets["159001"]
    detail_asset = detail_response.json()["asset"]
    for asset in (list_asset, detail_asset):
        shadow = asset["catalyst_shadow"]
        assert shadow["coverage_state"] == "active"
        assert shadow["shadow_only"] is True
        assert shadow["ranking_weight"] == 0
        assert shadow["changes_research_rank"] is False
        assert shadow["changes_actionable_rank"] is False
        assert shadow["themes"][0]["events"][0]["mapping_kind"] == "direct"
        assert asset["catalyst_score"] is None
        assert asset["sentiment_heat_score"] is None



def test_cached_evidence_reader_has_no_network_or_ai_dependency() -> None:
    evidence_source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "etf_catalyst_shadow"
        / "evidence.py"
    ).read_text(encoding="utf-8")
    assert "httpx" not in evidence_source
    assert "normalize_ai_extraction" not in evidence_source
