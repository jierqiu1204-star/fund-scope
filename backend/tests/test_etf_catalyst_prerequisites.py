from app.services.etf_catalyst_shadow.policy import (
    APPROVED_CATALYST_SOURCES,
    CATALYST_RANKING_WEIGHT,
    MAX_FETCH_ITEMS,
    MAX_OPERATION_SECONDS,
    catalyst_source_policy_hash,
    catalyst_source_policy_payload,
)
from app.services.short_research.daily_reconstructable import (
    daily_reconstructable_manifest,
)
from app.services.short_research.ranking_contract import final_score_v3_manifest
from app.services.short_research.ranking_surfaces import actionable_rank_manifest


def test_research_and_actionable_contracts_forbid_catalyst_inputs() -> None:
    research = daily_reconstructable_manifest().canonical_payload()
    actionable = actionable_rank_manifest().canonical_payload()
    final_score = final_score_v3_manifest()

    assert {"theme", "catalyst"}.issubset(research["forbidden_input_domains"])
    assert {"theme", "catalyst"}.issubset(actionable["forbidden_input_domains"])
    catalyst_component = final_score.components["theme_catalyst"]
    assert catalyst_component.score_bearing is False
    assert catalyst_component.weight == 0
    assert CATALYST_RANKING_WEIGHT == 0


def test_approved_source_policy_is_official_bounded_and_frozen() -> None:
    payload = catalyst_source_policy_payload()

    assert len(APPROVED_CATALYST_SOURCES) == 6
    assert {source.source_class for source in APPROVED_CATALYST_SOURCES} == {
        "official"
    }
    assert payload["timezone"] == "Asia/Shanghai"
    assert payload["fetch_policy"]["worker_count"] == 1
    assert payload["fetch_policy"]["maximum_items_per_source"] == MAX_FETCH_ITEMS == 20
    assert (
        payload["fetch_policy"]["operation_timeout_seconds"]
        == MAX_OPERATION_SECONDS
        == 50
    )
    assert payload["fetch_policy"]["retry_within_session"] is False
    assert payload["raw_content_retention"]["unreferenced_days"] == 180
    assert payload["ranking_policy"]["catalyst_weight"] == 0
    assert len(catalyst_source_policy_hash()) == 64
